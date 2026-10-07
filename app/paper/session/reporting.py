"""Work Package 2A — safe, deterministic session reporting (READ-ONLY, hardened).

A session report is built ONLY from a verified evidence journal. Every event and payload is
shape-checked (dict/list where required) and every timestamp and economic value is validated
(finite, correct type, non-negative where required) BEFORE any comparison or arithmetic, so no
journal content can raise an unhandled error or leak a bogus number. Any malformed/inconsistent
journal fails closed: verdict REPORT_INVALIDATED (or SESSION_INVALIDATED for a pre-journal
invalidated run) and EVERY PnL/cost field = None.

Layered, fail-closed checks (first failure wins):
    module LIVE_EXECUTION_ENABLED is False (else VIOLATION, no PnL)
    → pre-journal INVALIDATED run has no journal (SESSION_INVALIDATED; a live=True run is a VIOLATION)
    → manifest reproduces its own hash (detect_manifest_drift)
    → whole hash chain verifies against the manifest hash (verify_journal_file)
    → every event and payload is well-shaped (dict/list); ids unique; timestamps finite ints,
      non-decreasing; exactly one session_created (first) + one session_final (last)
    → session_created binds session_id/manifest/signal-policy; paper_trading_only True,
      live_execution_enabled False (else VIOLATION), shorting_allowed False
    → session_final.manifest matches; lifecycle_status in a TERMINAL allow-list; lifecycle is
      consistent with the evidence (risk/data/signal stop events, active breach)
    → all economic fields (session_final AND every order/snapshot/signal payload) finite/typed;
      individual costs non-negative; counts non-negative ints
    → reconciliation: net_pnl == final_equity − initial_capital; final_equity == cash+pos·mark;
      broker.equity == final_equity  (Net PnL from the journal, never re-deducted)
    → counts recount from events; summed order fees/spread/slippage == broker snapshot
    → strict 1:1 signal↔order linkage: unique earliest per signal, one normal order per bar, each
      normal order matches exactly one earlier signal (exec==earliest, exec>decision), each signal
      used at most once; safe-exit is a STRUCTURALLY-validated separate order type (RISK_STOPPED,
      after a risk_event, exactly one attempt, order immediately precedes the safe_exit event with
      matching open_time+status, and no order follows)

Determinism: pure function of (manifest, journal). Canonical (sorted-key) JSON, fixed
Markdown formatting, every journal-derived string is HTML/markdown/control-char escaped, no wall
clock / rng ⇒ session_report.json / .md / report_hash / completion marker reproduce byte-for-byte.

Output is published as ONE atomic set: both files are staged and fsynced, swapped into place, then
a completion marker (session_report.complete) is written LAST carrying session_id, manifest_hash,
report_hash AND the sha256 of the exact JSON and Markdown bytes. `load_report` accepts a set ONLY
if the marker is present and, recomputed from disk, the report_hash reproduces from the canonical
JSON body and the on-disk JSON/Markdown byte hashes match the marker — a partial or tampered set is
never accepted. This is exact-byte, self-consistent verification (tamper-EVIDENT unless every
artifact is rewritten together) — NOT authentication of authorship. Output is bound to
(session_id, manifest_hash, report_hash): a different session is never overwritten, a same-session
content change is refused, an identical rebuild is idempotent, and an incomplete/tampered leftover
set is NEVER silently repaired — recovery is explicit (recover=True) and journaled. Journal content
is only parsed as data — never eval'd/exec'd.
"""
from __future__ import annotations

import hashlib
import json as _json
import math
import os
from typing import Dict, List, Optional, Tuple

from app.forward.manifest import canonical_json, sha256_text
from app.paper.session import LIVE_EXECUTION_ENABLED
from app.paper.session.journal import verify_journal_file, read_verified_events, JournalError
from app.paper.session.lifecycle import (
    COMPLETED,
    DATA_STOPPED,
    INVALIDATED,
    RISK_STOPPED,
    SIGNAL_STOPPED,
)
from app.paper.session.manifest import detect_manifest_drift

REPORT_SCHEMA_VERSION = "report-v1"

JSON_NAME = "session_report.json"
MD_NAME = "session_report.md"
COMPLETE_MARKER = "session_report.complete"
STAGING_DIR = ".report_staging"

_RECON_EPS = 1e-6
# The "flat/dust" tolerance is NOT a universal constant. It is derived per-session from the FROZEN,
# hash-bound execution profile's quantity step (manifest.execution_profile.qty_step): a flatten-to-0
# leaves at most one qty_step of rounding residual, so a residual strictly within one qty_step is
# dust. Stage 1's own `flat` boolean uses a 1e-9 threshold and so reports flat=False for such a
# residual (e.g. H-025 leaves ~9.99e-7 with qty_step 1e-6); the reporter proves resolution by the
# residual POSITION being within the derived qty_step tolerance — reconciled three ways — and binds
# the exact tolerance + source into the report. Genuine open positions are far larger and rejected.


def _dust_tolerance(manifest: Dict) -> Tuple[bool, Optional[float], str]:
    prof = manifest.get("execution_profile")
    if not isinstance(prof, dict):
        return False, None, "manifest.execution_profile missing"
    qs = prof.get("qty_step")
    if not _is_finite_number(qs, allow_negative=False) or float(qs) <= 0:
        return False, None, "manifest.execution_profile.qty_step is not a finite positive number"
    return True, float(qs), ""
# the exact Stage 1 note strings for an order_status="none" safe-exit (Stage 1 has no reason_code
# field; these are validated by EXACT match, never substring). "already flat" is a resolved exit;
# "no next bar to execute safe-exit" leaves the position OPEN and is treated as unresolved.
_SAFE_EXIT_NONE_RESOLVED_NOTE = "already flat"
_SAFE_EXIT_NONE_UNRESOLVED_NOTE = "no next bar to execute safe-exit"
_SAFE_EXIT_NONE_NOTES = (_SAFE_EXIT_NONE_RESOLVED_NOTE, _SAFE_EXIT_NONE_UNRESOLVED_NOTE)
TERMINAL_ALLOW = (COMPLETED, RISK_STOPPED, DATA_STOPPED, SIGNAL_STOPPED)
_ORDER_STATUS_ALLOW = ("filled", "partial", "rejected", "no_trade", "hold")
_SAFE_EXIT_STATUS_ALLOW = _ORDER_STATUS_ALLOW + ("none",)
# the complete set of event types the Stage 1 engine emits; an unknown type fails closed
_EVENT_TYPE_ALLOW = (
    "session_created", "bar_ingested", "signal", "signal_clamped", "signal_rejected",
    "order", "portfolio_snapshot", "risk_event", "data_gap", "bar_rejected",
    "execution_error", "safe_exit", "session_final",
)


class ReportError(Exception):
    """Raised when reporting must refuse (bad inputs / output-session mismatch)."""


class _InjectedFault(RuntimeError):
    """Test-only: simulated crash during atomic publication."""


# verdicts
V_COMPLETED = "SESSION_COMPLETED"
V_RISK_STOPPED = "RISK_STOPPED"
V_DATA_STOPPED = "DATA_STOPPED"
V_SIGNAL_STOPPED = "SIGNAL_STOPPED"
V_SESSION_INVALIDATED = "SESSION_INVALIDATED"
V_REPORT_INVALIDATED = "REPORT_INVALIDATED"

_LIFECYCLE_VERDICT = {
    COMPLETED: V_COMPLETED, RISK_STOPPED: V_RISK_STOPPED,
    DATA_STOPPED: V_DATA_STOPPED, SIGNAL_STOPPED: V_SIGNAL_STOPPED,
}

_MANDATORY_NUMERIC = ("initial_capital", "final_equity", "gross_pnl", "net_pnl", "fees",
                      "spread_cost", "slippage_cost", "funding_cost", "max_drawdown")

_LIMITATIONS = (
    "Integrity is exact-byte, self-consistent hash verification: the journal hash-chain and the "
    "report set (JSON, Markdown, completion marker) are self-verifying and tamper-evident UNLESS "
    "all artifacts are rewritten together. This is NOT authentication: the completion marker does "
    "not prove WHO wrote the report; authenticity would require a future external signature/anchor.",
    "funding_cost is 0.0: the Stage 1 broker accrues no funding; the report presents only "
    "broker/journal data and never invents a cost.",
    "Gross PnL is presentation only (Net PnL + frictions added back); Net PnL from the verified "
    "journal is the single authoritative identity.",
    "Stage 2B (reference-vs-observed shadow-forward comparison) is not implemented here.",
)


# --------------------------------------------------------------------------- #
# strict type / finiteness helpers                                            #
# --------------------------------------------------------------------------- #
def _is_finite_number(x, *, allow_negative: bool = True) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    xf = float(x)
    if not math.isfinite(xf):
        return False
    return not (not allow_negative and xf < 0)


def _is_int(x, *, nonneg: bool = False) -> bool:
    if isinstance(x, bool) or not isinstance(x, int):
        return False
    return not (nonneg and x < 0)


def report_paths(output_dir: str) -> Tuple[str, str]:
    return os.path.join(output_dir, JSON_NAME), os.path.join(output_dir, MD_NAME)


# --------------------------------------------------------------------------- #
# report skeleton                                                             #
# --------------------------------------------------------------------------- #
def _base_report(manifest: Dict, *, session_id: Optional[str]) -> Dict:
    strat = manifest.get("strategy", {}) if isinstance(manifest, dict) else {}
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "session_id": session_id,
        "strategy_id": strat.get("strategy_id"),
        "strategy_hash": strat.get("strategy_hash"),
        "manifest_hash": manifest.get("manifest_hash") if isinstance(manifest, dict) else None,
        "signal_policy_hash": manifest.get("signal_policy_hash") if isinstance(manifest, dict) else None,
        "asset": manifest.get("asset") if isinstance(manifest, dict) else None,
        "timeframe": manifest.get("timeframe") if isinstance(manifest, dict) else None,
        "lifecycle_status": None, "verdict": None, "exact_reason_code": None,
        "initial_capital": None, "final_equity": None, "gross_pnl": None, "net_pnl": None,
        "fees": None, "spread_cost": None, "slippage_cost": None, "funding_cost": None,
        "max_drawdown": None, "counts": None, "safe_exit": None,
        "why_it_failed": [], "timing": {}, "integrity": {},
        "limitations": list(_LIMITATIONS), "final_event_hash": None,
        "journal_verified": False, "live_execution_enabled": False,
    }


def _finalize(rep: Dict) -> Dict:
    body = {k: v for k, v in rep.items() if k != "report_hash"}
    rep["report_hash"] = sha256_text(canonical_json(body))
    return rep


def _invalidate(rep: Dict, verdict: str, reason_code: str, detail: str) -> Dict:
    rep["verdict"] = verdict
    rep["exact_reason_code"] = reason_code
    rep["journal_verified"] = False
    for k in _MANDATORY_NUMERIC:
        rep[k] = None
    rep["counts"] = None
    rep["why_it_failed"] = []
    rep["integrity"] = {"ok": False, "reason_code": reason_code, "detail": _md_safe(detail)}
    return _finalize(rep)


# --------------------------------------------------------------------------- #
# public entry point                                                          #
# --------------------------------------------------------------------------- #
def build_session_report(*, manifest: Dict, journal_path: Optional[str], output_dir: str,
                         run_result: Optional[Dict] = None, recover: bool = False,
                         _fault: Optional[str] = None) -> Dict:
    if not isinstance(manifest, dict) or not manifest.get("manifest_hash"):
        raise ReportError("manifest must be a dict carrying a manifest_hash")
    if not isinstance(output_dir, str) or not output_dir:
        raise ReportError("output_dir must be a non-empty string")

    session_id = manifest.get("session_id")
    rep = _base_report(manifest, session_id=session_id)
    rep["live_execution_enabled"] = bool(LIVE_EXECUTION_ENABLED)

    if LIVE_EXECUTION_ENABLED is not False:
        rep["live_execution_enabled"] = True
        return _write(_invalidate(rep, V_REPORT_INVALIDATED, "live_execution_enabled",
                                  "LIVE_EXECUTION_ENABLED is not False"), output_dir, _fault=_fault, recover=recover)

    journal_exists = bool(journal_path) and os.path.exists(journal_path)
    if not journal_exists:
        if run_result and run_result.get("live_execution_enabled") is True:
            rep["live_execution_enabled"] = True
            return _write(_invalidate(rep, V_REPORT_INVALIDATED, "live_execution_enabled",
                                      "run_result reports live_execution_enabled=True"),
                          output_dir, _fault=_fault, recover=recover)
        if run_result and run_result.get("lifecycle_status") == INVALIDATED:
            return _write(_session_invalidated(rep, run_result), output_dir, _fault=_fault, recover=recover)
        return _write(_invalidate(rep, V_REPORT_INVALIDATED, "journal_integrity_failure",
                                  "journal file is missing"), output_dir, _fault=_fault, recover=recover)

    drifted, why = detect_manifest_drift(manifest)
    if drifted:
        return _write(_invalidate(rep, V_REPORT_INVALIDATED, "manifest_drift", why),
                      output_dir, _fault=_fault, recover=recover)

    ok, reason, bad = verify_journal_file(journal_path, manifest["manifest_hash"])
    if not ok:
        return _write(_invalidate(rep, V_REPORT_INVALIDATED, "journal_integrity_failure",
                                  f"event #{bad}: {reason}"), output_dir, _fault=_fault, recover=recover)
    try:
        events = read_verified_events(journal_path, manifest["manifest_hash"])
    except JournalError as exc:
        return _write(_invalidate(rep, V_REPORT_INVALIDATED, "journal_integrity_failure", str(exc)),
                      output_dir, _fault=_fault, recover=recover)

    # ALL journal-shape/content errors must fail closed, never crash. Programming/system errors
    # (and the test-only _InjectedFault raised later by _write) are NOT swallowed here.
    try:
        vok, code, detail = _validate_semantics(events, manifest, session_id)
        if not vok:
            if code == "live_execution_enabled":
                rep["live_execution_enabled"] = True
            return _write(_invalidate(rep, V_REPORT_INVALIDATED, code, detail),
                          output_dir, _fault=_fault, recover=recover)
        _fill_success(rep, manifest, events)
    except Exception as exc:   # noqa: BLE001 -- journal input must never crash the reporter
        return _write(_invalidate(rep, V_REPORT_INVALIDATED, "unexpected_error",
                                  f"{type(exc).__name__}: {exc}"), output_dir, _fault=_fault, recover=recover)
    return _write(_finalize(rep), output_dir, _fault=_fault, recover=recover)


def _session_invalidated(rep: Dict, run_result: Dict) -> Dict:
    reason = (run_result.get("mismatched_field") or run_result.get("reason") or "invalidated")
    rep["lifecycle_status"] = INVALIDATED
    rep["live_execution_enabled"] = bool(run_result.get("live_execution_enabled", False))
    detail = run_result.get("detail") or run_result.get("reason") or ""
    return _invalidate(rep, V_SESSION_INVALIDATED, str(reason), str(detail))


# --------------------------------------------------------------------------- #
# semantic + structural validation (first failure wins)                       #
# --------------------------------------------------------------------------- #
def _validate_semantics(events: List[Dict], manifest: Dict, session_id) -> Tuple[bool, str, str]:
    if not events:
        return False, "incomplete_journal", "no events"

    # (0) shape: every event is a dict with a dict payload, non-empty str type/id, known type
    for e in events:
        if not isinstance(e, dict):
            return False, "malformed_event", "an event is not an object"
        if not isinstance(e.get("payload"), dict):
            return False, "malformed_payload", f"event #{e.get('seq')} payload is not an object"
        et = e.get("event_type")
        if not isinstance(et, str) or not et:
            return False, "malformed_event", "event_type is not a non-empty string"
        if et not in _EVENT_TYPE_ALLOW:
            return False, "unknown_event_type", f"unknown event_type {et!r}"
        if not isinstance(e.get("event_id"), str) or not e.get("event_id"):
            return False, "malformed_event", "event_id is not a non-empty string"

    # (a) exactly one session_created (first) + one session_final (last)
    if sum(1 for e in events if e["event_type"] == "session_created") != 1 \
            or events[0]["event_type"] != "session_created":
        return False, "incomplete_journal", "there must be exactly one session_created, first"
    if sum(1 for e in events if e["event_type"] == "session_final") != 1 \
            or events[-1]["event_type"] != "session_final":
        return False, "incomplete_journal", "there must be exactly one session_final, last"

    # (b) timestamps non-negative ints; seq valid int
    for e in events:
        if not _is_int(e.get("timestamp"), nonneg=True):
            return False, "invalid_event_timestamp", f"event #{e.get('seq')} timestamp not a non-negative int"
        if not _is_int(e.get("seq"), nonneg=True):
            return False, "invalid_seq", "event seq not a non-negative int"

    # (c) unique event ids
    ids = [e.get("event_id") for e in events]
    if len(set(ids)) != len(ids):
        return False, "duplicate_event_id", "event_id values are not unique"

    # (d) non-decreasing timestamps
    prev = None
    for e in events:
        if prev is not None and e["timestamp"] < prev:
            return False, "timestamp_monotonicity_failure", f"event #{e['seq']} timestamp decreases"
        prev = e["timestamp"]

    created, final = events[0]["payload"], events[-1]["payload"]

    # (e) session_created binding
    if created.get("session_id") != session_id:
        return False, "session_id_mismatch", "journal session_id != manifest session_id"
    if created.get("manifest_hash") != manifest["manifest_hash"]:
        return False, "manifest_hash_mismatch", "session_created.manifest_hash != manifest"
    if created.get("paper_trading_only") is not True:
        return False, "paper_trading_only_violation", "paper_trading_only is not True"
    if created.get("live_execution_enabled") is not False:
        return False, "live_execution_enabled", "session_created live_execution_enabled != False"
    if created.get("shorting_allowed") is not False:
        return False, "shorting_allowed_violation", "shorting_allowed is not False"
    if created.get("signal_policy_hash") != manifest.get("signal_policy_hash"):
        return False, "signal_policy_hash_mismatch", "signal_policy_hash != manifest"

    # (f) session_final manifest binding
    if final.get("manifest_hash") != manifest["manifest_hash"]:
        return False, "manifest_hash_mismatch", "session_final.manifest_hash != manifest"

    # (g) terminal lifecycle allow-list
    lifecycle = final.get("lifecycle_status")
    if lifecycle not in TERMINAL_ALLOW:
        return False, "invalid_lifecycle_status", f"lifecycle_status {lifecycle!r} not permitted"

    # session_final container shapes
    absolute_risk = final.get("absolute_risk")
    if not isinstance(absolute_risk, dict):
        return False, "malformed_payload", "absolute_risk is not an object"
    breaches = absolute_risk.get("breaches")
    if not isinstance(breaches, list):
        return False, "malformed_payload", "absolute_risk.breaches is not a list"
    for b in breaches:
        if not isinstance(b, dict):
            return False, "malformed_payload", "a breach is not an object"
    broker = final.get("broker")
    if not isinstance(broker, dict):
        return False, "malformed_payload", "broker snapshot is not an object"
    counts = final.get("counts")
    if not isinstance(counts, dict):
        return False, "malformed_payload", "counts is not an object"

    # (h) terminal state consistency
    tcok, tccode = _terminal_consistency(events, lifecycle, absolute_risk, breaches)
    if not tcok:
        return False, tccode, "lifecycle inconsistent with journal evidence"

    # (i) session_final economic types
    init_cap, final_equity, net_pnl = final.get("initial_capital"), final.get("final_equity"), final.get("net_pnl")
    if not _is_finite_number(init_cap, allow_negative=False) or float(init_cap) <= 0:
        return False, "invalid_numeric_field:initial_capital", "initial_capital not finite > 0"
    if not _is_finite_number(final_equity):
        return False, "invalid_numeric_field:final_equity", "final_equity not finite"
    if not _is_finite_number(net_pnl):
        return False, "invalid_numeric_field:net_pnl", "net_pnl not finite"
    for name, neg in (("cash", True), ("position_units", True), ("mark_price", False),
                      ("equity", True), ("fees_paid", False), ("spread_cost", False),
                      ("slippage_cost", False), ("drawdown", False), ("realized_pnl", True),
                      ("equity_peak", True)):
        if not _is_finite_number(broker.get(name), allow_negative=neg):
            return False, f"invalid_numeric_field:broker.{name}", f"broker.{name} invalid"

    # (j) counts non-negative ints
    count_keys = ("bars", "signals", "orders", "fills", "partials", "rejects",
                  "risk_events", "safe_exits", "execution_errors")
    for k in count_keys:
        if not _is_int(counts.get(k), nonneg=True):
            return False, "invalid_counts", f"counts.{k} not a non-negative int"

    # (k) per-event economic validation (orders / snapshots / signals)
    eok, ecode, edetail = _validate_event_economics(events)
    if not eok:
        return False, ecode, edetail

    # (l) reconciliation
    if abs(net_pnl - (final_equity - init_cap)) > _RECON_EPS:
        return False, "net_pnl_reconciliation_failure", "net_pnl != final_equity - initial_capital"
    if abs(final_equity - (broker["cash"] + broker["position_units"] * broker["mark_price"])) > _RECON_EPS:
        return False, "net_pnl_reconciliation_failure", "final_equity != cash + position*mark"
    if abs(broker["equity"] - final_equity) > _RECON_EPS:
        return False, "equity_reconciliation_failure", "broker.equity != final_equity"

    # (m) counts recount
    rc = _recount(events)
    for k in count_keys:
        if rc[k] != counts[k]:
            return False, "counts_mismatch", f"counts.{k}={counts[k]} but journal has {rc[k]}"

    # (n) summed order costs == broker snapshot
    sums = _order_cost_sums(events)
    for src, dst in (("fee", "fees_paid"), ("spread_cost", "spread_cost"),
                     ("slippage_cost", "slippage_cost")):
        if abs(sums[src] - float(broker[dst])) > _RECON_EPS:
            return False, "cost_reconciliation_failure", f"summed order {src} != broker.{dst}"

    # (o) risk breach observed/limit finite
    for b in breaches:
        for key in ("observed", "limit"):
            v = b.get(key)
            if v is not None and not _is_finite_number(v):
                return False, "invalid_numeric_field:breach", f"breach {key} not finite"

    # (p) strict 1:1 signal<->order linkage + structural + RESULT-proven safe-exit reconciled
    #     three ways against the final broker state, using a qty_step-derived dust tolerance.
    ok_t, dust_tol, treason = _dust_tolerance(manifest)
    if not ok_t:
        return False, "invalid_qty_step", treason
    return _validate_linkage(events, lifecycle, final, dust_tol)


def _terminal_consistency(events, lifecycle, absolute_risk, breaches) -> Tuple[bool, str]:
    """Each terminal status must match EXACTLY one lawful event model of the Stage 1 engine."""
    et = [e["event_type"] for e in events]
    risk_events = [e for e in events if e["event_type"] == "risk_event"]
    has_risk_kind = any(e["payload"].get("kind") == "risk" for e in risk_events)
    has_data_kind = any(e["payload"].get("kind") == "data" for e in risk_events)
    n_safe_exit = et.count("safe_exit")
    has_data_gap = "data_gap" in et
    has_bar_rejected = "bar_rejected" in et
    has_signal_rejected = "signal_rejected" in et
    breached = absolute_risk.get("breached") is True and len(breaches) > 0

    if lifecycle == COMPLETED:
        if risk_events or has_data_gap or has_bar_rejected or has_signal_rejected \
                or n_safe_exit or breached:
            return False, "inconsistent_terminal_state"
    elif lifecycle == RISK_STOPPED:
        if not (has_risk_kind and breached and n_safe_exit == 1):
            return False, "inconsistent_terminal_state"
        if has_data_kind or has_data_gap or has_bar_rejected or has_signal_rejected:
            return False, "inconsistent_terminal_state"   # no competing terminal cause
    elif lifecycle == DATA_STOPPED:
        if not (has_data_kind or has_data_gap or has_bar_rejected):
            return False, "inconsistent_terminal_state"
        if n_safe_exit or has_signal_rejected:
            return False, "inconsistent_terminal_state"
        # no ordinary events implying continued running after the terminal data evidence
        last_data = max([i for i, e in enumerate(events)
                         if e["event_type"] in ("data_gap", "bar_rejected")
                         or (e["event_type"] == "risk_event" and e["payload"].get("kind") == "data")],
                        default=-1)
        if any(events[j]["event_type"] in ("order", "signal", "bar_ingested", "portfolio_snapshot")
               for j in range(last_data + 1, len(events) - 1)):
            return False, "inconsistent_terminal_state"
    elif lifecycle == SIGNAL_STOPPED:
        sr = [e for e in events if e["event_type"] == "signal_rejected"]
        if not sr or not sr[-1]["payload"].get("reason_code"):
            return False, "inconsistent_terminal_state"
        if n_safe_exit or risk_events or has_data_gap or has_bar_rejected:
            return False, "inconsistent_terminal_state"
        sr_seq = max(i for i, e in enumerate(events) if e["event_type"] == "signal_rejected")
        if any(events[j]["event_type"] == "order" for j in range(sr_seq + 1, len(events))):
            return False, "inconsistent_terminal_state"
    return True, ""


def _validate_event_economics(events: List[Dict]) -> Tuple[bool, str, str]:
    for e in events:
        et, p = e["event_type"], e["payload"]
        if et == "order":
            if not _is_int(p.get("exec_open_time"), nonneg=True):
                return False, "invalid_order_field", "order.exec_open_time not a non-negative int"
            if p.get("status") not in _ORDER_STATUS_ALLOW:
                return False, "invalid_order_field", f"order.status {p.get('status')!r} not allowed"
            for k in ("fee", "spread_cost", "slippage_cost", "fill_price"):
                if not _is_finite_number(p.get(k), allow_negative=False):
                    return False, "invalid_order_field", f"order.{k} not finite >= 0"
            for k in ("requested_units", "filled_units", "cash_after", "position_after",
                      "equity_after"):
                if not _is_finite_number(p.get(k)):
                    return False, "invalid_order_field", f"order.{k} not finite"
        elif et == "portfolio_snapshot":
            if not _is_int(p.get("open_time"), nonneg=True):
                return False, "invalid_snapshot_field", "snapshot.open_time not a non-negative int"
            for k in ("close", "cash", "mark_price", "equity", "equity_peak", "position_units",
                      "realized_pnl"):
                if not _is_finite_number(p.get(k)):
                    return False, "invalid_snapshot_field", f"snapshot.{k} not finite"
            for k in ("drawdown", "fees_paid", "spread_cost", "slippage_cost", "gross_exposure_frac"):
                if not _is_finite_number(p.get(k), allow_negative=False):
                    return False, "invalid_snapshot_field", f"snapshot.{k} not finite >= 0"
        elif et == "signal":
            db, st, eet = p.get("decision_bar"), p.get("signal_time"), p.get("earliest_execution_time")
            if not _is_int(db, nonneg=True):
                return False, "invalid_signal_field", "signal.decision_bar invalid"
            if not _is_int(st, nonneg=True) or st < db:
                return False, "invalid_signal_field", "signal.signal_time invalid (< decision_bar)"
            if eet is not None and (not _is_int(eet, nonneg=True) or eet <= db):
                return False, "invalid_signal_field", "signal.earliest_execution_time invalid"
            if not _is_finite_number(p.get("target_position")):
                return False, "invalid_signal_field", "signal.target_position not finite"
        elif et == "bar_ingested":
            if not _is_int(p.get("open_time"), nonneg=True):
                return False, "invalid_bar_field", "bar.open_time not a non-negative int"
            for k in ("open", "high", "low", "close"):
                if not _is_finite_number(p.get(k)) or float(p[k]) <= 0:
                    return False, "invalid_bar_field", f"bar.{k} not finite > 0"
            if not _is_finite_number(p.get("volume"), allow_negative=False):
                return False, "invalid_bar_field", "bar.volume not finite >= 0"
            o, h, low_, c = float(p["open"]), float(p["high"]), float(p["low"]), float(p["close"])
            if not (h >= low_ and h >= o and h >= c and low_ <= o and low_ <= c):
                return False, "invalid_bar_field", "impossible OHLC relationship (need low<=open,close<=high)"
        elif et == "safe_exit":
            if not _is_int(p.get("open_time"), nonneg=True):
                return False, "invalid_safe_exit_field", "safe_exit.open_time not a non-negative int"
            if p.get("order_status") not in _SAFE_EXIT_STATUS_ALLOW:
                return False, "invalid_safe_exit_field", f"safe_exit.order_status {p.get('order_status')!r} not allowed"
            if "flat" in p and not isinstance(p["flat"], bool):
                return False, "invalid_safe_exit_field", "safe_exit.flat not a bool"
            for k in ("position_units", "mark_price", "equity"):
                if k in p and not _is_finite_number(p[k]):
                    return False, "invalid_safe_exit_field", f"safe_exit.{k} not finite"
        elif et == "risk_event":
            if not isinstance(p.get("kind"), str) or not p.get("kind"):
                return False, "invalid_risk_event_field", "risk_event.kind not a non-empty string"
            ar = p.get("absolute_risk")
            if not isinstance(ar, dict) or not isinstance(ar.get("breaches"), list):
                return False, "invalid_risk_event_field", "risk_event.absolute_risk malformed"
        elif et == "data_gap":
            if not _is_int(p.get("open_time"), nonneg=True):
                return False, "invalid_data_gap_field", "data_gap.open_time not a non-negative int"
            if not _is_int(p.get("missing_trading_bars"), nonneg=True):
                return False, "invalid_data_gap_field", "data_gap.missing_trading_bars invalid"
        elif et == "bar_rejected":
            if not isinstance(p.get("reason"), str) or not p.get("reason"):
                return False, "invalid_bar_rejected_field", "bar_rejected.reason not a non-empty string"
        elif et == "signal_rejected":
            if not isinstance(p.get("reason_code"), str) or not p.get("reason_code"):
                return False, "invalid_signal_rejected_field", "signal_rejected.reason_code missing"
    return True, "", ""


def _recount(events: List[Dict]) -> Dict:
    rc = {k: 0 for k in ("bars", "signals", "orders", "fills", "partials", "rejects",
                         "risk_events", "safe_exits", "execution_errors")}
    for e in events:
        et = e["event_type"]
        if et == "bar_ingested":
            rc["bars"] += 1
        elif et == "signal":
            rc["signals"] += 1
        elif et == "order":
            rc["orders"] += 1
            st = e["payload"].get("status")
            if st == "filled":
                rc["fills"] += 1
            elif st == "partial":
                rc["partials"] += 1
            elif st == "rejected":
                rc["rejects"] += 1
        elif et == "risk_event":
            rc["risk_events"] += 1
        elif et == "safe_exit":
            if e["payload"].get("order_status") not in (None, "none"):
                rc["safe_exits"] += 1
        elif et == "execution_error":
            rc["execution_errors"] += 1
    return rc


def _order_cost_sums(events: List[Dict]) -> Dict:
    s = {"fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0}
    for e in events:
        if e["event_type"] != "order":
            continue
        for k in s:
            s[k] += float(e["payload"][k])   # validated finite >= 0 in _validate_event_economics
    return s


def _agree(a, b, tol: float) -> bool:
    return _is_finite_number(a) and _is_finite_number(b) and abs(float(a) - float(b)) <= tol


def _validate_linkage(events: List[Dict], lifecycle: str, final: Dict,
                      dust_tol: float) -> Tuple[bool, str, str]:
    # ---- structural safe-exit identification (not just a matching timestamp) --------- #
    safe_exit_idx = [i for i, e in enumerate(events) if e["event_type"] == "safe_exit"]
    order_idx = [i for i, e in enumerate(events) if e["event_type"] == "order"]
    safe_exit_order_pos: Optional[int] = None
    if len(safe_exit_idx) > 1:
        return False, "safe_exit_multiple", "more than one safe-exit event"
    if safe_exit_idx:
        se_i = safe_exit_idx[0]
        se = events[se_i]["payload"]
        if lifecycle != RISK_STOPPED:
            return False, "safe_exit_without_risk_stop", "safe_exit present but lifecycle != RISK_STOPPED"
        # the safe-exit must be armed by a preceding risk_event of kind == "risk" (not data)
        risk_kind_idx = [j for j, e in enumerate(events)
                         if e["event_type"] == "risk_event" and e["payload"].get("kind") == "risk"
                         and j < se_i]
        if not risk_kind_idx:
            return False, "safe_exit_before_risk_event", "no risk_event(kind=risk) precedes the safe_exit"
        trigger = max(risk_kind_idx)
        if any(events[j]["event_type"] == "order" and j > se_i for j in range(len(events))):
            return False, "order_after_safe_exit", "an order follows the safe_exit event"
        status = se.get("order_status")
        broker = final.get("broker") or {}
        b_pos, b_eq, b_mark = broker.get("position_units"), broker.get("equity"), broker.get("mark_price")
        final_equity = final.get("final_equity")
        se_pu, se_eq, se_mark = se.get("position_units"), se.get("equity"), se.get("mark_price")
        if status == "none":
            # already-flat / no-next-bar: NO order between the triggering risk_event and safe_exit
            if any(events[j]["event_type"] == "order" for j in range(trigger + 1, se_i)):
                return False, "safe_exit_order_mismatch", "order present before an order_status=none safe-exit"
            # (item 1) the note must be an EXACT Stage 1 none-note (no substring / arbitrary text)
            if se.get("note") not in _SAFE_EXIT_NONE_NOTES:
                return False, "safe_exit_none_note_invalid", f"unknown order_status=none note {se.get('note')!r}"
            # (item 2) an unresolved (no-next-bar) exit leaves the position OPEN -> not a valid exit
            if se.get("note") == _SAFE_EXIT_NONE_UNRESOLVED_NOTE:
                return False, "safe_exit_unresolved", "safe-exit left the position open (no next bar)"
            if "flat" in se and not isinstance(se.get("flat"), bool):
                return False, "safe_exit_not_flat", "safe_exit.flat not a bool"
            # missing position evidence must NOT be read as flat
            if not _is_finite_number(se_pu):
                return False, "safe_exit_missing_position", "already-flat safe-exit has no finite position_units"
            if not _is_finite_number(b_pos):
                return False, "safe_exit_not_flat", "final broker.position_units not finite"
            # both positions within dust tolerance AND agree with each other
            if abs(float(se_pu)) > dust_tol or abs(float(b_pos)) > dust_tol:
                return False, "safe_exit_not_flat", "already-flat position not within dust tolerance"
            if not _agree(se_pu, b_pos, dust_tol):
                return False, "safe_exit_position_mismatch", "safe_exit vs final broker position disagree"
            if not _agree(se_eq, final_equity, _RECON_EPS) or not _agree(se_eq, b_eq, _RECON_EPS):
                return False, "safe_exit_equity_inconsistent", "safe_exit.equity != final_equity/broker.equity"
            if not _agree(se_mark, b_mark, _RECON_EPS):
                return False, "safe_exit_mark_inconsistent", "safe_exit.mark_price != final broker.mark_price"
            safe_exit_order_pos = None
        else:
            prev = events[se_i - 1] if se_i > 0 else None
            if prev is None or prev["event_type"] != "order":
                return False, "safe_exit_order_mismatch", "no order immediately precedes safe_exit"
            po = prev["payload"]
            pa = po.get("position_after")
            if po.get("exec_open_time") != se.get("open_time") or po.get("status") != status:
                return False, "safe_exit_order_mismatch", "safe-exit order open_time/status mismatch"
            if not (se_i - 1 > trigger):
                return False, "safe_exit_order_mismatch", "safe-exit order does not follow the risk_event"
            # (item 2) PROVE the exit resolved — a matching timestamp/status is NOT enough.
            if status != "filled":
                return False, "safe_exit_unresolved", f"safe-exit did not complete (status={status})"
            if "flat" in se and not isinstance(se.get("flat"), bool):
                return False, "safe_exit_not_flat", "safe_exit.flat not a bool"
            # THREE-WAY residual: safe_exit.position_units, order.position_after, final broker.position_units
            for v, name in ((se_pu, "safe_exit.position_units"), (pa, "order.position_after"),
                            (b_pos, "final broker.position_units")):
                if not _is_finite_number(v) or abs(float(v)) > dust_tol:
                    return False, "safe_exit_not_flat", f"{name} not within dust tolerance"
            if not (_agree(se_pu, pa, dust_tol) and _agree(pa, b_pos, dust_tol)
                    and _agree(se_pu, b_pos, dust_tol)):
                return False, "safe_exit_position_mismatch", "safe_exit/order/broker positions disagree"
            # equity agrees with final_equity AND final broker.equity; mark agrees with broker.mark
            if not _agree(se_eq, final_equity, _RECON_EPS) or not _agree(se_eq, b_eq, _RECON_EPS):
                return False, "safe_exit_equity_inconsistent", "safe_exit.equity != final_equity/broker.equity"
            if not _is_finite_number(se_mark) or float(se_mark) <= 0 or not _agree(se_mark, b_mark, _RECON_EPS):
                return False, "safe_exit_mark_inconsistent", "safe_exit.mark_price invalid or != final broker.mark_price"
            safe_exit_order_pos = se_i - 1

    # ---- signals keyed by earliest, uniqueness enforced ------------------------------ #
    sig_by_earliest: Dict = {}
    for i, e in enumerate(events):
        if e["event_type"] != "signal":
            continue
        p = e["payload"]
        eet = p.get("earliest_execution_time")
        if eet is None:
            continue
        if eet in sig_by_earliest:
            return False, "duplicate_signal_earliest", "two signals share earliest_execution_time"
        sig_by_earliest[eet] = {"seq": i, "decision_bar": p["decision_bar"], "used": False}

    # ---- every NORMAL order links to exactly one earlier signal (1:1) ---------------- #
    seen_exec = set()
    for i in order_idx:
        if i == safe_exit_order_pos:
            continue
        exec_t = events[i]["payload"]["exec_open_time"]
        if exec_t in seen_exec:
            return False, "multiple_orders_same_bar", f"two normal orders at exec_open_time {exec_t}"
        seen_exec.add(exec_t)
        sig = sig_by_earliest.get(exec_t)
        if sig is None:
            return False, "order_without_signal", f"order at {exec_t} has no matching signal"
        if sig["used"]:
            return False, "order_reuses_signal", f"signal at earliest {exec_t} reused by two orders"
        sig["used"] = True
        if not (i > sig["seq"]):
            return False, "order_not_after_signal", "order does not follow its signal"
        if not (exec_t > sig["decision_bar"]):
            return False, "order_not_after_decision", f"order at {exec_t} not after decision {sig['decision_bar']}"
    return True, "", ""


# --------------------------------------------------------------------------- #
# success builder                                                             #
# --------------------------------------------------------------------------- #
def _sorted_breaches(absolute_risk: Dict) -> List[Dict]:
    out = [{"rule": b.get("rule"), "observed": b.get("observed"),
            "limit": b.get("limit"), "message": b.get("message")}
           for b in (absolute_risk or {}).get("breaches") or []]
    out.sort(key=lambda x: (str(x["rule"]), _num(x["observed"])))
    return out


def _num(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else float("inf")


def _why_it_failed(lifecycle: str, events: List[Dict], final: Dict) -> Tuple[List[Dict], str]:
    if lifecycle == RISK_STOPPED:
        rows = _sorted_breaches(final.get("absolute_risk"))
        return (rows, str(rows[0]["rule"])) if rows else ([], "risk_stopped")
    if lifecycle == DATA_STOPPED:
        rows = _sorted_breaches(final.get("absolute_risk"))
        if rows:
            return rows, str(rows[0]["rule"])
        for e in reversed(events):
            if e["event_type"] == "bar_rejected":
                r = e["payload"].get("reason")
                return [{"rule": "bar_rejected", "observed": r, "limit": None, "message": r}], "bar_rejected"
            if e["event_type"] == "data_gap":
                mv = e["payload"].get("missing_trading_bars")
                return [{"rule": "data_gap", "observed": mv, "limit": None, "message": "data gap"}], "data_gap"
        return [], "data_stopped"
    if lifecycle == SIGNAL_STOPPED:
        for e in reversed(events):
            if e["event_type"] == "signal_rejected":
                rc = e["payload"].get("reason_code", "signal_rejected")
                return [{"rule": rc, "observed": e["payload"].get("original"),
                         "limit": None, "message": "signal rejected"}], str(rc)
        return [], "signal_stopped"
    return [], "completed"


def _max_drawdown(events: List[Dict], broker: Dict) -> float:
    dd = 0.0
    for e in events:
        if e["event_type"] == "portfolio_snapshot":
            v = e["payload"].get("drawdown")
            if _is_finite_number(v) and v > dd:
                dd = float(v)
    if _is_finite_number(broker.get("drawdown")) and broker["drawdown"] > dd:
        dd = float(broker["drawdown"])
    return dd


def _safe_exit_summary(events: List[Dict], dust_tol: float) -> Optional[Dict]:
    """Surface the safe-exit outcome honestly (a report is only built after the safe-exit passed
    three-way reconciliation, so a present safe-exit is resolved — but the dust residual and the
    exact tolerance + its source are shown, never hidden)."""
    for e in events:
        if e["event_type"] != "safe_exit":
            continue
        p = e["payload"]
        pu = p.get("position_units")
        residual = float(pu) if _is_finite_number(pu) else None
        return {"order_status": p.get("order_status"), "note": _md_safe(p.get("note")),
                "resolved": True, "residual_position_units": residual, "flat_flag": p.get("flat"),
                "dust_tolerance": dust_tol,
                "dust_tolerance_source": "manifest.execution_profile.qty_step"}
    return None


def _timing_block(events: List[Dict], counts: Dict) -> Dict:
    first_signal = None
    for e in events:
        if e["event_type"] == "signal":
            p = e["payload"]
            first_signal = {"decision_bar": p.get("decision_bar"), "signal_time": p.get("signal_time"),
                            "earliest_execution_time": p.get("earliest_execution_time")}
            break
    safe_times = {e["payload"].get("open_time") for e in events if e["event_type"] == "safe_exit"}
    n_safe_orders = sum(1 for e in events if e["event_type"] == "order"
                        and e["payload"].get("exec_open_time") in safe_times)
    return {
        "signals": counts.get("signals", 0), "orders": counts.get("orders", 0),
        "fills": counts.get("fills", 0), "partials": counts.get("partials", 0),
        "rejects": counts.get("rejects", 0), "risk_events": counts.get("risk_events", 0),
        "safe_exits": counts.get("safe_exits", 0), "execution_errors": counts.get("execution_errors", 0),
        "data_gap_events": sum(1 for e in events if e["event_type"] == "data_gap"),
        "safe_exit_orders": n_safe_orders,
        "execution_timing_rule": "pending decision executes at the next valid bar open",
        "first_signal": first_signal, "look_ahead_violations": 0,
        "order_signal_links_ok": True, "timestamp_monotonic": True, "first_timing_anomaly": None,
    }


def _fill_success(rep: Dict, manifest: Dict, events: List[Dict]) -> None:
    final = events[-1]["payload"]
    broker = final["broker"]
    lifecycle = final["lifecycle_status"]
    counts = final["counts"]
    init_cap = float(final["initial_capital"])
    final_equity = float(final["final_equity"])
    net_pnl = float(final["net_pnl"])
    fees, spread_cost = float(broker["fees_paid"]), float(broker["spread_cost"])
    slippage_cost, funding_cost = float(broker["slippage_cost"]), 0.0
    friction = fees + spread_cost + slippage_cost + funding_cost
    why, code = _why_it_failed(lifecycle, events, final)

    rep["lifecycle_status"] = lifecycle
    rep["verdict"] = _LIFECYCLE_VERDICT[lifecycle]
    rep["exact_reason_code"] = code
    rep["initial_capital"] = init_cap
    rep["final_equity"] = final_equity
    rep["net_pnl"] = net_pnl
    rep["gross_pnl"] = net_pnl + friction
    rep["fees"], rep["spread_cost"], rep["slippage_cost"], rep["funding_cost"] = \
        fees, spread_cost, slippage_cost, funding_cost
    rep["max_drawdown"] = _max_drawdown(events, broker)
    rep["counts"] = dict(counts)
    rep["why_it_failed"] = why
    _ok_t, _dust_tol, _ = _dust_tolerance(manifest)
    rep["safe_exit"] = _safe_exit_summary(events, _dust_tol)
    rep["timing"] = _timing_block(events, counts)
    rep["integrity"] = {"ok": True, "reason_code": "ok", "detail": ""}
    rep["final_event_hash"] = events[-1]["event_hash"]
    rep["journal_verified"] = True
    rep["live_execution_enabled"] = False


# --------------------------------------------------------------------------- #
# atomic, cryptographically-verified, session-bound output                    #
# --------------------------------------------------------------------------- #
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_report(output_dir: str) -> Optional[Dict]:
    """Return the report ONLY if a complete, cryptographically self-consistent set is present:
    marker + json + md all exist, the marker's json/markdown sha256 match the on-disk bytes, and
    the report_hash reproduces from the canonical JSON body and matches both the JSON field and the
    marker. Any tamper/truncation/partial ⇒ None."""
    marker = os.path.join(output_dir, COMPLETE_MARKER)
    jpath, mpath = report_paths(output_dir)
    if not (os.path.exists(marker) and os.path.exists(jpath) and os.path.exists(mpath)):
        return None
    try:
        with open(marker, "rb") as f:
            m = _json.loads(f.read().decode("utf-8"))
        json_bytes = open(jpath, "rb").read()
        md_bytes = open(mpath, "rb").read()
        rep = _json.loads(json_bytes.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(rep, dict) or not isinstance(m, dict):
        return None
    # exact-bytes integrity against the marker
    if _sha256_bytes(json_bytes) != m.get("json_sha256"):
        return None
    if _sha256_bytes(md_bytes) != m.get("markdown_sha256"):
        return None
    # recompute report_hash from the canonical body; must match JSON field AND marker
    body = {k: v for k, v in rep.items() if k != "report_hash"}
    recomputed = sha256_text(canonical_json(body))
    if recomputed != rep.get("report_hash") or recomputed != m.get("report_hash"):
        return None
    if rep.get("session_id") != m.get("session_id") or rep.get("manifest_hash") != m.get("manifest_hash"):
        return None
    return rep


def _artifacts_present(output_dir: str) -> bool:
    jpath, mpath = report_paths(output_dir)
    return any(os.path.exists(p) for p in
               (jpath, mpath, os.path.join(output_dir, COMPLETE_MARKER),
                os.path.join(output_dir, STAGING_DIR)))


def _recover_cleanup(output_dir: str, sid, mh) -> None:
    """Explicit recovery: verify any readable leftover belongs to THIS session, then remove the
    partial/tampered artifacts + staging so a fresh set can be published. Journaled to
    session_report.recovery.log."""
    jpath, mpath = report_paths(output_dir)
    marker = os.path.join(output_dir, COMPLETE_MARKER)
    staging = os.path.join(output_dir, STAGING_DIR)
    if os.path.exists(jpath):
        try:
            with open(jpath, encoding="utf-8") as f:
                raw = _json.load(f)
        except Exception:
            raw = None
        if isinstance(raw, dict) and (raw.get("session_id") != sid or raw.get("manifest_hash") != mh):
            raise ReportError("refusing to recover: leftover report belongs to a different session")
    for p in (marker, jpath, mpath):
        if os.path.exists(p):
            os.remove(p)
    if os.path.isdir(staging):
        for name in os.listdir(staging):
            os.remove(os.path.join(staging, name))
        os.rmdir(staging)
    with open(os.path.join(output_dir, "session_report.recovery.log"), "a", encoding="utf-8") as f:
        f.write(canonical_json({"event": "recovered_incomplete_report_set",
                                "session_id": sid, "manifest_hash": mh}) + "\n")


def _write(rep: Dict, output_dir: str, *, _fault: Optional[str] = None,
           recover: bool = False) -> Dict:
    os.makedirs(output_dir, exist_ok=True)
    sid, mh, rh = rep.get("session_id"), rep.get("manifest_hash"), rep.get("report_hash")

    complete = load_report(output_dir)
    if complete is not None:
        if complete.get("session_id") != sid or complete.get("manifest_hash") != mh:
            raise ReportError(f"refusing to overwrite a different session's report "
                              f"({complete.get('session_id')!r} != {sid!r})")
        if complete.get("report_hash") != rh:
            raise ReportError("refusing to silently overwrite: same session, different report content")
        return rep  # identical complete report -> idempotent no-op

    # No COMPLETE set. If ANY artifact/staging is present, the set is incomplete or tampered — never
    # silently overwrite or repair it. Recovery must be explicit (recover=True).
    if _artifacts_present(output_dir):
        if not recover:
            raise ReportError("existing report set is incomplete or tampered")
        _recover_cleanup(output_dir, sid, mh)

    _publish_set(rep, output_dir, _fault=_fault)
    return rep


def _publish_set(rep: Dict, output_dir: str, *, _fault: Optional[str] = None) -> None:
    json_text = canonical_json(rep) + "\n"
    md_text = _render_markdown(rep)
    json_sha = _sha256_bytes(json_text.encode("utf-8"))
    md_sha = _sha256_bytes(md_text.encode("utf-8"))
    staging = os.path.join(output_dir, STAGING_DIR)
    os.makedirs(staging, exist_ok=True)
    jtmp, mtmp = os.path.join(staging, JSON_NAME), os.path.join(staging, MD_NAME)
    jpath, mpath = report_paths(output_dir)
    marker = os.path.join(output_dir, COMPLETE_MARKER)

    if os.path.exists(marker):     # no complete set is advertised mid-publish
        os.remove(marker)

    _fsync_write(jtmp, json_text)
    if _fault == "after_json_stage":
        raise _InjectedFault("after_json_stage")
    _fsync_write(mtmp, md_text)
    if _fault == "after_md_stage":
        raise _InjectedFault("after_md_stage")

    os.replace(jtmp, jpath)
    if _fault == "between_publish":
        raise _InjectedFault("between_publish")
    os.replace(mtmp, mpath)

    marker_body = canonical_json({
        "session_id": rep.get("session_id"), "manifest_hash": rep.get("manifest_hash"),
        "report_hash": rep.get("report_hash"), "json_sha256": json_sha, "markdown_sha256": md_sha,
    }) + "\n"
    _fsync_write(marker, marker_body)     # marker written ONLY after both files are in place
    try:
        os.rmdir(staging)
    except OSError:
        pass


def _fsync_write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


# --------------------------------------------------------------------------- #
# deterministic, injection-safe Markdown                                      #
# --------------------------------------------------------------------------- #
_CTRL_TRANSLATE = {c: 0x20 for c in list(range(0x00, 0x20)) + [0x7f, 0x85, 0x2028, 0x2029]}


def _md_safe(v) -> str:
    """Neutralise journal-derived text: strip newlines/control chars/Unicode line separators and
    escape HTML/markdown metacharacters, so text can never forge a heading, a status line or HTML."""
    s = "" if v is None else str(v)
    s = s.translate(_CTRL_TRANSLATE)                 # newlines, controls, U+2028/2029/85 -> space
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "'")
    return s


def _live_line(live) -> str:
    if live is False:
        return "- Live execution: DISABLED"
    if live is True:
        return "- Live execution: VIOLATION — ENABLED"
    return "- Live execution: INVALID/UNKNOWN"


def _fmt_money(x) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


def _fmt_frac(x) -> str:
    return "n/a" if x is None else f"{x:.6f}"


def _render_markdown(rep: Dict) -> str:
    L: List[str] = []
    A = L.append
    A("# Paper Session Report")
    A("")
    A("## Status")
    A("")
    A(f"- Session: {_md_safe(rep['session_id'])}")
    A(f"- Strategy: {_md_safe(rep['strategy_id'])} ({_md_safe(rep['strategy_hash'])})")
    A(f"- Asset / timeframe: {_md_safe(rep['asset'])} / {_md_safe(rep['timeframe'])}")
    A(f"- Status: {_md_safe(rep['lifecycle_status'])}")
    A(f"- Verdict: {_md_safe(rep['verdict'])}")
    integ = rep.get("integrity") or {}
    A(f"- Data/journal integrity: {'OK' if integ.get('ok') else 'FAILED'}")
    A(f"- Journal verified: {rep['journal_verified']}")
    A(_live_line(rep.get("live_execution_enabled")))
    A(f"- Exact reason: {_md_safe(rep['exact_reason_code'])}")
    A("")
    A("## Result (gross vs net)")
    A("")
    A(f"- Initial capital: {_fmt_money(rep['initial_capital'])}")
    A(f"- Final equity: {_fmt_money(rep['final_equity'])}")
    A(f"- Gross PnL (net + frictions added back): {_fmt_money(rep['gross_pnl'])}")
    A(f"- Net PnL (Final Equity - Initial Capital): {_fmt_money(rep['net_pnl'])}")
    A("")
    A("## Cost breakdown")
    A("")
    A(f"- Fees: {_fmt_money(rep['fees'])}")
    A(f"- Spread cost: {_fmt_money(rep['spread_cost'])}")
    A(f"- Slippage cost: {_fmt_money(rep['slippage_cost'])}")
    A(f"- Funding cost: {_fmt_money(rep['funding_cost'])}")
    A("")
    A("## Risk and stops")
    A("")
    A(f"- max_drawdown: {_fmt_frac(rep['max_drawdown'])}")
    if rep.get("why_it_failed"):
        A("- Why it failed (deterministically sorted):")
        for b in rep["why_it_failed"]:
            A(f"    - rule={_md_safe(b.get('rule'))} observed={_md_safe(b.get('observed'))} "
              f"limit={_md_safe(b.get('limit'))}")
    else:
        A("- Why it failed: n/a (no stop/breach)")
    se = rep.get("safe_exit")
    if se:
        A(f"- Safe-exit: status={_md_safe(se.get('order_status'))} resolved={se.get('resolved')} "
          f"residual_position_units={_md_safe(se.get('residual_position_units'))} "
          f"(flat_flag={_md_safe(se.get('flat_flag'))})")
        A(f"    - dust_tolerance={_md_safe(se.get('dust_tolerance'))} "
          f"(source={_md_safe(se.get('dust_tolerance_source'))})")
    A("")
    A("## Signals and orders")
    A("")
    counts = rep.get("counts") or {}
    A(f"- signals={counts.get('signals')} orders={counts.get('orders')} "
      f"fills={counts.get('fills')} partials={counts.get('partials')} "
      f"rejects={counts.get('rejects')} risk_events={counts.get('risk_events')} "
      f"safe_exits={counts.get('safe_exits')}")
    A("")
    A("## Timing diagnostics")
    A("")
    timing = rep.get("timing") or {}
    A(f"- Execution timing: {_md_safe(timing.get('execution_timing_rule', 'n/a'))}")
    fs = timing.get("first_signal")
    if fs:
        A(f"- First decision bar: {_md_safe(fs.get('decision_bar'))} -> earliest execution: "
          f"{_md_safe(fs.get('earliest_execution_time'))}")
    A(f"- Look-ahead violations: {timing.get('look_ahead_violations', 'n/a')}")
    A(f"- Order/signal links ok: {timing.get('order_signal_links_ok', 'n/a')}")
    A(f"- Safe-exit orders: {timing.get('safe_exit_orders', 'n/a')}")
    A(f"- Timestamp monotonic: {timing.get('timestamp_monotonic', 'n/a')}")
    A(f"- First timing anomaly: {_md_safe(timing.get('first_timing_anomaly', 'n/a'))}")
    A(f"- Data gap / stale events: {timing.get('data_gap_events', 'n/a')}")
    A("")
    A("## Journal and manifest hashes")
    A("")
    A(f"- manifest_hash: {_md_safe(rep['manifest_hash'])}")
    A(f"- signal_policy_hash: {_md_safe(rep['signal_policy_hash'])}")
    A(f"- final_event_hash: {_md_safe(rep['final_event_hash'])}")
    A(f"- report_hash: {_md_safe(rep.get('report_hash'))}")
    A("")
    A("## Limitations")
    A("")
    for lim in rep.get("limitations") or []:
        A(f"- {_md_safe(lim)}")
    A("")
    return "\n".join(L)
