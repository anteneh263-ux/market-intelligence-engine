"""WP2B-4 — Phase 4 Final Integration & Evidence Gate (TEST-MODE ONLY).

Deterministic, fail-closed, read-only. WP2B-4 implements NO independent economic formulas: it invokes the
frozen public builders to reproduce upstream artifacts and compares their hashes and outputs against
independently pinned expected bindings. It gates exactly ONE authoritative forward paper session against an
earlier reference baseline via the frozen WP2B-3 TEST-mode drift diagnostic.

Authoritative design: PHASE4_WP2B_4_CONTROL_MATRIX_R3 + R3A (R3A governs on conflict), hardened per
WP2B-4 HARDENING R1.

Safety: production is impossible here (mode must be "TEST"); the empty
`APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES` allowlist is asserted; `release_eligible`,
`live_execution_eligible`, `live_execution_enabled` stay False; WP2B-4 never enables production or live
execution. It provides NO signatures/authentication and cannot determine who approved the pinned bindings.
"""
from __future__ import annotations

import errno
import hashlib
import math
import os
from typing import Dict, List, Optional, Tuple

from app.forward import metrics_v2 as _metrics_v2
from app.forward import trade_cycle_ledger as _tcl
from app.forward import segment_pipeline as _segp
from app.forward import cost_analysis as _cost
from app.forward import drift_classification as _drift
from app.forward.shadow import consistency as _consistency
from app.forward.shadow import ranges as _ranges
from app.forward.cost_analysis import canonical_bytes
from app.forward.segment_pipeline import build_segment_report_from_journal, SegmentPipelineError
from app.forward.cost_analysis import build_cost_analysis_from_journal, CostAnalysisError
from app.forward.drift_classification import (
    classify_drift_from_journals, DriftClassificationError,
)
# F2 (HARDENING R2): the production-threshold-policy allowlist is read at runtime from the
# authoritative module object `_drift.APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES` (see step 12
# SAFETY_INVARIANTS). No by-value import alias exists, so there is exactly one runtime source of
# truth and no stale snapshot can diverge from the module.
import app.paper.session as _psession
import app.paper.session.engine as _engine
import app.paper.session.journal as _journal
import app.paper.session.manifest as _manifest_mod
import app.paper.session.reporting as _reporting


def _sha(obj) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


# --------------------------------------------------------------------------- #
# constants                                                                   #
# --------------------------------------------------------------------------- #
METHOD = "phase4-final-gate"
VERSION = "wp2b4-v1"
SCHEMA_VERSION = "phase4-gate-v1"

JSON_NAME = "phase4_final_gate.json"
MD_NAME = "phase4_final_gate.md"
MARKER_NAME = "phase4_final_gate.complete"
STAGING_DIR = ".phase4_gate_staging"

WP2B4_DEPENDENCY_CONTRACT: Dict[str, str] = {
    "session__init__.py": "a25b55d4ebffa92db4a015ba41a3117438c45962881ae5609d2e1c9fdc5166c7",
    "engine.py": "138cbd3b98b42d06e9fb373d83461ddc84df0d49e4d53bc68de5726b704ef27c",
    "journal.py": "8f1326b9d1f25b234b1469523bbe6a1dea98128391a002e0cc38f245b3f6a7bd",
    "manifest.py": "1af26b546cbddbe15303149a37119388611a44af17c0d3b4bfd5b348e3a16ecf",
    "reporting.py": "951512df78646a1c9832d92096f68937c81b1749817ab8cdf0b3ed6de910fcc1",
    "metrics_v2.py": "707622376bd75b9d6630aae99b04f9eb8222d1b5508b52f0862e26006448808b",
    "trade_cycle_ledger.py": "5c36a60418b55eab9b36942101f56fb22a22a03fe4098cbb113200355795f1a2",
    "segment_pipeline.py": "eadbfda4d3fe32b7484433547353230513e3b9e00c4cb27f74cee487afdf5f43",
    "cost_analysis.py": "0aec32bc252842620f660860ba3c08d9c58595b597adcd5278baa93e4ed727cb",
    "drift_classification.py": "cd8c9375e2bf89ef6179c70d54667800c5bc194aeecea8acbccb8ab120bc55d9",
    "shadow_consistency.py": "93dede8123d4fbb8dff90c42f141d6fdacc9c395e5098a7532bd9e42d8958a72",
    "shadow_ranges.py": "7476ee16e88129e7f258f0c9c877f34c006b4a93128ff51d768a16f86b8eb742",
}
_DEP_FILES = {
    "session__init__.py": _psession.__file__, "engine.py": _engine.__file__,
    "journal.py": _journal.__file__, "manifest.py": _manifest_mod.__file__,
    "reporting.py": _reporting.__file__, "metrics_v2.py": _metrics_v2.__file__,
    "trade_cycle_ledger.py": _tcl.__file__, "segment_pipeline.py": _segp.__file__,
    "cost_analysis.py": _cost.__file__, "drift_classification.py": _drift.__file__,
    "shadow_consistency.py": _consistency.__file__, "shadow_ranges.py": _ranges.__file__,
}

VALID, INVALID = "VALID", "INVALID"
COMPLETE, PARTIAL, INSUFFICIENT = "COMPLETE", "PARTIAL", "INSUFFICIENT"
PASSED, FAILED = "PASSED", "FAILED"
ACT_COMPLETE = "PHASE4_TECHNICALLY_COMPLETE"
ACT_NEEDS_MORE_DATA = "NEEDS_MORE_DATA"
ACT_DRIFT = "DRIFT_REVIEW_REQUIRED"
ACT_WARNING = "WARNING_REVIEW_REQUIRED"
ACT_INVALID = "INVALID"
PASS, FAIL, NOT_EVALUATED = "PASS", "FAIL", "NOT_EVALUATED"

CHECK_IDS = (
    "DEPENDENCY_CONTRACT", "EXPECTED_BINDINGS_SCHEMA", "INPUT_STRUCTURE", "ARTIFACT_PRESENCE",
    "JOURNAL_VERIFICATION", "STAGE2A_REPORT_SET", "AUTHORITATIVE_SESSION", "IDENTITY_BINDING",
    "WP2B1_REPRODUCTION", "WP2B2_REPRODUCTION", "WP2B3_REPRODUCTION", "SAFETY_INVARIANTS",
    "EXPECTED_BINDING_MATCH", "DETERMINISTIC_DOUBLE_BUILD", "WP2B3_STATUS_MAPPING",
)
_CHECK_STATUSES = (PASS, FAIL, NOT_EVALUATED)

EXPECTED_BINDINGS_FIELDS = (
    "forward_manifest_hash", "forward_journal_final_hash",
    "stage2a_report_hash", "stage2a_json_sha256", "stage2a_markdown_sha256",
    "wp2b1_pipeline_hash", "wp2b2_result_hash", "wp2b2_session_result_hash",
    "wp2b3_report_hash", "wp2b3_economic_content_hash",
    "wp2b3_dependency_contract_hash", "wp2b4_dependency_contract_hash",
    "reference_manifest_hash", "reference_journal_final_hash",
    "reference_dataset_hash", "forward_dataset_hash",
    "expected_baseline_hash", "expected_threshold_policy_hash", "threshold_policy_hash",
)  # 19

_TOP_KEYS = frozenset((
    "method", "version", "schema_version", "validity_status", "evidence_status", "phase4_gate_status",
    "action_status", "reason_code", "checks", "reference_identity", "forward_identity", "stage2a",
    "upstream", "dependency_contract_hash_v4", "expected_bindings_hash", "wp2b3_summary", "safety",
    "session_id", "limitations", "final_evidence_hash",
))

# closed WP2B-3 vocabularies (Finding 3)
_WP3_VALIDITY = ("VALID", "INVALID")
_WP3_EVIDENCE = ("SUFFICIENT", "PARTIAL", "INSUFFICIENT")
_WP3_DRIFT = ("WITHIN_THRESHOLD", "WARNING", "DRIFT", "NOT_EVALUATED")
_WP3_ACTION = ("WITHIN_THRESHOLD", "WARNING", "DRIFT", "NEEDS_MORE_DATA", "INVALID")

# F4 (HARDENING R2): complete WP2B-3 cross-field status semantics as a closed allowlist of the ONLY
# (validity, evidence, drift, action) tuples the frozen WP2B-3 classifier can emit. Any tuple not in
# this set is a contradiction and is rejected (fail-closed). The set is derived directly from the
# frozen classifier's action-derivation logic (drift_classification.py):
#   - INVALID reports: (INVALID, INSUFFICIENT, NOT_EVALUATED, INVALID).
#   - action WITHIN_THRESHOLD  <=  no DRIFT, no NOT_EVALUATED driver, no WARNING  => evidence SUFFICIENT,
#                                  drift WITHIN_THRESHOLD.
#   - action WARNING           <=  no DRIFT, no NOT_EVALUATED driver, >=1 WARNING => evidence SUFFICIENT,
#                                  drift WARNING.
#   - action DRIFT             <=  >=1 DRIFT driver => drift DRIFT, evidence SUFFICIENT (n_not==0) OR
#                                  PARTIAL (some NOT_EVALUATED alongside the DRIFT driver). Evidence can
#                                  never be INSUFFICIENT because a DRIFT driver means n_eval>=1. This is
#                                  the frozen "DRIFT-over-insufficient" rule: DRIFT is retained even when
#                                  evidence is only PARTIAL. (Documented deviation from the literal
#                                  finding text "DRIFT requires SUFFICIENT" — the frozen classifier is
#                                  authoritative and also emits DRIFT+PARTIAL.)
#   - action NEEDS_MORE_DATA   <=  no DRIFT, >=1 NOT_EVALUATED driver => evidence PARTIAL (n_eval>0) or
#                                  INSUFFICIENT (n_eval==0); drift WARNING (some WARNING driver present)
#                                  or NOT_EVALUATED. Never drift WITHIN_THRESHOLD.
# Every one of the required adversarial contradictions (drift=DRIFT/action=WITHIN_THRESHOLD,
# drift=WITHIN_THRESHOLD/action=DRIFT, drift=WARNING/action=WITHIN_THRESHOLD,
# evidence=SUFFICIENT/action=NEEDS_MORE_DATA, evidence=INSUFFICIENT/action=WITHIN_THRESHOLD) is absent
# from this set and is therefore rejected.
_WP3_REACHABLE = frozenset({
    ("INVALID", "INSUFFICIENT", "NOT_EVALUATED", "INVALID"),
    ("VALID", "SUFFICIENT", "WITHIN_THRESHOLD", "WITHIN_THRESHOLD"),
    ("VALID", "SUFFICIENT", "WARNING", "WARNING"),
    ("VALID", "SUFFICIENT", "DRIFT", "DRIFT"),
    ("VALID", "PARTIAL", "DRIFT", "DRIFT"),
    ("VALID", "PARTIAL", "WARNING", "NEEDS_MORE_DATA"),
    ("VALID", "PARTIAL", "NOT_EVALUATED", "NEEDS_MORE_DATA"),
    ("VALID", "INSUFFICIENT", "NOT_EVALUATED", "NEEDS_MORE_DATA"),
})

_LIMITATIONS = (
    "WP2B-4 implements no independent economic formulas. It invokes the frozen public builders to "
    "reproduce upstream artifacts and compares their hashes and outputs against independently pinned "
    "expected bindings.",
    "The report set is self-verifying and tamper-evident only when compared with independently pinned "
    "expected hashes. An actor able to rewrite the JSON, Markdown, marker AND the external pin store can "
    "create a new internally consistent set. WP2B-4 does not provide signatures, trusted timestamps or "
    "cryptographic authentication.",
    "WP2B-4 cannot determine who approved the supplied expected bindings or whether independent human "
    "review occurred. Independent approval is an operational control outside this module.",
    "PHASE4_TECHNICALLY_COMPLETE attests only to artifact presence, hash-validity, identity-consistency "
    "against independently pinned bindings, and a TEST-mode drift diagnostic with no adverse deviation at "
    "sufficient evidence. It is NOT strategy profitability, statistical robustness, out-of-sample success, "
    "production readiness, live-trading safety, Phase-5 validation, or holdout approval.",
    "TEST-mode only; production classification is mechanically disabled (empty approval allowlist); no "
    "result enables release or live execution. Single-segment whole-journal sessions only.",
    "Directory fsync is best-effort: recognized-unsupported platforms proceed with file fsync + atomic "
    "replace + marker-last; this never changes artifact bytes and no extra persistent file is written.",
)

_EQUAL_FIELDS = ("strategy_hash", "param_hash", "asset", "instrument_type", "timeframe",
                 "execution_profile_hash", "risk_limits_hash", "initial_capital")


class Phase4GateError(Exception):
    """Structural misuse / overwrite refusal / unconstructable report. Semantic failures are a FAILED gate."""


class _JournalVerifyError(Exception):
    pass


class _InjectedFault(Exception):
    pass


# --------------------------------------------------------------------------- #
# small helpers                                                               #
# --------------------------------------------------------------------------- #
def _is_pathlike(x) -> bool:
    return isinstance(x, (str, bytes, os.PathLike))


def _is_hex64(s) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _dependency_contract_hash() -> str:
    return _sha(WP2B4_DEPENDENCY_CONTRACT)


def _verify_dependency_contract() -> Optional[str]:
    for key, path in _DEP_FILES.items():
        try:
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return "DEPENDENCY_CONTRACT_MISMATCH"
        if digest != WP2B4_DEPENDENCY_CONTRACT[key]:
            return "DEPENDENCY_CONTRACT_MISMATCH"
    return None


def _manifest_hash(manifest) -> Optional[str]:
    if not isinstance(manifest, dict):
        return None
    mh = manifest.get("manifest_hash")
    return mh if (isinstance(mh, str) and mh) else None


def _read_events(path, manifest_hash):
    from app.paper.session.journal import read_verified_events, JournalError
    try:
        return read_verified_events(path, manifest_hash)
    except JournalError as exc:
        raise _JournalVerifyError(str(exc))


def _journal_final_hash(events) -> str:
    return events[-1]["event_hash"]


def _bar_open_times(events) -> List[int]:
    return sorted(int(e["payload"]["open_time"]) for e in events if e["event_type"] == "bar_ingested")


def _period_bounds(events, tf_ms) -> Tuple[Optional[int], Optional[int]]:
    ots = _bar_open_times(events)
    if not ots or not _is_int(tf_ms):
        return None, None
    return ots[0], ots[-1] + tf_ms


def _validate_periods(rs, rc, fs, fe) -> Optional[str]:
    if not (_is_int(rs) and _is_int(rc) and _is_int(fs) and _is_int(fe)):
        return "BINDING_MISMATCH"
    if not (rs < rc and fs < fe):
        return "BINDING_MISMATCH"
    if fs < rc:                     # forward must start at or after the reference cutoff
        return "BINDING_MISMATCH"
    return None


def _live_false(events) -> bool:
    for e in events:
        if e["event_type"] == "session_created":
            return e["payload"].get("live_execution_enabled") is False
    return False


def _validate_wp3_vocab(wp3) -> Optional[str]:
    """Validate the complete closed WP2B-3 vocabulary + documented combinations (Finding 3)."""
    if not isinstance(wp3, dict):
        return "METRIC_INVALID"
    v, ev, dr, ac = (wp3.get("validity_status"), wp3.get("evidence_status"),
                     wp3.get("drift_status"), wp3.get("action_status"))
    if v not in _WP3_VALIDITY or ev not in _WP3_EVIDENCE or dr not in _WP3_DRIFT or ac not in _WP3_ACTION:
        return "METRIC_INVALID"
    # F4 (HARDENING R2): complete cross-field semantics. The (validity, evidence, drift, action)
    # tuple must be one the frozen WP2B-3 classifier can actually emit. Anything else — including
    # validity/action INVALID disagreement, WITHIN_THRESHOLD without SUFFICIENT evidence, a drift
    # verdict contradicting the action, or NEEDS_MORE_DATA with SUFFICIENT evidence — is a
    # contradiction and is rejected fail-closed.
    if (v, ev, dr, ac) not in _WP3_REACHABLE:
        return "METRIC_INVALID"
    return None


def _identity_block(manifest: Dict, journal_final_hash: str, dataset_hash: str,
                    period_start_ms, period_end_ms) -> Dict:
    strat = manifest.get("strategy", {}) if isinstance(manifest.get("strategy"), dict) else {}
    return {
        "manifest_hash": manifest.get("manifest_hash"), "journal_final_hash": journal_final_hash,
        "dataset_hash": dataset_hash, "data_source": manifest.get("data_source"),
        "period_start_ms": period_start_ms, "period_end_ms": period_end_ms,
        "strategy_hash": strat.get("strategy_hash"), "param_hash": manifest.get("param_hash"),
        "asset": manifest.get("asset"), "instrument_type": manifest.get("instrument_type"),
        "timeframe": manifest.get("timeframe"),
        "execution_profile_hash": manifest.get("execution_profile_hash"),
        "risk_limits_hash": manifest.get("absolute_risk_limits_hash"),
        "initial_capital": manifest.get("initial_capital"),
    }


# --------------------------------------------------------------------------- #
# read-only candidate generation (grants no approval)                          #
# --------------------------------------------------------------------------- #
def build_phase4_expected_bindings_candidate(session_dir, manifest, segment_policy, drift_inputs) -> Dict:
    """Read-only: recompute the 19 expected-binding hashes for external pinning. approved=False; the final
    gate NEVER calls this internally, and never writes/replaces the supplied expected_bindings."""
    from app.paper.session.reporting import load_report, report_paths
    jpath = os.path.join(str(session_dir), "journal.jsonl")
    mh = _manifest_hash(manifest)
    if mh is None:
        raise Phase4GateError("manifest missing manifest_hash")
    events = _read_events(jpath, mh)
    jfh = _journal_final_hash(events)
    verified = load_report(str(session_dir))
    if verified is None:
        raise Phase4GateError("candidate: Stage-2A report set is not self-consistent")
    jbytes = open(report_paths(str(session_dir))[0], "rb").read()
    mbytes = open(report_paths(str(session_dir))[1], "rb").read()
    seg = build_segment_report_from_journal(jpath, segment_policy, manifest)
    cost = build_cost_analysis_from_journal(jpath, segment_policy, manifest)
    di = drift_inputs
    wp3 = classify_drift_from_journals(
        di["reference_journal_path"], di["forward_journal_path"], di["reference_manifest"],
        di["forward_manifest"], di["reference_segment_policy"], di["forward_segment_policy"],
        di["threshold_policy"], di["expected_baseline_hash"], di["expected_threshold_policy_hash"],
        di.get("classification_mode", "TEST"))
    ref_events = _read_events(di["reference_journal_path"], _manifest_hash(di["reference_manifest"]))
    cand = {
        "forward_manifest_hash": mh, "forward_journal_final_hash": jfh,
        "stage2a_report_hash": verified.get("report_hash"),
        "stage2a_json_sha256": hashlib.sha256(jbytes).hexdigest(),
        "stage2a_markdown_sha256": hashlib.sha256(mbytes).hexdigest(),
        "wp2b1_pipeline_hash": seg.get("pipeline_hash"), "wp2b2_result_hash": cost.get("result_hash"),
        "wp2b2_session_result_hash": cost.get("session", {}).get("session_result_hash"),
        "wp2b3_report_hash": wp3.get("report_hash"), "wp2b3_economic_content_hash": wp3.get("economic_content_hash"),
        "wp2b3_dependency_contract_hash": wp3.get("dependency_contract_hash"),
        "wp2b4_dependency_contract_hash": _dependency_contract_hash(),
        "reference_manifest_hash": _manifest_hash(di["reference_manifest"]),
        "reference_journal_final_hash": _journal_final_hash(ref_events),
        "reference_dataset_hash": di["reference_manifest"].get("dataset_hash"),
        "forward_dataset_hash": manifest.get("dataset_hash"),
        "expected_baseline_hash": di["expected_baseline_hash"],
        "expected_threshold_policy_hash": di["expected_threshold_policy_hash"],
        "threshold_policy_hash": _sha(di["threshold_policy"]),
    }
    return {"expected_bindings_candidate": cand, "approved": False,
            "note": "candidate only — requires independent external review before use as expected_bindings"}


# --------------------------------------------------------------------------- #
# crash-safe publication (exactly three artifacts) + best-effort dir fsync      #
# --------------------------------------------------------------------------- #
def _fsync_dir(path: str) -> None:
    """Best-effort directory fsync. Recognized-unsupported cases return silently; everything else raises to
    abort publication. Every opened descriptor is closed in a finally block."""
    if not hasattr(os, "O_DIRECTORY"):
        return
    dfd = None
    try:
        dfd = os.open(path, os.O_DIRECTORY)
        os.fsync(dfd)
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP), errno.ENOSYS,
                         errno.EINVAL):
            return
        raise
    finally:
        if dfd is not None:
            os.close(dfd)


def _fsync_write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def _canonical_json(obj) -> str:
    return canonical_bytes(obj).decode("utf-8")


def _artifacts_present(output_dir: str) -> bool:
    return any(os.path.exists(os.path.join(output_dir, n))
               for n in (JSON_NAME, MD_NAME, MARKER_NAME, STAGING_DIR))


def _recover_cleanup(output_dir: str) -> None:
    """Remove ONLY the known WP2B-4 artifacts + staging remnants; never touch unrelated files."""
    for n in (MARKER_NAME, JSON_NAME, MD_NAME):
        p = os.path.join(output_dir, n)
        if os.path.exists(p):
            os.remove(p)
    staging = os.path.join(output_dir, STAGING_DIR)
    if os.path.isdir(staging):
        for name in os.listdir(staging):
            os.remove(os.path.join(staging, name))
        os.rmdir(staging)


def _write(report: Dict, output_dir: str, *, recover: bool = False, _fault: Optional[str] = None) -> None:
    """Overwrite/recovery contract (Finding 6): idempotent identical set; refuse different session or
    different content; refuse incomplete/tampered set unless recover=True (then clean only WP2B-4 files)."""
    os.makedirs(output_dir, exist_ok=True)
    feh, sid = report["final_evidence_hash"], report["session_id"]
    mh = report["forward_identity"].get("manifest_hash")
    existing = load_phase4_final_gate(output_dir)
    if existing is not None:
        e_sid = existing.get("session_id")
        e_mh = existing.get("forward_identity", {}).get("manifest_hash")
        if e_sid != sid or e_mh != mh:
            raise Phase4GateError("refusing to overwrite a different session's gate report")
        if existing.get("final_evidence_hash") != feh:
            raise Phase4GateError("refusing to overwrite: same session, different gate content")
        return  # identical complete set -> idempotent no-op
    if _artifacts_present(output_dir):
        if not recover:
            raise Phase4GateError("existing gate report set is incomplete or tampered")
        _recover_cleanup(output_dir)
    _publish(report, output_dir, _fault=_fault)


def _publish(report: Dict, output_dir: str, *, _fault: Optional[str] = None) -> None:
    json_text = _canonical_json(report) + "\n"
    md_text = _render_markdown(report)
    json_sha = hashlib.sha256(json_text.encode("utf-8")).hexdigest()
    md_sha = hashlib.sha256(md_text.encode("utf-8")).hexdigest()
    staging = os.path.join(output_dir, STAGING_DIR)
    os.makedirs(staging, exist_ok=True)
    jtmp, mtmp = os.path.join(staging, JSON_NAME), os.path.join(staging, MD_NAME)
    jpath, mpath = os.path.join(output_dir, JSON_NAME), os.path.join(output_dir, MD_NAME)
    marker = os.path.join(output_dir, MARKER_NAME)

    if os.path.exists(marker):
        os.remove(marker)
    _fsync_write(jtmp, json_text)
    if _fault == "after_json_stage":
        raise _InjectedFault("after_json_stage")
    _fsync_write(mtmp, md_text)
    if _fault == "after_md_stage":
        raise _InjectedFault("after_md_stage")
    os.replace(jtmp, jpath)
    if _fault == "dir_after_json":
        raise _InjectedFault("dir_after_json")
    _fsync_dir(output_dir)
    if _fault == "between_publish":
        raise _InjectedFault("between_publish")
    os.replace(mtmp, mpath)
    if _fault == "dir_after_md":
        raise _InjectedFault("dir_after_md")
    _fsync_dir(output_dir)

    marker_body = _canonical_json({
        "schema_version": SCHEMA_VERSION, "final_evidence_hash": report.get("final_evidence_hash"),
        "json_sha256": json_sha, "markdown_sha256": md_sha,
        "manifest_hash": report.get("forward_identity", {}).get("manifest_hash"),
        "session_id": report.get("session_id"),
    }) + "\n"
    # Any failure AFTER the marker is created must remove it so no valid completion marker survives
    # (Finding 5). Cleanup is best-effort and never hides the original exception.
    try:
        _fsync_write(marker, marker_body)
        if _fault == "marker_fsync":
            raise _InjectedFault("marker_fsync")
        if _fault == "dir_final":
            raise _InjectedFault("dir_final")
        _fsync_dir(output_dir)
    except BaseException:
        try:
            if os.path.exists(marker):
                os.remove(marker)
        except OSError:
            pass
        raise
    try:
        os.rmdir(staging)
    except OSError:
        pass


def load_phase4_final_gate(output_dir: str) -> Optional[Dict]:
    """Return the gate report ONLY if the complete, self-consistent, identity-bound set is present."""
    jpath, mpath = os.path.join(output_dir, JSON_NAME), os.path.join(output_dir, MD_NAME)
    marker = os.path.join(output_dir, MARKER_NAME)
    if not (os.path.exists(marker) and os.path.exists(jpath) and os.path.exists(mpath)):
        return None
    try:
        import json as _json
        m = _json.loads(open(marker, "rb").read().decode("utf-8"))
        json_bytes = open(jpath, "rb").read()
        md_bytes = open(mpath, "rb").read()
        rep = _json.loads(json_bytes.decode("utf-8"))
    except Exception:
        return None
    if not (isinstance(rep, dict) and isinstance(m, dict)):
        return None
    if hashlib.sha256(json_bytes).hexdigest() != m.get("json_sha256"):
        return None
    if hashlib.sha256(md_bytes).hexdigest() != m.get("markdown_sha256"):
        return None
    body = {k: v for k, v in rep.items() if k != "final_evidence_hash"}
    if _sha(body) != rep.get("final_evidence_hash") or rep.get("final_evidence_hash") != m.get("final_evidence_hash"):
        return None
    # marker identity fields (Finding 4)
    if m.get("manifest_hash") != rep.get("forward_identity", {}).get("manifest_hash"):
        return None
    if m.get("session_id") != rep.get("session_id"):
        return None
    if m.get("schema_version") != rep.get("schema_version") or rep.get("schema_version") != SCHEMA_VERSION:
        return None
    # persisted schema integrity (Finding 4)
    if set(rep.keys()) != set(_TOP_KEYS):
        return None
    if rep.get("phase4_gate_status") not in (PASSED, FAILED):
        return None
    checks = rep.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CHECK_IDS):
        return None
    for c, cid in zip(checks, CHECK_IDS):
        if not isinstance(c, dict) or set(c.keys()) != {"check_id", "status"}:
            return None
        if c["check_id"] != cid or c["status"] not in _CHECK_STATUSES:
            return None
    return rep


def _render_markdown(rep: Dict) -> str:
    L: List[str] = []
    A = L.append
    A("# Phase 4 Final Gate")
    A("")
    for k in ("method", "version", "schema_version", "validity_status", "evidence_status",
              "phase4_gate_status", "action_status", "reason_code"):
        A(f"- {k}: {rep[k]}")
    s = rep["safety"]
    for k in ("release_eligible", "live_execution_eligible", "live_execution_enabled",
              "production_allowlist_empty"):
        A(f"- {k}: {s[k]}")
    A("")
    A("## Checks")
    for c in rep["checks"]:
        A(f"- {c['check_id']}: {c['status']}")
    A("")
    A("## Limitations")
    for lim in rep["limitations"]:
        A(f"- {lim}")
    A("")
    A(f"- final_evidence_hash: {rep['final_evidence_hash']}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# report assembly + decision                                                  #
# --------------------------------------------------------------------------- #
def _blank_checks() -> Dict[str, str]:
    return {cid: NOT_EVALUATED for cid in CHECK_IDS}


def _assemble_report(checks: Dict[str, str], validity, evidence, gate, action, reason,
                     bodyfields: Dict, session_id) -> Dict:
    rep = {
        "method": METHOD, "version": VERSION, "schema_version": SCHEMA_VERSION,
        "validity_status": validity, "evidence_status": evidence,
        "phase4_gate_status": gate, "action_status": action, "reason_code": reason,
        "checks": [{"check_id": cid, "status": checks[cid]} for cid in CHECK_IDS],
        "reference_identity": bodyfields.get("reference_identity", {}),
        "forward_identity": bodyfields.get("forward_identity", {}),
        "stage2a": bodyfields.get("stage2a", {}), "upstream": bodyfields.get("upstream", {}),
        "dependency_contract_hash_v4": _dependency_contract_hash(),
        "expected_bindings_hash": bodyfields.get("expected_bindings_hash"),
        "wp2b3_summary": bodyfields.get("wp2b3_summary", {}),
        "safety": {"live_execution_enabled": False, "release_eligible": False,
                   "live_execution_eligible": False, "production_allowlist_empty": True},
        "session_id": session_id, "limitations": list(_LIMITATIONS),
    }
    body = {k: v for k, v in rep.items() if k != "final_evidence_hash"}
    rep["final_evidence_hash"] = _sha(body)
    return rep


def _failed(checks, reason, bodyfields, session_id):
    return _assemble_report(checks, INVALID, INSUFFICIENT, FAILED, ACT_INVALID, reason, bodyfields, session_id)


def _decide(wp3: Dict, checks: Dict, bodyfields: Dict, sid) -> Dict:
    """Map a VOCAB-VALIDATED WP2B-3 result to the gate outcome (no silent fallback)."""
    v, a, ev = wp3.get("validity_status"), wp3.get("action_status"), wp3.get("evidence_status")
    if v == "INVALID":
        return _assemble_report(checks, INVALID, INSUFFICIENT, FAILED, ACT_INVALID, "WP2B3_INVALID",
                                bodyfields, sid)
    if a == "DRIFT":
        return _assemble_report(checks, VALID, COMPLETE, FAILED, ACT_DRIFT, "WP2B3_DRIFT", bodyfields, sid)
    if a == "WARNING":
        return _assemble_report(checks, VALID, COMPLETE, FAILED, ACT_WARNING, "WP2B3_WARNING", bodyfields, sid)
    if a == "NEEDS_MORE_DATA":
        return _assemble_report(checks, VALID, PARTIAL, FAILED, ACT_NEEDS_MORE_DATA,
                                "WP2B3_NEEDS_MORE_DATA", bodyfields, sid)
    # a == "WITHIN_THRESHOLD" with SUFFICIENT evidence (guaranteed by vocab validation)
    return _assemble_report(checks, VALID, COMPLETE, PASSED, ACT_COMPLETE, None, bodyfields, sid)


# --------------------------------------------------------------------------- #
# evaluation core (steps 7–15) — no WP2B builder runs before its frozen step    #
# --------------------------------------------------------------------------- #
def _gate_core(ctx: Dict, *, _fault: Optional[str] = None) -> Dict:
    checks = ctx["checks"]
    eb = ctx["expected_bindings"]
    man = ctx["forward_manifest"]
    rep2a = ctx["verified_stage2a"]
    jfh = ctx["journal_final_hash"]
    di = ctx["drift_inputs"]
    ref_man = di["reference_manifest"]
    bodyfields: Dict = {}
    sid = ctx.get("session_id")

    def fail(cid, reason):
        checks[cid] = FAIL
        return _failed(checks, reason, bodyfields, sid)

    # ----- step 7 AUTHORITATIVE_SESSION (verified Stage-2A + independent journal re-verify) -----
    fman_di = di["forward_manifest"]
    try:
        di_fwd_events = _read_events(di["forward_journal_path"], _manifest_hash(fman_di))
        ref_events = _read_events(di["reference_journal_path"], _manifest_hash(ref_man))
    except _JournalVerifyError:
        return fail("AUTHORITATIVE_SESSION", "AUTHORITATIVE_SESSION_MISMATCH")
    di_fwd_jfh = _journal_final_hash(di_fwd_events)
    ref_jfh = _journal_final_hash(ref_events)
    if (rep2a.get("session_id") != man.get("session_id")
            or rep2a.get("manifest_hash") != man.get("manifest_hash")
            or rep2a.get("final_event_hash") != jfh
            or rep2a.get("journal_verified") is not True
            or _manifest_hash(fman_di) != _manifest_hash(man)
            or ctx["forward_segment_policy"] != di["forward_segment_policy"]
            or di_fwd_jfh != jfh
            or ref_jfh == jfh):
        return fail("AUTHORITATIVE_SESSION", "AUTHORITATIVE_SESSION_MISMATCH")
    checks["AUTHORITATIVE_SESSION"] = PASS

    # ----- step 8 IDENTITY_BINDING (dataset concrete + equal fields + temporal periods) -----
    tf_ref = di["reference_segment_policy"].get("timeframe_ms") if isinstance(di["reference_segment_policy"], dict) else None
    tf_fwd = di["forward_segment_policy"].get("timeframe_ms") if isinstance(di["forward_segment_policy"], dict) else None
    if not _is_int(tf_ref) or tf_ref != tf_fwd:
        return fail("IDENTITY_BINDING", "BINDING_MISMATCH")
    rs, rc = _period_bounds(ref_events, tf_ref)
    fs, fe = _period_bounds(di_fwd_events, tf_fwd)
    per = _validate_periods(rs, rc, fs, fe)
    if per:
        return fail("IDENTITY_BINDING", per)
    # F1 (HARDENING R2): step 8 validates only that each MANIFEST dataset_hash is present and a
    # concrete lowercase hex64 value, and (below) that the identity blocks are built from the
    # manifest dataset_hash. It does NOT compare the manifest dataset hashes against the
    # expected_bindings reference/forward_dataset_hash — that external-pin comparison belongs
    # exclusively to step 13 EXPECTED_BINDING_MATCH (recomputed dict, lines below), so a valid but
    # mismatched pinned dataset hash surfaces as EXPECTED_BINDING_MISMATCH after safety runs.
    ref_ds, fwd_ds = ref_man.get("dataset_hash"), man.get("dataset_hash")
    if not (_is_hex64(ref_ds) and _is_hex64(fwd_ds)):
        return fail("IDENTITY_BINDING", "DATASET_IDENTITY_INVALID")
    rstrat = ref_man.get("strategy", {}) if isinstance(ref_man.get("strategy"), dict) else {}
    fstrat = man.get("strategy", {}) if isinstance(man.get("strategy"), dict) else {}
    ref_eq = {"strategy_hash": rstrat.get("strategy_hash"), "param_hash": ref_man.get("param_hash"),
              "asset": ref_man.get("asset"), "instrument_type": ref_man.get("instrument_type"),
              "timeframe": ref_man.get("timeframe"),
              "execution_profile_hash": ref_man.get("execution_profile_hash"),
              "risk_limits_hash": ref_man.get("absolute_risk_limits_hash"),
              "initial_capital": ref_man.get("initial_capital")}
    fwd_eq = {"strategy_hash": fstrat.get("strategy_hash"), "param_hash": man.get("param_hash"),
              "asset": man.get("asset"), "instrument_type": man.get("instrument_type"),
              "timeframe": man.get("timeframe"),
              "execution_profile_hash": man.get("execution_profile_hash"),
              "risk_limits_hash": man.get("absolute_risk_limits_hash"),
              "initial_capital": man.get("initial_capital")}
    if any(ref_eq[k] != fwd_eq[k] for k in _EQUAL_FIELDS):
        return fail("IDENTITY_BINDING", "BINDING_MISMATCH")
    if not _finite(man.get("initial_capital")) or float(man.get("initial_capital")) <= 0:
        return fail("IDENTITY_BINDING", "BINDING_MISMATCH")
    checks["IDENTITY_BINDING"] = PASS

    # ----- step 9 WP2B1_REPRODUCTION (build here; catch only its documented exception) -----
    try:
        seg = ctx["build_wp2b1"]()
    except SegmentPipelineError:
        return fail("WP2B1_REPRODUCTION", "UPSTREAM_HASH_MISMATCH")
    if len(seg.get("segments", [])) != 1 or seg["segments"][0].get("status") != "OK":
        return fail("WP2B1_REPRODUCTION", "UPSTREAM_HASH_MISMATCH")
    checks["WP2B1_REPRODUCTION"] = PASS

    # ----- step 10 WP2B2_REPRODUCTION -----
    try:
        cost = ctx["build_wp2b2"]()
    except CostAnalysisError:
        return fail("WP2B2_REPRODUCTION", "UPSTREAM_HASH_MISMATCH")
    if len(cost.get("segments", [])) != 1:
        return fail("WP2B2_REPRODUCTION", "UPSTREAM_HASH_MISMATCH")
    checks["WP2B2_REPRODUCTION"] = PASS

    # ----- step 11 WP2B3_REPRODUCTION -----
    try:
        wp3 = classify_drift_from_journals(
            di["reference_journal_path"], di["forward_journal_path"], di["reference_manifest"],
            di["forward_manifest"], di["reference_segment_policy"], di["forward_segment_policy"],
            di["threshold_policy"], di["expected_baseline_hash"], di["expected_threshold_policy_hash"],
            di.get("classification_mode", "TEST"))
    except DriftClassificationError:
        return fail("WP2B3_REPRODUCTION", "UPSTREAM_HASH_MISMATCH")
    if wp3.get("validity_status") != "INVALID" and wp3.get("forward_source_journal_final_hash") != jfh:
        return fail("WP2B3_REPRODUCTION", "AUTHORITATIVE_SESSION_MISMATCH")
    checks["WP2B3_REPRODUCTION"] = PASS

    bodyfields["reference_identity"] = _identity_block(ref_man, ref_jfh, ref_ds, rs, rc)
    bodyfields["forward_identity"] = _identity_block(man, jfh, fwd_ds, fs, fe)
    bodyfields["stage2a"] = {"report_hash": rep2a.get("report_hash"),
                             "json_sha256": ctx["stage2a_json_sha256"],
                             "markdown_sha256": ctx["stage2a_markdown_sha256"]}
    bodyfields["upstream"] = {
        "wp2b1_pipeline_hash": seg.get("pipeline_hash"), "wp2b2_result_hash": cost.get("result_hash"),
        "wp2b2_session_result_hash": cost.get("session", {}).get("session_result_hash"),
        "wp2b3_report_hash": wp3.get("report_hash"), "wp2b3_economic_content_hash": wp3.get("economic_content_hash"),
        "wp2b3_dependency_contract_hash": wp3.get("dependency_contract_hash")}
    bodyfields["expected_bindings_hash"] = _sha(eb)
    bodyfields["wp2b3_summary"] = {"validity_status": wp3.get("validity_status"),
                                   "evidence_status": wp3.get("evidence_status"),
                                   "drift_status": wp3.get("drift_status"),
                                   "action_status": wp3.get("action_status")}

    # ----- step 12 SAFETY_INVARIANTS -----
    if not (_live_false(ctx["forward_events"]) and _live_false(ref_events)):
        return fail("SAFETY_INVARIANTS", "LIVE_EXECUTION_ENABLED")
    if man.get("live_execution_enabled") is not False or ref_man.get("live_execution_enabled") is not False:
        return fail("SAFETY_INVARIANTS", "LIVE_EXECUTION_ENABLED")
    # F3 (HARDENING R2): global live-execution kill switch. Independently of the per-manifest and
    # per-journal live flags above, the authoritative paper-session global must be False. Reading it
    # at runtime (not a snapshot) means any process-wide enablement fails the gate closed here.
    if _psession.LIVE_EXECUTION_ENABLED is not False:
        return fail("SAFETY_INVARIANTS", "LIVE_EXECUTION_ENABLED")
    if wp3.get("release_eligible") is not False or wp3.get("live_execution_eligible") is not False:
        return fail("SAFETY_INVARIANTS", "ELIGIBILITY_VIOLATION")
    # F2 (HARDENING R2): read the allowlist from the authoritative module object at runtime; fail
    # closed whenever it is not exactly the empty tuple ().
    if _drift.APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES != () or di.get("classification_mode", "TEST") != "TEST":
        return fail("SAFETY_INVARIANTS", "PRODUCTION_NOT_SUPPORTED_V4")
    checks["SAFETY_INVARIANTS"] = PASS

    # ----- step 13 EXPECTED_BINDING_MATCH -----
    recomputed = {
        "forward_manifest_hash": _manifest_hash(man), "forward_journal_final_hash": jfh,
        "stage2a_report_hash": rep2a.get("report_hash"),
        "stage2a_json_sha256": ctx["stage2a_json_sha256"], "stage2a_markdown_sha256": ctx["stage2a_markdown_sha256"],
        "wp2b1_pipeline_hash": seg.get("pipeline_hash"), "wp2b2_result_hash": cost.get("result_hash"),
        "wp2b2_session_result_hash": cost.get("session", {}).get("session_result_hash"),
        "wp2b3_report_hash": wp3.get("report_hash"), "wp2b3_economic_content_hash": wp3.get("economic_content_hash"),
        "wp2b3_dependency_contract_hash": wp3.get("dependency_contract_hash"),
        "wp2b4_dependency_contract_hash": _dependency_contract_hash(),
        "reference_manifest_hash": _manifest_hash(ref_man), "reference_journal_final_hash": ref_jfh,
        "reference_dataset_hash": ref_ds, "forward_dataset_hash": fwd_ds,
        "expected_baseline_hash": di["expected_baseline_hash"],
        "expected_threshold_policy_hash": di["expected_threshold_policy_hash"],
        "threshold_policy_hash": _sha(di["threshold_policy"]),
    }
    if eb.get("threshold_policy_hash") != eb.get("expected_threshold_policy_hash"):
        return fail("EXPECTED_BINDING_MATCH", "EXPECTED_BINDING_MISMATCH")
    for k in EXPECTED_BINDINGS_FIELDS:
        if eb.get(k) != recomputed.get(k):
            return fail("EXPECTED_BINDING_MATCH", "EXPECTED_BINDING_MISMATCH")
    checks["EXPECTED_BINDING_MATCH"] = PASS

    # ----- step 14 DETERMINISTIC_DOUBLE_BUILD (two pure in-memory builds) -----
    if _fault == "nondeterministic":
        return fail("DETERMINISTIC_DOUBLE_BUILD", "NONDETERMINISTIC_REPLAY")
    vocab = _validate_wp3_vocab(wp3)

    def build_final(chk_src):
        c = dict(chk_src)
        c["DETERMINISTIC_DOUBLE_BUILD"] = PASS
        if vocab:
            c["WP2B3_STATUS_MAPPING"] = FAIL
            return _assemble_report(c, INVALID, INSUFFICIENT, FAILED, ACT_INVALID, vocab, bodyfields, sid)
        c["WP2B3_STATUS_MAPPING"] = PASS
        return _decide(wp3, c, bodyfields, sid)
    r1, r2 = build_final(checks), build_final(checks)
    if r1["final_evidence_hash"] != r2["final_evidence_hash"]:
        return fail("DETERMINISTIC_DOUBLE_BUILD", "NONDETERMINISTIC_REPLAY")
    checks["DETERMINISTIC_DOUBLE_BUILD"] = PASS

    # ----- step 15 WP2B3_STATUS_MAPPING (closed-vocabulary; no silent fallback) -----
    if vocab:
        checks["WP2B3_STATUS_MAPPING"] = FAIL
        report = _assemble_report(checks, INVALID, INSUFFICIENT, FAILED, ACT_INVALID, vocab, bodyfields, sid)
    else:
        checks["WP2B3_STATUS_MAPPING"] = PASS
        report = _decide(wp3, checks, bodyfields, sid)
    _write(report, ctx["output_dir"], recover=ctx.get("recover", False), _fault=_fault)
    return report


# --------------------------------------------------------------------------- #
# PUBLIC gate                                                                 #
# --------------------------------------------------------------------------- #
def _validate_expected_bindings(eb) -> Optional[str]:
    if not isinstance(eb, dict) or set(eb.keys()) != set(EXPECTED_BINDINGS_FIELDS):
        return "EXPECTED_BINDINGS_MALFORMED"
    for k in EXPECTED_BINDINGS_FIELDS:
        if not _is_hex64(eb.get(k)):
            return "EXPECTED_BINDINGS_MALFORMED"
    return None


def build_phase4_final_gate(session_dir, manifest, segment_policy, drift_inputs, expected_bindings,
                            output_dir, *, recover: bool = False, _fault: Optional[str] = None) -> Dict:
    from app.paper.session.reporting import load_report, report_paths
    checks = _blank_checks()

    dep = _verify_dependency_contract()
    if dep:
        checks["DEPENDENCY_CONTRACT"] = FAIL
        return _failed(checks, dep, {}, None)
    checks["DEPENDENCY_CONTRACT"] = PASS

    ebreason = _validate_expected_bindings(expected_bindings)
    if ebreason:
        checks["EXPECTED_BINDINGS_SCHEMA"] = FAIL
        return _failed(checks, ebreason, {}, None)
    checks["EXPECTED_BINDINGS_SCHEMA"] = PASS

    # step 3 INPUT_STRUCTURE (raise + publish nothing)
    if not _is_pathlike(session_dir) or not _is_pathlike(output_dir):
        raise Phase4GateError("session_dir and output_dir must be paths")
    if not isinstance(drift_inputs, dict) or not isinstance(manifest, dict):
        raise Phase4GateError("manifest and drift_inputs must be objects, not bundles")
    for p in (drift_inputs.get("reference_journal_path"), drift_inputs.get("forward_journal_path")):
        if not _is_pathlike(p):
            raise Phase4GateError("journal paths must be filesystem paths")
    if drift_inputs.get("classification_mode", "TEST") != "TEST":
        raise Phase4GateError("classification_mode must be 'TEST'")
    checks["INPUT_STRUCTURE"] = PASS

    jpath = os.path.join(str(session_dir), "journal.jsonl")
    j2a, m2a = report_paths(str(session_dir))
    marker2a = os.path.join(str(session_dir), "session_report.complete")
    if not (os.path.exists(jpath) and os.path.exists(j2a) and os.path.exists(m2a) and os.path.exists(marker2a)):
        checks["ARTIFACT_PRESENCE"] = FAIL
        return _failed(checks, "ARTIFACT_MISSING", {}, None)
    checks["ARTIFACT_PRESENCE"] = PASS

    mh = _manifest_hash(manifest)
    if mh is None:
        checks["JOURNAL_VERIFICATION"] = FAIL
        return _failed(checks, "JOURNAL_VERIFICATION_FAILED", {}, None)
    try:
        events = _read_events(jpath, mh)
    except _JournalVerifyError:
        checks["JOURNAL_VERIFICATION"] = FAIL
        return _failed(checks, "JOURNAL_VERIFICATION_FAILED", {}, None)
    checks["JOURNAL_VERIFICATION"] = PASS
    jfh = _journal_final_hash(events)

    verified = load_report(str(session_dir))         # step 6: verify BEFORE any field is used
    if verified is None:
        checks["STAGE2A_REPORT_SET"] = FAIL
        return _failed(checks, "REPORT_SET_INVALID", {}, None)
    checks["STAGE2A_REPORT_SET"] = PASS
    j2a_sha = hashlib.sha256(open(j2a, "rb").read()).hexdigest()
    m2a_sha = hashlib.sha256(open(m2a, "rb").read()).hexdigest()

    ctx = {
        "checks": checks, "forward_manifest": manifest, "forward_segment_policy": segment_policy,
        "verified_stage2a": verified, "forward_events": events, "journal_final_hash": jfh,
        "drift_inputs": drift_inputs, "expected_bindings": expected_bindings, "output_dir": str(output_dir),
        "session_id": verified.get("session_id"), "stage2a_json_sha256": j2a_sha,
        "stage2a_markdown_sha256": m2a_sha, "recover": recover,
        "build_wp2b1": lambda: build_segment_report_from_journal(jpath, segment_policy, manifest),
        "build_wp2b2": lambda: build_cost_analysis_from_journal(jpath, segment_policy, manifest),
    }
    return _gate_core(ctx, _fault=_fault)


def _build_phase4_final_gate_from_artifacts(*, verified_stage2a, forward_events, forward_manifest,
                                            forward_segment_policy, wp2b1, wp2b2, drift_inputs,
                                            expected_bindings, output_dir, stage2a_json_sha256,
                                            stage2a_markdown_sha256, recover=False, _fault=None) -> Dict:
    """PRIVATE, TEST-ONLY. Steps 1–6 assumed satisfied by construction (marked PASS); runs 7–15 + publish
    with caller-supplied (crafted) WP2B-1/2 reports. Not public."""
    checks = _blank_checks()
    dep = _verify_dependency_contract()
    if dep:
        checks["DEPENDENCY_CONTRACT"] = FAIL
        return _failed(checks, dep, {}, verified_stage2a.get("session_id"))
    checks["DEPENDENCY_CONTRACT"] = PASS
    ebreason = _validate_expected_bindings(expected_bindings)
    if ebreason:
        checks["EXPECTED_BINDINGS_SCHEMA"] = FAIL
        return _failed(checks, ebreason, {}, verified_stage2a.get("session_id"))
    checks["EXPECTED_BINDINGS_SCHEMA"] = PASS
    for cid in ("INPUT_STRUCTURE", "ARTIFACT_PRESENCE", "JOURNAL_VERIFICATION", "STAGE2A_REPORT_SET"):
        checks[cid] = PASS
    ctx = {
        "checks": checks, "forward_manifest": forward_manifest, "forward_segment_policy": forward_segment_policy,
        "verified_stage2a": verified_stage2a, "forward_events": forward_events,
        "journal_final_hash": _journal_final_hash(forward_events), "drift_inputs": drift_inputs,
        "expected_bindings": expected_bindings, "output_dir": str(output_dir),
        "session_id": verified_stage2a.get("session_id"), "stage2a_json_sha256": stage2a_json_sha256,
        "stage2a_markdown_sha256": stage2a_markdown_sha256, "recover": recover,
        "build_wp2b1": lambda: wp2b1, "build_wp2b2": lambda: wp2b2,
    }
    return _gate_core(ctx, _fault=_fault)
