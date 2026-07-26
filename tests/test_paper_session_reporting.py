"""Phase 4 Stage 2 — Work Package 2A: safe, deterministic session reporting.

Test-first. These tests are written BEFORE the implementation and must fail against the stub
(NotImplementedError), then pass once reporting.py is implemented. All tests are hermetic:
deterministic synthetic bars (or a fixed real-data slice), fixed seeds, temporary output
directories only. The report is READ-ONLY over the journal and never runs orders or mutates
session state; live execution stays structurally disabled.
"""
from __future__ import annotations

import hashlib
import json
import os

import pandas as pd
import pytest

from app.forward.manifest import canonical_json, sha256_text
from app.strategy_research.strategies.registry import get_strategy
from app.forward.shadow.regime import DEFAULT_REGIME_POLICY, regime_policy_hash
from app.forward.shadow.consistency import DEFAULT_CONSISTENCY_POLICY, consistency_policy_hash
from app.forward.shadow.ranges import build_reference_ranges, freeze_range_policy

from app.paper.session import LIVE_EXECUTION_ENABLED
from app.paper.session.engine import run_replay
from app.paper.session.journal import Journal, verify_journal_file, compute_event_hash
from app.paper.session.manifest import build_session_manifest
from app.paper.session.profiles import freeze_execution_profile
from app.paper.session.risk import freeze_risk_limits
from app.paper.session.ingest import dataset_hash
from app.paper.session.lifecycle import (
    COMPLETED, RISK_STOPPED, DATA_STOPPED, SIGNAL_STOPPED, INVALIDATED)

from app.paper.session import reporting
from app.paper.session.reporting import (
    build_session_report,
    report_paths,
    ReportError,
    REPORT_SCHEMA_VERSION,
    V_COMPLETED,
    V_RISK_STOPPED,
    V_SESSION_INVALIDATED,
    V_REPORT_INVALIDATED,
)

_T0 = 1_600_000_000_000
_DAY = 86_400_000


# --------------------------------------------------------------------------- #
# deterministic fixtures                                                       #
# --------------------------------------------------------------------------- #
def _frame(closes, *, start=_T0, step=_DAY) -> pd.DataFrame:
    closes = [float(c) for c in closes]
    opens = [closes[0]] + closes[:-1]
    rows = []
    for i in range(len(closes)):
        o, c = opens[i], closes[i]
        rows.append({"open_time": start + i * step, "open": o,
                     "high": max(o, c) + 1.0, "low": max(0.01, min(o, c) - 1.0),
                     "close": c, "volume": 1000.0})
    return pd.DataFrame(rows)


def _uptrend(n=30, base=100.0, slope=3.0):
    return _frame([base + slope * i for i in range(n)])


def _mk_manifest(frame, *, risk=None, length=5):
    strat = get_strategy("H-025")
    prof = freeze_execution_profile("BTC/USD")
    rpol = freeze_range_policy(seed=42, block_length=3, n_resamples=100, min_unique_segments=6)
    segs = [{"segment_index": i, "metrics": {
        "return": 0.001 * (i % 7 - 3), "drawdown": -0.02 - 0.001 * (i % 3),
        "trade_count": float(5 + i % 4), "turnover": float(1 + i % 3), "exposure_time": 0.5,
        "win_rate": 0.5 + 0.01 * (i % 3), "avg_holding_period": float(3 + i % 2),
        "long_short_balance": 1.0}} for i in range(24)]
    ref = build_reference_ranges(segs, policy=rpol)
    risk = risk or freeze_risk_limits(max_drawdown=0.90)
    man = build_session_manifest(
        strategy=strat, params={"length": length}, asset="BTC/USD", timeframe="1d",
        data_source={"provider": "synthetic_test", "root": "n/a", "is_real": False},
        start_timestamp_ms=int(frame["open_time"].iloc[0]), initial_capital=100_000.0,
        position_sizing_policy={"type": "target_fraction_of_equity", "max_gross": 1.0},
        execution_profile=prof, absolute_risk_limits=risk,
        gap_stale_policy={"max_gap_bars": 2, "on_gap": "DATA_STOPPED"},
        regime_policy_hash=regime_policy_hash(DEFAULT_REGIME_POLICY),
        consistency_policy_hash=consistency_policy_hash(DEFAULT_CONSISTENCY_POLICY),
        reference_range_hash=ref["range_hash"], comparability_basis=ref["comparability_basis"],
        dataset_hash=dataset_hash(frame))
    return man, prof, strat, risk


def _run(frame, journal_dir, *, risk=None, length=5):
    man, prof, strat, risk = _mk_manifest(frame, risk=risk, length=length)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": length}, frame=frame, journal_dir=str(journal_dir))
    return res, man


def _completed_session(tmp_path):
    frame = _uptrend(30)
    res, man = _run(frame, tmp_path / "j", length=5)
    assert res["lifecycle_status"] == COMPLETED
    return res, man


def _risk_stopped_session(tmp_path):
    closes = [100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0]
    res, man = _run(_frame(closes), tmp_path / "j", length=5,
                    risk=freeze_risk_limits(max_drawdown=0.10))
    assert res["lifecycle_status"] == RISK_STOPPED
    return res, man


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# a good broker snapshot for hand-built custom journals (reconciles: equity == cash)
def _good_snapshot(cap):
    return {"cash": cap, "position_units": 0.0, "avg_entry_price": 0.0, "mark_price": 0.0,
            "equity": cap, "equity_peak": cap, "drawdown": 0.0, "realized_pnl": 0.0,
            "fees_paid": 0.0, "slippage_cost": 0.0, "spread_cost": 0.0, "gross_exposure_frac": 0.0}


def _zero_counts():
    # a journal with only session_created + session_final has zero counted events
    return {"bars": 0, "signals": 0, "orders": 0, "fills": 0, "partials": 0, "rejects": 0,
            "risk_events": 0, "safe_exits": 0, "execution_errors": 0}


def _build_custom_journal(path, manifest, specs):
    """Build a VALID hash-chained journal from (event_type, payload, ts, id) specs, so the
    hash layer verifies but a chosen SEMANTIC field can be wrong (tests the semantic layer)."""
    j = Journal(path, manifest["manifest_hash"])
    for et, payload, ts, eid in specs:
        j.append(et, payload, timestamp_ms=ts, event_id=eid)
    return path


def _default_created_payload(manifest):
    return {"session_id": manifest["session_id"], "manifest_hash": manifest["manifest_hash"],
            "paper_trading_only": True, "live_execution_enabled": False,
            "shorting_allowed": False, "signal_policy_hash": manifest["signal_policy_hash"],
            "regime_policy_hash": manifest["regime_policy_hash"],
            "consistency_policy_hash": manifest["consistency_policy_hash"],
            "reference_range_hash": manifest["reference_range_hash"]}


def _default_final_payload(manifest, *, net_pnl=0.0, final_equity=None):
    cap = float(manifest["initial_capital"])
    fe = cap if final_equity is None else final_equity
    return {"lifecycle_status": COMPLETED, "manifest_hash": manifest["manifest_hash"],
            "absolute_risk": {"breached": False, "breaches": []}, "counts": _zero_counts(),
            "broker": _good_snapshot(cap), "final_equity": fe, "initial_capital": cap,
            "net_pnl": net_pnl, "net_return": net_pnl / cap}


# =========================================================================== #
# NEGATIVE TESTS (fail-closed) — written first                                #
# =========================================================================== #
def test_corrupt_event_hash_is_report_invalidated(tmp_path):
    res, man = _completed_session(tmp_path)
    jp = res["journal_path"]
    lines = open(jp, encoding="utf-8").read().splitlines()
    obj = json.loads(lines[2])           # tamper a payload field WITHOUT recomputing the hash
    obj["payload"]["close"] = 999999.0
    lines[2] = canonical_json(obj)
    open(jp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["exact_reason_code"] == "journal_integrity_failure"
    assert rep["journal_verified"] is False
    assert rep["net_pnl"] is None and rep["gross_pnl"] is None and rep["final_equity"] is None


def test_wrong_manifest_hash_is_report_invalidated(tmp_path):
    res, man = _completed_session(tmp_path)
    wrong = dict(man, manifest_hash="0" * 64)   # journal no longer anchors to this manifest
    rep = build_session_report(manifest=wrong, journal_path=res["journal_path"],
                               output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["journal_verified"] is False
    assert rep["net_pnl"] is None


def test_missing_event_is_report_invalidated(tmp_path):
    res, man = _completed_session(tmp_path)
    jp = res["journal_path"]
    lines = open(jp, encoding="utf-8").read().splitlines()
    del lines[3]                                   # drop a middle event -> non-contiguous seq
    open(jp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["exact_reason_code"] == "journal_integrity_failure"
    assert rep["net_pnl"] is None


def test_duplicated_event_is_report_invalidated(tmp_path):
    res, man = _completed_session(tmp_path)
    jp = res["journal_path"]
    lines = open(jp, encoding="utf-8").read().splitlines()
    lines.insert(3, lines[3])                      # duplicate a line -> non-contiguous seq
    open(jp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["net_pnl"] is None


def test_truncated_journal_without_session_final_is_report_invalidated(tmp_path):
    res, man = _completed_session(tmp_path)
    jp = res["journal_path"]
    lines = open(jp, encoding="utf-8").read().splitlines()
    assert json.loads(lines[-1])["event_type"] == "session_final"
    open(jp, "w", encoding="utf-8").write("\n".join(lines[:-1]) + "\n")   # valid prefix, no final
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["exact_reason_code"] == "incomplete_journal"
    assert rep["net_pnl"] is None


def test_inconsistent_net_pnl_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = str(tmp_path / "j" / "journal.jsonl")
    os.makedirs(os.path.dirname(jp), exist_ok=True)
    # valid hash chain, but net_pnl (999) != final_equity - initial_capital (0)
    _build_custom_journal(jp, man, [
        ("session_created", _default_created_payload(man), _T0, "session_created"),
        ("session_final", _default_final_payload(man, net_pnl=999.0, final_equity=100_000.0),
         _T0 + _DAY, "session_final")])
    assert verify_journal_file(jp, man["manifest_hash"])[0] is True   # chain is intact
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["exact_reason_code"] == "net_pnl_reconciliation_failure"
    assert rep["net_pnl"] is None


def test_non_monotonic_timestamps_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = str(tmp_path / "j" / "journal.jsonl")
    os.makedirs(os.path.dirname(jp), exist_ok=True)
    _build_custom_journal(jp, man, [
        ("session_created", _default_created_payload(man), _T0 + 10 * _DAY, "session_created"),
        ("portfolio_snapshot", {"open_time": _T0, "close": 100.0, **_good_snapshot(100_000.0)},
         _T0, "snap:1"),   # timestamp goes BACKWARDS
        ("session_final", _default_final_payload(man), _T0 + 11 * _DAY, "session_final")])
    assert verify_journal_file(jp, man["manifest_hash"])[0] is True
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["exact_reason_code"] == "timestamp_monotonicity_failure"
    assert rep["net_pnl"] is None


def test_live_execution_enabled_in_journal_is_refused(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = str(tmp_path / "j" / "journal.jsonl")
    os.makedirs(os.path.dirname(jp), exist_ok=True)
    created = dict(_default_created_payload(man), live_execution_enabled=True)   # forbidden
    _build_custom_journal(jp, man, [
        ("session_created", created, _T0, "session_created"),
        ("session_final", _default_final_payload(man), _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["exact_reason_code"] == "live_execution_enabled"
    assert rep["net_pnl"] is None


def test_live_execution_enabled_module_constant_is_refused(tmp_path, monkeypatch):
    res, man = _completed_session(tmp_path)
    monkeypatch.setattr(reporting, "LIVE_EXECUTION_ENABLED", True)
    rep = build_session_report(manifest=man, journal_path=res["journal_path"],
                               output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["exact_reason_code"] == "live_execution_enabled"


def test_output_session_mismatch_refuses_overwrite(tmp_path):
    resA, manA = _completed_session(tmp_path / "A")
    out = str(tmp_path / "out")
    build_session_report(manifest=manA, journal_path=resA["journal_path"], output_dir=out)
    before = _sha(report_paths(out)[0])
    # a DIFFERENT session must not overwrite the existing report directory
    resB, manB = _run(_uptrend(25, base=200.0), tmp_path / "B", length=5)
    assert manB["session_id"] != manA["session_id"]
    with pytest.raises(ReportError):
        build_session_report(manifest=manB, journal_path=resB["journal_path"], output_dir=out)
    assert _sha(report_paths(out)[0]) == before   # existing evidence untouched


# =========================================================================== #
# INTEGRATION: COMPLETED / RISK_STOPPED / INVALIDATED                          #
# =========================================================================== #
def _mandatory_fields():
    return ["report_schema_version", "session_id", "strategy_id", "strategy_hash",
            "manifest_hash", "signal_policy_hash", "asset", "timeframe", "lifecycle_status",
            "verdict", "exact_reason_code", "initial_capital", "final_equity", "gross_pnl",
            "net_pnl", "fees", "spread_cost", "slippage_cost", "funding_cost", "max_drawdown",
            "counts", "final_event_hash", "journal_verified", "live_execution_enabled",
            "report_hash"]


def test_completed_report_has_all_fields_and_reconciles(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    for f in _mandatory_fields():
        assert f in rep, f"missing mandatory field {f}"
    assert rep["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert rep["verdict"] == V_COMPLETED
    assert rep["lifecycle_status"] == COMPLETED
    assert rep["journal_verified"] is True
    assert rep["live_execution_enabled"] is False
    assert rep["session_id"] == man["session_id"]
    assert rep["strategy_id"] == man["strategy"]["strategy_id"]
    assert rep["manifest_hash"] == man["manifest_hash"]
    assert rep["final_event_hash"] == res["journal_summary"]["final_event_hash"]
    # net PnL identity is the authoritative one, taken from the verified journal
    assert abs(rep["net_pnl"] - (rep["final_equity"] - rep["initial_capital"])) < 1e-9
    # gross = net + all frictions added back (costs never re-deducted)
    friction = rep["fees"] + rep["spread_cost"] + rep["slippage_cost"] + rep["funding_cost"]
    assert abs(rep["gross_pnl"] - (rep["net_pnl"] + friction)) < 1e-9
    assert rep["funding_cost"] == 0.0   # Stage 1 broker accrues no funding
    # files exist
    jpath, mpath = report_paths(out)
    assert os.path.exists(jpath) and os.path.exists(mpath)


def test_report_hash_reproduces_from_written_json(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    on_disk = _read_json(report_paths(out)[0])
    assert on_disk == rep
    body = {k: v for k, v in on_disk.items() if k != "report_hash"}
    assert sha256_text(canonical_json(body)) == on_disk["report_hash"]


def test_risk_stopped_report_explains_exact_reason(tmp_path):
    res, man = _risk_stopped_session(tmp_path)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    assert rep["verdict"] == V_RISK_STOPPED
    assert rep["lifecycle_status"] == RISK_STOPPED
    assert rep["why_it_failed"], "risk stop must carry an explanation"
    rules = [b["rule"] for b in rep["why_it_failed"]]
    assert "max_drawdown" in rules
    for b in rep["why_it_failed"]:
        assert set(b) >= {"rule", "observed", "limit"}
    # deterministically sorted (stable order)
    assert rules == sorted(rules)
    assert rep["exact_reason_code"] == sorted(rules)[0]


def test_invalidated_run_without_journal_is_session_invalidated(tmp_path):
    # a manifest drift makes run_replay return INVALIDATED and write NO journal
    frame = _uptrend(10)
    man, prof, strat, risk = _mk_manifest(frame)
    tampered = dict(man, manifest_hash="f" * 64)   # stored hash no longer reproduces
    res = run_replay(manifest=tampered, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": 5}, frame=frame, journal_dir=str(tmp_path / "j"))
    assert res["lifecycle_status"] == INVALIDATED
    assert not os.path.exists(str(tmp_path / "j" / "journal.jsonl"))
    rep = build_session_report(manifest=tampered, journal_path=None, run_result=res,
                               output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_SESSION_INVALIDATED
    assert rep["journal_verified"] is False
    assert rep["net_pnl"] is None
    assert rep["exact_reason_code"]   # a concrete mismatch reason, never blank


def test_report_is_read_only_over_journal_and_confined_to_output(tmp_path):
    res, man = _completed_session(tmp_path)
    jp = res["journal_path"]
    j_before = _sha(jp)
    jdir_before = sorted(os.listdir(os.path.dirname(jp)))
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=jp, output_dir=out)
    assert _sha(jp) == j_before                                  # journal untouched
    assert sorted(os.listdir(os.path.dirname(jp))) == jdir_before
    # a complete report set: json + md + completion marker; no leftover .tmp at the top level
    names = os.listdir(out)
    assert "session_report.json" in names and "session_report.md" in names
    assert "session_report.complete" in names
    assert not any(n.endswith(".tmp") for n in names)


def test_markdown_status_block_is_present(tmp_path):
    res, man = _risk_stopped_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    md = open(report_paths(out)[1], encoding="utf-8").read()
    for token in ["Session", "Strategy", "Status", "Verdict", "Live execution: DISABLED",
                  "Data/journal integrity", "Journal verified"]:
        assert token in md, f"status block missing {token!r}"
    assert man["session_id"] in md
    assert "RISK_STOPPED" in md
    assert "max_drawdown" in md or "Drawdown" in md


# =========================================================================== #
# DETERMINISM: same manifest+journal => byte-identical JSON, MD, report_hash   #
# =========================================================================== #
def _load_btc_slice(n0=300, n1=760):
    path = os.path.join("data_csv", "BTCUSD_1d.csv")
    if not os.path.exists(path):
        pytest.skip("BTCUSD_1d.csv not present")
    from app.strategy_research.providers.registry import get_provider
    from app.strategy_research.providers.base import DataRequest
    df = get_provider("csv", root="data_csv").get(DataRequest(asset="BTC/USD", timeframe="1d")).df
    return df.iloc[n0:n1].reset_index(drop=True)


def test_h025_report_is_byte_identical_across_two_runs(tmp_path):
    frame = _load_btc_slice()
    outs = []
    finals = []
    for tag in ("a", "b"):
        man, prof, strat, risk = _mk_manifest(frame, risk=freeze_risk_limits(max_drawdown=0.90),
                                              length=200)
        res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                         params={"length": 200}, frame=frame, journal_dir=str(tmp_path / f"j{tag}"))
        out = str(tmp_path / f"out{tag}")
        rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
        outs.append((out, rep))
        finals.append(res["journal_summary"]["final_event_hash"])
    (outA, repA), (outB, repB) = outs
    jA, mA = report_paths(outA)
    jB, mB = report_paths(outB)
    assert _sha(jA) == _sha(jB), "session_report.json must be byte-identical"
    assert _sha(mA) == _sha(mB), "session_report.md must be byte-identical"
    assert _sha(_marker_path(outA)) == _sha(_marker_path(outB)), "completion marker must be byte-identical"
    assert repA["report_hash"] == repB["report_hash"]
    assert reporting.load_report(outA) is not None and reporting.load_report(outB) is not None
    assert finals[0] == finals[1]            # underlying journal is deterministic too
    assert repA["final_event_hash"] == finals[0]


# =========================================================================== #
# WP2A HARDENING (REJECT round) — written first, must fail against the current #
# implementation, then pass after hardening.                                   #
# =========================================================================== #
def _hand_chain(path, manifest_hash, raw_events):
    """Write a VALID hash-chained journal from full event dicts (no parent/hash), giving total
    control over field TYPES/VALUES so the type/semantic layer can be tested in isolation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    genesis = sha256_text(canonical_json({"anchor": "manifest", "manifest_hash": manifest_hash}))
    parent = genesis
    lines = []
    for e in raw_events:
        e = dict(e)
        e["parent_hash"] = parent
        e["event_hash"] = compute_event_hash(e)
        parent = e["event_hash"]
        lines.append(canonical_json(e))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _ev(seq, et, payload, ts, eid):
    return {"event_id": eid, "seq": seq, "timestamp": ts, "event_type": et,
            "payload": payload, "parent_hash": None}


def _created_final_chain(path, man, *, final_overrides=None, created_overrides=None):
    created = dict(_default_created_payload(man), **(created_overrides or {}))
    final = dict(_default_final_payload(man), **(final_overrides or {}))
    return _hand_chain(path, man["manifest_hash"], [
        _ev(0, "session_created", created, _T0, "session_created"),
        _ev(1, "session_final", final, _T0 + _DAY, "session_final")])


def _jp(tmp_path, name="journal.jsonl"):
    return str(tmp_path / "j" / name)


def _pnl_all_none(rep):
    return (rep["net_pnl"] is None and rep["gross_pnl"] is None and rep["final_equity"] is None
            and rep["fees"] is None and rep["spread_cost"] is None and rep["slippage_cost"] is None)


# ---- (1) strict finite / type validation ------------------------------------------ #
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, True, "x", [1], {"a": 1}])
def test_net_pnl_non_finite_or_wrong_type_is_report_invalidated(tmp_path, bad):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _created_final_chain(jp, man, final_overrides={"net_pnl": bad})
    assert verify_journal_file(jp, man["manifest_hash"])[0] is True
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert _pnl_all_none(rep)


@pytest.mark.parametrize("field", ["initial_capital", "final_equity", "fees_paid_broker",
                                   "position_units", "mark_price", "cash"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, True, "x"])
def test_economic_fields_non_finite_or_wrong_type_is_report_invalidated(tmp_path, field, bad):
    man, *_ = _mk_manifest(_uptrend(5))
    cap = float(man["initial_capital"])
    jp = _jp(tmp_path)
    fin = _default_final_payload(man)
    if field == "fees_paid_broker":
        fin["broker"] = dict(fin["broker"], fees_paid=bad)
    elif field in ("position_units", "mark_price", "cash"):
        fin["broker"] = dict(fin["broker"], **{field: bad})
    else:
        fin[field] = bad
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "session_final", fin, _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert _pnl_all_none(rep)


@pytest.mark.parametrize("bad_ts", [float("nan"), None, "x", True])
def test_bad_event_timestamp_is_report_invalidated(tmp_path, bad_ts):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "portfolio_snapshot", {"open_time": _T0, "close": 100.0, **_good_snapshot(100_000.0)},
            bad_ts, "snap:1"),
        _ev(2, "session_final", _default_final_payload(man), _T0 + _DAY, "session_final")])
    assert verify_journal_file(jp, man["manifest_hash"])[0] is True
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert _pnl_all_none(rep)


# ---- (2) terminal lifecycle allow-list --------------------------------------------- #
@pytest.mark.parametrize("status", [INVALIDATED, "RUNNING", "VALIDATED", "CREATED", "BOGUS", None, 123])
def test_invalid_terminal_lifecycle_is_report_invalidated(tmp_path, status):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _created_final_chain(jp, man, final_overrides={"lifecycle_status": status})
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED
    # invariant: an invalidated verdict NEVER carries performance numbers
    assert _pnl_all_none(rep)


# ---- (3) structural journal semantics ---------------------------------------------- #
def test_two_session_created_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "session_created", _default_created_payload(man), _T0, "session_created:2"),
        _ev(2, "session_final", _default_final_payload(man), _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_duplicate_event_id_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "portfolio_snapshot", {"open_time": _T0, "close": 100.0, **_good_snapshot(100_000.0)},
            _T0, "dup"),
        _ev(2, "bar_ingested", {"open_time": _T0, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, _T0, "dup"),
        _ev(3, "session_final", _default_final_payload(man), _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


@pytest.mark.parametrize("override", [
    {"paper_trading_only": False},
    {"shorting_allowed": True},
    {"signal_policy_hash": "0" * 64},
    {"manifest_hash": "0" * 64},
])
def test_session_created_semantic_violation_is_report_invalidated(tmp_path, override):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _created_final_chain(jp, man, created_overrides=override)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_final_equity_not_matching_broker_equity_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    cap = float(man["initial_capital"])
    jp = _jp(tmp_path)
    # net_pnl identity holds (0), but broker.equity (cap+5) != final_equity (cap)
    fin = _default_final_payload(man)
    fin["broker"] = dict(fin["broker"], equity=cap + 5.0)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "session_final", fin, _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_counts_mismatch_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    fin = _default_final_payload(man)
    fin["counts"] = dict(_zero_counts(), bars=1, signals=7)   # claims 7 signals; journal has none
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": _T0, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, _T0, "bar:1"),
        _ev(2, "session_final", fin, _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_cost_sum_mismatch_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    cap = float(man["initial_capital"])
    jp = _jp(tmp_path)
    fin = _default_final_payload(man)
    fin["broker"] = dict(fin["broker"], fees_paid=50.0)   # broker claims 50 fees; no order events
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "session_final", fin, _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (4) actual execution timing (order<->signal) ---------------------------------- #
def test_order_executes_on_decision_bar_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    D = _T0                     # decision bar
    X = _T0 + _DAY             # correct earliest execution (next bar)
    order = {"status": "filled", "side": "buy", "requested_units": 1.0, "filled_units": 1.0,
             "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
             "exec_open_time": D,   # BUT actually executes on the decision bar (look-ahead)
             "cash_after": 0.0, "position_after": 1.0, "equity_after": 100_000.0, "reason": ""}
    sig = {"decision_bar": D, "signal_time": D + 1, "earliest_execution_time": X,
           "target_position": 1.0, "aligned_regime_label": "NA"}
    fin = _default_final_payload(man)
    fin["counts"] = dict(_zero_counts(), bars=1, signals=1, orders=1, fills=1)
    # event timestamps stay monotonic; the look-ahead lives ONLY in order.exec_open_time == D
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": D, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, D, "bar:1"),
        _ev(2, "signal", sig, D + 1, "signal:1"),
        _ev(3, "order", order, X, "order:1"),
        _ev(4, "session_final", fin, X, "session_final")])
    assert verify_journal_file(jp, man["manifest_hash"])[0] is True
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (5) atomic report set: crash before completion leaves nothing valid ----------- #
@pytest.mark.parametrize("fault", ["after_json_stage", "after_md_stage", "between_publish"])
def test_crash_before_completion_leaves_no_valid_report(tmp_path, fault):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    with pytest.raises(RuntimeError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out,
                             _fault=fault)
    assert reporting.load_report(out) is None   # no partial set is accepted


def test_load_report_returns_complete_set(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    loaded = reporting.load_report(out)
    assert loaded is not None and loaded["report_hash"] == rep["report_hash"]


# ---- (6) output binding: idempotent vs content-change vs different session ---------- #
def test_identical_report_is_idempotent_noop(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    r1 = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    before = _sha(report_paths(out)[0])
    r2 = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    assert r1["report_hash"] == r2["report_hash"]
    assert _sha(report_paths(out)[0]) == before   # unchanged bytes


def test_same_session_changed_content_refuses_silent_overwrite(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    before = _sha(report_paths(out)[0])
    # tamper the on-disk JSON's report content but keep the SAME session_id/manifest_hash
    jpath = report_paths(out)[0]
    obj = _read_json(jpath)
    obj["net_pnl"] = (obj["net_pnl"] or 0.0) + 123.0
    obj["report_hash"] = "deadbeef"
    with open(jpath, "w", encoding="utf-8") as f:
        f.write(canonical_json(obj) + "\n")
    with pytest.raises(ReportError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)


# ---- (7) dedicated status integration tests ---------------------------------------- #
def _data_stopped_session(tmp_path):
    closes = [100, 103, 106, 109]
    rows = []
    t = _T0
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        rows.append({"open_time": t, "open": float(o), "high": float(max(o, c) + 1),
                     "low": float(min(o, c) - 1), "close": float(c), "volume": 1000.0})
        t += _DAY * (6 if i == 2 else 1)      # a 5-missing-bar gap after the 3rd bar
    frame = pd.DataFrame(rows)
    man, prof, strat, risk = _mk_manifest(frame, risk=freeze_risk_limits(max_missing_bars=1),
                                          length=3)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": 3}, frame=frame, journal_dir=str(tmp_path / "j"))
    assert res["lifecycle_status"] == DATA_STOPPED
    return res, man


def test_data_stopped_report_has_exact_reason(tmp_path):
    res, man = _data_stopped_session(tmp_path)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    assert rep["verdict"] == "DATA_STOPPED"
    assert rep["exact_reason_code"] == "max_missing_bars"
    rules = [b["rule"] for b in rep["why_it_failed"]]
    assert "max_missing_bars" in rules
    for b in rep["why_it_failed"]:
        assert set(b) >= {"rule", "observed", "limit"}


def _signal_stopped_session(tmp_path, monkeypatch):
    frame = _uptrend(12)
    strat = get_strategy("H-025")
    man, prof, _, risk = _mk_manifest(frame, length=3)
    orig = strat.positions

    def patched(fr, params):
        s = orig(fr, params).copy()
        s.iloc[-1] = float("nan")
        return s
    monkeypatch.setattr(strat, "positions", patched)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": 3}, frame=frame, journal_dir=str(tmp_path / "j"))
    assert res["lifecycle_status"] == SIGNAL_STOPPED
    return res, man


def test_signal_stopped_report_has_exact_reason_code(tmp_path, monkeypatch):
    res, man = _signal_stopped_session(tmp_path, monkeypatch)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    assert rep["verdict"] == "SIGNAL_STOPPED"
    assert rep["exact_reason_code"] == "non_finite_value"


def test_report_invalidated_shows_no_numbers_in_json_or_md(tmp_path):
    res, man = _completed_session(tmp_path)
    jp = res["journal_path"]
    lines = open(jp, encoding="utf-8").read().splitlines()
    obj = json.loads(lines[2]); obj["payload"]["close"] = 12345.0
    lines[2] = canonical_json(obj)
    open(jp, "w", encoding="utf-8").write("\n".join(lines) + "\n")   # corrupt -> invalidated
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=out)
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)
    md = open(report_paths(out)[1], encoding="utf-8").read()
    # the result / cost lines must read n/a, never a computed number
    for line in md.splitlines():
        if line.startswith("- Net PnL (Final") or line.startswith("- Final equity:") \
                or line.startswith("- Gross PnL (net") or line.startswith("- Fees:") \
                or line.startswith("- Spread cost:") or line.startswith("- Slippage cost:"):
            assert "n/a" in line, f"invalidated report leaked a number: {line!r}"


# ---- (8) markdown safety: injected newlines/headings are neutralized --------------- #
def test_markdown_injection_in_journal_text_is_neutralized(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    cap = float(man["initial_capital"])
    jp = _jp(tmp_path)
    inject = "max_missing_bars\n# INJECTED HEADING\n- Live execution: ENABLED"
    breach = {"rule": inject, "observed": 5, "limit": 1, "message": "gap"}
    fin = _default_final_payload(man)
    fin["lifecycle_status"] = DATA_STOPPED
    fin["absolute_risk"] = {"breached": True, "breaches": [breach]}
    fin["counts"] = dict(_zero_counts(), bars=1, risk_events=1)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": _T0, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, _T0, "bar:1"),
        _ev(2, "risk_event", {"open_time": _T0, "kind": "data",
                              "absolute_risk": {"breached": True, "breaches": [breach]},
                              "action": "record_evidence; DATA_STOPPED"}, _T0, "data:1"),
        _ev(3, "session_final", fin, _T0 + _DAY, "session_final")])
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=out)
    assert rep["verdict"] == "DATA_STOPPED"
    md = open(report_paths(out)[1], encoding="utf-8").read()
    for line in md.splitlines():
        assert not line.startswith("# INJECTED"), "journal text forged a markdown heading"
    assert "Live execution: DISABLED" in md
    assert "\n- Live execution: ENABLED" not in md


# =========================================================================== #
# WP2A HARDENING ROUND 2 (REJECT) — written first; fail before, pass after.    #
# =========================================================================== #
def _events_from_run(res):
    """Strip parent/hash from a real journal's events so they can be mutated + rebuilt."""
    evs = []
    for line in open(res["journal_path"], encoding="utf-8"):
        e = json.loads(line)
        evs.append({"event_id": e["event_id"], "seq": e["seq"], "timestamp": e["timestamp"],
                    "event_type": e["event_type"], "payload": e["payload"], "parent_hash": None})
    return evs


def _rebuild(path, mh, events):
    for i, e in enumerate(events):
        e["seq"] = i
    return _hand_chain(path, mh, events)


def _marker_path(out):
    return os.path.join(out, "session_report.complete")


# ---- (1) load_report cryptographically verifies the whole set ---------------------- #
def test_load_report_detects_json_pnl_tamper_without_report_hash_change(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    jp = report_paths(out)[0]
    obj = _read_json(jp)
    obj["net_pnl"] = (obj["net_pnl"] or 0.0) + 999.0     # report_hash field left untouched
    with open(jp, "w", encoding="utf-8") as f:
        f.write(canonical_json(obj) + "\n")
    assert reporting.load_report(out) is None


def test_load_report_detects_report_hash_forgery_in_json_and_marker(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    jp = report_paths(out)[0]
    obj = _read_json(jp)
    obj["net_pnl"] = (obj["net_pnl"] or 0.0) + 5.0
    obj["report_hash"] = "f" * 64                         # forge, but do not recompute correctly
    with open(jp, "w", encoding="utf-8") as f:
        f.write(canonical_json(obj) + "\n")
    m = _read_json(_marker_path(out)); m["report_hash"] = "f" * 64
    with open(_marker_path(out), "w", encoding="utf-8") as f:
        f.write(canonical_json(m) + "\n")
    assert reporting.load_report(out) is None            # recomputed hash != forged hash


def test_load_report_detects_markdown_tamper(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    mp = report_paths(out)[1]
    with open(mp, "a", encoding="utf-8") as f:
        f.write("\n- Net PnL: 999999.00\n")
    assert reporting.load_report(out) is None


def test_load_report_detects_markdown_swapped_from_other_session(tmp_path):
    resA, manA = _completed_session(tmp_path / "A")
    resB, manB = _run(_uptrend(25, base=200.0), tmp_path / "B", length=5)
    outA = str(tmp_path / "outA"); outB = str(tmp_path / "outB")
    build_session_report(manifest=manA, journal_path=resA["journal_path"], output_dir=outA)
    build_session_report(manifest=manB, journal_path=resB["journal_path"], output_dir=outB)
    # replace A's markdown with B's
    with open(report_paths(outB)[1], encoding="utf-8") as f:
        bmd = f.read()
    with open(report_paths(outA)[1], "w", encoding="utf-8") as f:
        f.write(bmd)
    assert reporting.load_report(outA) is None


@pytest.mark.parametrize("target", ["json", "md", "marker"])
def test_load_report_detects_truncation(tmp_path, target):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    path = {"json": report_paths(out)[0], "md": report_paths(out)[1], "marker": _marker_path(out)}[target]
    data = open(path, "rb").read()
    with open(path, "wb") as f:
        f.write(data[: len(data) // 2])                  # truncate
    assert reporting.load_report(out) is None


def test_marker_contains_file_hashes(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    m = _read_json(_marker_path(out))
    for k in ("session_id", "manifest_hash", "report_hash", "json_sha256", "markdown_sha256"):
        assert k in m
    assert reporting.load_report(out) is not None        # untampered set loads


# ---- (2) total fail-closed payload validation -------------------------------------- #
@pytest.mark.parametrize("mutation", [
    ("payload_none",), ("payload_list",), ("payload_str",),
    ("absrisk_list",), ("absrisk_str",), ("breaches_dict",), ("breaches_str",),
    ("breach_none",), ("breach_list",), ("broker_list",), ("counts_list",),
    ("order_payload_list",), ("snapshot_payload_list",), ("signal_payload_list",),
])
def test_malformed_payload_types_are_report_invalidated_not_crash(tmp_path, mutation):
    res, man = _risk_stopped_session(tmp_path)   # has orders, signals, snapshots, risk, safe_exit
    evs = _events_from_run(res)
    kind = mutation[0]
    if kind == "payload_none":
        evs[1]["payload"] = None
    elif kind == "payload_list":
        evs[1]["payload"] = [1, 2, 3]
    elif kind == "payload_str":
        evs[1]["payload"] = "not a dict"
    elif kind == "absrisk_list":
        evs[-1]["payload"]["absolute_risk"] = [1]
    elif kind == "absrisk_str":
        evs[-1]["payload"]["absolute_risk"] = "x"
    elif kind == "breaches_dict":
        evs[-1]["payload"]["absolute_risk"]["breaches"] = {"a": 1}
    elif kind == "breaches_str":
        evs[-1]["payload"]["absolute_risk"]["breaches"] = "x"
    elif kind == "breach_none":
        evs[-1]["payload"]["absolute_risk"]["breaches"] = [None]
    elif kind == "breach_list":
        evs[-1]["payload"]["absolute_risk"]["breaches"] = [[1, 2]]
    elif kind == "broker_list":
        evs[-1]["payload"]["broker"] = [1, 2]
    elif kind == "counts_list":
        evs[-1]["payload"]["counts"] = [1, 2]
    elif kind in ("order_payload_list", "snapshot_payload_list", "signal_payload_list"):
        want = {"order_payload_list": "order", "snapshot_payload_list": "portfolio_snapshot",
                "signal_payload_list": "signal"}[kind]
        for e in evs:
            if e["event_type"] == want:
                e["payload"] = [1, 2, 3]
                break
    jp = _jp(tmp_path, "mut.jsonl")
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (3) honest live-execution display --------------------------------------------- #
def test_live_module_constant_marks_violation_in_markdown(tmp_path, monkeypatch):
    res, man = _completed_session(tmp_path)
    monkeypatch.setattr(reporting, "LIVE_EXECUTION_ENABLED", True)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["live_execution_enabled"] is True and _pnl_all_none(rep)
    md = open(report_paths(out)[1], encoding="utf-8").read()
    assert "VIOLATION" in md and "ENABLED" in md
    assert "Live execution: DISABLED" not in md


def test_live_run_result_marks_violation(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    run_result = {"lifecycle_status": INVALIDATED, "mismatched_field": "paper_live_invariant",
                  "live_execution_enabled": True}
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=None, run_result=run_result, output_dir=out)
    assert rep["verdict"] == V_REPORT_INVALIDATED
    assert rep["live_execution_enabled"] is True and _pnl_all_none(rep)
    md = open(report_paths(out)[1], encoding="utf-8").read()
    assert "VIOLATION" in md


# ---- (4) strict 1:1 signal/order linkage + structural safe-exit -------------------- #
def test_two_orders_reuse_one_signal_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    D, X = _T0, _T0 + _DAY
    o = lambda: {"status": "filled", "side": "buy", "requested_units": 1.0, "filled_units": 1.0,
                 "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
                 "exec_open_time": X, "cash_after": 0.0, "position_after": 1.0,
                 "equity_after": 100_000.0, "reason": ""}
    sig = {"decision_bar": D, "signal_time": D + 1, "earliest_execution_time": X,
           "target_position": 1.0, "aligned_regime_label": "NA"}
    fin = _default_final_payload(man)
    fin["counts"] = dict(_zero_counts(), bars=1, signals=1, orders=2, fills=2)
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": D, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, D, "bar:1"),
        _ev(2, "signal", sig, D + 1, "signal:1"),
        _ev(3, "order", o(), X, "order:1"),
        _ev(4, "order", o(), X, "order:2"),
        _ev(5, "session_final", fin, X, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_two_signals_same_earliest_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    D0, D1, X = _T0, _T0 + _DAY, _T0 + 2 * _DAY
    s = lambda d: {"decision_bar": d, "signal_time": d + 1, "earliest_execution_time": X,
                   "target_position": 0.5, "aligned_regime_label": "NA"}
    fin = _default_final_payload(man)
    fin["counts"] = dict(_zero_counts(), bars=2, signals=2)
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": D0, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, D0, "bar:1"),
        _ev(2, "signal", s(D0), D0 + 1, "signal:1"),
        _ev(3, "bar_ingested", {"open_time": D1, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, D1, "bar:2"),
        _ev(4, "signal", s(D1), D1 + 1, "signal:2"),
        _ev(5, "session_final", fin, X, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def _valid_risk_run(tmp_path):
    return _risk_stopped_session(tmp_path)


def test_safe_exit_status_mismatch_is_report_invalidated(tmp_path):
    res, man = _valid_risk_run(tmp_path)
    evs = _events_from_run(res)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["order_status"] = "partial"   # order was 'filled'
    jp = _jp(tmp_path, "mut.jsonl")
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_order_after_safe_exit_is_report_invalidated(tmp_path):
    res, man = _valid_risk_run(tmp_path)
    evs = _events_from_run(res)
    fin = evs[-1]
    extra = {"status": "filled", "side": "sell", "requested_units": -1.0, "filled_units": -1.0,
             "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
             "exec_open_time": fin["timestamp"], "cash_after": 0.0, "position_after": 0.0,
             "equity_after": 100_000.0, "reason": ""}
    evs.insert(len(evs) - 1, {"event_id": "order:after", "seq": 0, "timestamp": fin["timestamp"],
                              "event_type": "order", "payload": extra, "parent_hash": None})
    jp = _jp(tmp_path, "mut.jsonl")
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_orphan_order_with_fake_safe_exit_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    Y = _T0 + _DAY
    order = {"status": "filled", "side": "buy", "requested_units": 1.0, "filled_units": 1.0,
             "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
             "exec_open_time": Y, "cash_after": 0.0, "position_after": 1.0,
             "equity_after": 100_000.0, "reason": ""}
    fake_safe = {"open_time": Y, "order_status": "filled", "flat": True, "position_units": 0.0,
                 "mark_price": 100.0, "equity": 100_000.0, "note": "fake"}
    fin = _default_final_payload(man)   # COMPLETED, so a safe_exit is illegitimate
    fin["counts"] = dict(_zero_counts(), bars=1, orders=1, fills=1, safe_exits=1)
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": _T0, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, _T0, "bar:1"),
        _ev(2, "order", order, Y, "order:1"),
        _ev(3, "safe_exit", fake_safe, Y, "safeexit:1"),
        _ev(4, "session_final", fin, Y, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (5) validate all economic event data ------------------------------------------ #
def test_negative_individual_order_cost_is_report_invalidated(tmp_path):
    res, man = _risk_stopped_session(tmp_path)
    evs = _events_from_run(res)
    for e in evs:
        if e["event_type"] == "order":
            e["payload"]["fee"] = -1.0        # a single negative cost, even if totals reconcile
            break
    jp = _jp(tmp_path, "mut.jsonl")
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "x", True])
def test_bad_order_fill_price_is_report_invalidated(tmp_path, bad):
    res, man = _risk_stopped_session(tmp_path)
    evs = _events_from_run(res)
    for e in evs:
        if e["event_type"] == "order":
            e["payload"]["fill_price"] = bad
            break
    jp = _jp(tmp_path, "mut.jsonl")
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_bad_snapshot_value_is_report_invalidated(tmp_path):
    res, man = _risk_stopped_session(tmp_path)
    evs = _events_from_run(res)
    for e in evs:
        if e["event_type"] == "portfolio_snapshot":
            e["payload"]["equity"] = float("nan")
            break
    jp = _jp(tmp_path, "mut.jsonl")
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (6) terminal state consistency ------------------------------------------------ #
def test_completed_with_stray_risk_event_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    fin = _default_final_payload(man)   # COMPLETED
    fin["counts"] = dict(_zero_counts(), bars=1, risk_events=1)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": _T0, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, _T0, "bar:1"),
        _ev(2, "risk_event", {"open_time": _T0, "kind": "risk",
                              "absolute_risk": {"breached": True, "breaches": [
                                  {"rule": "max_drawdown", "observed": 0.5, "limit": 0.1, "message": "x"}]},
                              "action": "x"}, _T0, "risk:1"),
        _ev(3, "session_final", fin, _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_risk_stopped_without_breach_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    fin = _default_final_payload(man)
    fin["lifecycle_status"] = RISK_STOPPED
    fin["absolute_risk"] = {"breached": False, "breaches": []}   # claims risk stop, no breach
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "session_final", fin, _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (7) markdown text safety: HTML/script + unicode line separators --------------- #
def test_markdown_escapes_html_and_unicode_linebreaks(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    inject = "max <script>alert(1)</script> # INJECTED\n- Live execution: ENABLED & bad"
    breach = {"rule": inject, "observed": 5, "limit": 1, "message": "gap"}
    fin = _default_final_payload(man)
    fin["lifecycle_status"] = DATA_STOPPED
    fin["absolute_risk"] = {"breached": True, "breaches": [breach]}
    fin["counts"] = dict(_zero_counts(), bars=1, risk_events=1)
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", {"open_time": _T0, "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1.0}, _T0, "bar:1"),
        _ev(2, "risk_event", {"open_time": _T0, "kind": "data",
                              "absolute_risk": {"breached": True, "breaches": [breach]},
                              "action": "x"}, _T0, "data:1"),
        _ev(3, "session_final", fin, _T0 + _DAY, "session_final")])
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=out)
    assert rep["verdict"] == "DATA_STOPPED"
    md = open(report_paths(out)[1], encoding="utf-8").read()
    assert "<script>" not in md and "</script>" not in md
    assert "\u2028" not in md and "\u2029" not in md and "\u0085" not in md
    for line in md.splitlines():
        assert not line.startswith("# INJECTED")
    assert "Live execution: DISABLED" in md


# =========================================================================== #
# WP2A HARDENING ROUND 3 (final targeted rectification) — test-first.          #
# =========================================================================== #
_BREACH = {"rule": "max_drawdown", "observed": 0.5, "limit": 0.1, "message": "dd"}
_DBREACH = {"rule": "max_missing_bars", "observed": 5, "limit": 1, "message": "gap"}


def _bar(ot, c=100.0):
    return {"open_time": ot, "open": c, "high": c + 1.0, "low": c - 1.0, "close": c, "volume": 1000.0}


def _final(man, status, *, breaches, counts):
    cap = float(man["initial_capital"])
    return {"lifecycle_status": status, "manifest_hash": man["manifest_hash"],
            "absolute_risk": {"breached": bool(breaches), "breaches": list(breaches)},
            "counts": counts, "broker": _good_snapshot(cap), "final_equity": cap,
            "initial_capital": cap, "net_pnl": 0.0, "net_return": 0.0}


def _risk_none_events(man):
    """Minimal VALID RISK_STOPPED: risk_event(kind=risk) then an already-flat safe_exit(none)."""
    return [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), _T0, "bar:1"),
        _ev(2, "risk_event", {"open_time": _T0, "kind": "risk",
                              "absolute_risk": {"breached": True, "breaches": [_BREACH]},
                              "action": "x"}, _T0 + 1, "risk:1"),
        _ev(3, "safe_exit", {"open_time": _T0, "order_status": "none", "flat": True,
                             "position_units": 0.0, "mark_price": 0.0, "equity": 100_000.0,
                             "note": "already flat"}, _T0 + 1, "safeexit:1"),
        _ev(4, "session_final", _final(man, RISK_STOPPED, breaches=[_BREACH],
             counts=dict(_zero_counts(), bars=1, risk_events=1)), _T0 + 2, "session_final")]


def test_mini_risk_none_is_valid_positive_control(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], _risk_none_events(man))
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_RISK_STOPPED and rep["exact_reason_code"] == "max_drawdown"


# ---- (1) exact terminal-state contract --------------------------------------------- #
def test_completed_with_data_gap_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), _T0, "bar:1"),
        _ev(2, "data_gap", {"open_time": _T0, "missing_trading_bars": 3, "wall_gap_ms": 3,
                            "trading_gap_ms": 3}, _T0, "gap:1"),
        _ev(3, "session_final", _final(man, COMPLETED, breaches=[], counts=dict(_zero_counts(), bars=1)),
            _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_completed_with_bar_rejected_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), _T0, "bar:1"),
        _ev(2, "bar_rejected", {"open_time": _T0, "reason": "out of order"}, _T0, "rej:1"),
        _ev(3, "session_final", _final(man, COMPLETED, breaches=[], counts=dict(_zero_counts(), bars=1)),
            _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_risk_stopped_without_safe_exit_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), _T0, "bar:1"),
        _ev(2, "risk_event", {"open_time": _T0, "kind": "risk",
                              "absolute_risk": {"breached": True, "breaches": [_BREACH]},
                              "action": "x"}, _T0 + 1, "risk:1"),
        _ev(3, "session_final", _final(man, RISK_STOPPED, breaches=[_BREACH],
             counts=dict(_zero_counts(), bars=1, risk_events=1)), _T0 + 2, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_data_stopped_with_safe_exit_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), _T0, "bar:1"),
        _ev(2, "data_gap", {"open_time": _T0, "missing_trading_bars": 5, "wall_gap_ms": 5,
                            "trading_gap_ms": 5}, _T0, "gap:1"),
        _ev(3, "risk_event", {"open_time": _T0, "kind": "data",
                              "absolute_risk": {"breached": True, "breaches": [_DBREACH]},
                              "action": "x"}, _T0, "data:1"),
        _ev(4, "safe_exit", {"open_time": _T0, "order_status": "none", "flat": True,
                             "note": "x"}, _T0, "safeexit:1"),
        _ev(5, "session_final", _final(man, DATA_STOPPED, breaches=[_DBREACH],
             counts=dict(_zero_counts(), bars=1, risk_events=1)), _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_signal_stopped_with_order_after_rejected_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    order = {"status": "filled", "side": "buy", "requested_units": 1.0, "filled_units": 1.0,
             "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
             "exec_open_time": _T0, "cash_after": 0.0, "position_after": 1.0,
             "equity_after": 100_000.0, "reason": ""}
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), _T0, "bar:1"),
        _ev(2, "signal_rejected", {"decision_bar": _T0, "status": "rejected",
                                   "reason_code": "non_finite_value", "original": "x"}, _T0, "sr:1"),
        _ev(3, "order", order, _T0, "order:1"),
        _ev(4, "session_final", _final(man, SIGNAL_STOPPED, breaches=[],
             counts=dict(_zero_counts(), bars=1, orders=1, fills=1)), _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_competing_terminal_causes_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    # RISK_STOPPED lifecycle but a signal_rejected also present -> competing terminal cause
    evs = _risk_none_events(man)
    evs.insert(2, _ev(0, "signal_rejected", {"decision_bar": _T0, "reason_code": "non_finite_value",
                                             "original": "x"}, _T0, "sr:1"))
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (2) safe-exit contract -------------------------------------------------------- #
def test_safe_exit_after_data_risk_but_no_risk_kind_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    for e in evs:
        if e["event_type"] == "risk_event":
            e["payload"]["kind"] = "data"          # a data-kind risk_event cannot arm a safe-exit
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_order_between_risk_and_none_safe_exit_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    order = {"status": "filled", "side": "sell", "requested_units": -1.0, "filled_units": -1.0,
             "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
             "exec_open_time": _T0, "cash_after": 0.0, "position_after": 0.0,
             "equity_after": 100_000.0, "reason": ""}
    # insert an order between the risk_event and the order_status="none" safe_exit
    idx = next(i for i, e in enumerate(evs) if e["event_type"] == "safe_exit")
    evs.insert(idx, _ev(0, "order", order, _T0 + 1, "order:x"))
    evs[-1]["payload"]["counts"] = dict(_zero_counts(), bars=1, risk_events=1, orders=1, fills=1)
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


@pytest.mark.parametrize("bad_open", [-1, "x", 1.5, None])
def test_invalid_safe_exit_open_time_is_report_invalidated(tmp_path, bad_open):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["open_time"] = bad_open
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_invalid_safe_exit_order_status_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["order_status"] = "bogus"
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_two_safe_exit_events_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    idx = next(i for i, e in enumerate(evs) if e["event_type"] == "safe_exit")
    dup = dict(evs[idx]); dup = _ev(0, "safe_exit", dict(evs[idx]["payload"]), _T0 + 1, "safeexit:2")
    evs.insert(idx + 1, dup)
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (3) refuse silent repair of tampered output ----------------------------------- #
def test_build_refuses_to_repair_tampered_markdown(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    jp, mp = report_paths(out); mk = _marker_path(out)
    with open(mp, "a", encoding="utf-8") as f:
        f.write("\n- tampered\n")
    jb, mkb = _sha(jp), _sha(mk)
    with pytest.raises(ReportError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    assert _sha(jp) == jb and _sha(mk) == mkb          # untouched


def test_build_refuses_to_repair_tampered_marker(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    m = _read_json(_marker_path(out)); m["json_sha256"] = "0" * 64
    with open(_marker_path(out), "w", encoding="utf-8") as f:
        f.write(canonical_json(m) + "\n")
    with pytest.raises(ReportError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)


def test_build_refuses_to_repair_tampered_json(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    jp = report_paths(out)[0]
    obj = _read_json(jp); obj["net_pnl"] = (obj["net_pnl"] or 0.0) + 1.0
    with open(jp, "w", encoding="utf-8") as f:
        f.write(canonical_json(obj) + "\n")
    with pytest.raises(ReportError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)


def test_incomplete_crash_set_is_not_silently_repaired(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    with pytest.raises(RuntimeError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out,
                             _fault="between_publish")
    assert reporting.load_report(out) is None
    with pytest.raises(ReportError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)


def test_explicit_recovery_rebuilds_a_valid_set(tmp_path):
    res, man = _completed_session(tmp_path)
    out = str(tmp_path / "out")
    with pytest.raises(RuntimeError):
        build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out,
                             _fault="between_publish")
    assert reporting.load_report(out) is None
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out,
                               recover=True)
    assert reporting.load_report(out) is not None
    assert rep["verdict"] == V_COMPLETED


# ---- (4) complete event validation ------------------------------------------------- #
def test_negative_event_timestamp_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), -5, "bar:1"),          # negative timestamp
        _ev(2, "session_final", _final(man, COMPLETED, breaches=[], counts=dict(_zero_counts(), bars=1)),
            _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


@pytest.mark.parametrize("field,val", [("event_type", ""), ("event_id", "")])
def test_empty_event_type_or_id_is_report_invalidated(tmp_path, field, val):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", _bar(_T0), _T0, "bar:1"),
        _ev(2, "session_final", _final(man, COMPLETED, breaches=[], counts=dict(_zero_counts(), bars=1)),
            _T0 + _DAY, "session_final")]
    evs[1][field] = val
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_unknown_event_type_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "mystery_event", {"x": 1}, _T0, "m:1"),
        _ev(2, "session_final", _final(man, COMPLETED, breaches=[], counts=_zero_counts()),
            _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


@pytest.mark.parametrize("mut", [{"high": 1.0, "low": 5.0}, {"close": float("nan")},
                                 {"open": -3.0}, {"volume": float("inf")}])
def test_bad_bar_ohlcv_is_report_invalidated(tmp_path, mut):
    man, *_ = _mk_manifest(_uptrend(5))
    bar = _bar(_T0); bar.update(mut)
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", bar, _T0, "bar:1"),
        _ev(2, "session_final", _final(man, COMPLETED, breaches=[], counts=dict(_zero_counts(), bars=1)),
            _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# =========================================================================== #
# WP2A HARDENING ROUND 4 (final targeted fix) — test-first.                     #
# =========================================================================== #
def _risk_filled_events(man):
    """Minimal VALID RISK_STOPPED with an EXECUTED (filled) safe-exit that truly flattens."""
    T0, T1, T2 = _T0, _T0 + _DAY, _T0 + 2 * _DAY
    cap = float(man["initial_capital"])
    o_buy = {"status": "filled", "side": "buy", "requested_units": 1.0, "filled_units": 1.0,
             "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
             "exec_open_time": T1, "cash_after": 0.0, "position_after": 1.0,
             "equity_after": cap, "reason": ""}
    o_exit = {"status": "filled", "side": "sell", "requested_units": -1.0, "filled_units": -1.0,
              "fill_price": 100.0, "fee": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0,
              "exec_open_time": T2, "cash_after": cap, "position_after": 0.0,
              "equity_after": cap, "reason": ""}
    snap = dict(_good_snapshot(cap), open_time=T1, close=100.0)
    sig = {"decision_bar": T0, "signal_time": T0 + 1, "earliest_execution_time": T1,
           "target_position": 1.0, "aligned_regime_label": "NA"}
    se = {"open_time": T2, "order_status": "filled", "flat": True, "position_units": 0.0,
          "mark_price": 100.0, "equity": cap, "note": "position flattened"}
    fin = _final(man, RISK_STOPPED, breaches=[_BREACH],
                 counts=dict(_zero_counts(), bars=3, signals=1, orders=2, fills=2,
                             risk_events=1, safe_exits=1))
    # final broker state must agree three-ways with the safe-exit (position 0, mark 100, equity cap)
    fin["broker"] = dict(fin["broker"], position_units=0.0, mark_price=100.0, equity=cap)
    return [
        _ev(0, "session_created", _default_created_payload(man), T0, "session_created"),
        _ev(1, "bar_ingested", _bar(T0), T0, "bar:1"),
        _ev(2, "signal", sig, T0 + 1, "signal:1"),
        _ev(3, "bar_ingested", _bar(T1), T1, "bar:2"),
        _ev(4, "order", o_buy, T1, "order:1"),
        _ev(5, "portfolio_snapshot", snap, T1, "snap:1"),
        _ev(6, "risk_event", {"open_time": T1, "kind": "risk",
                              "absolute_risk": {"breached": True, "breaches": [_BREACH]},
                              "action": "x"}, T1, "risk:1"),
        _ev(7, "bar_ingested", _bar(T2), T2, "bar:3"),
        _ev(8, "order", o_exit, T2, "order:2"),
        _ev(9, "safe_exit", se, T2, "safeexit:1"),
        _ev(10, "session_final", fin, T2, "session_final")]


def test_mini_risk_filled_is_valid_positive_control(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], _risk_filled_events(man))
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_RISK_STOPPED and rep["exact_reason_code"] == "max_drawdown"


# ---- (1) safe_exit(order_status="none") note allow-list ---------------------------- #
@pytest.mark.parametrize("note", ["x", "flat", "already  flat", "", None, 123, ["already flat"]])
def test_none_safe_exit_arbitrary_note_is_report_invalidated(tmp_path, note):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["note"] = note
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_none_safe_exit_exact_already_flat_note_is_valid(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], _risk_none_events(man))    # note == "already flat"
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_RISK_STOPPED


def test_no_next_bar_unresolved_safe_exit_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["note"] = "no next bar to execute safe-exit"   # valid note, but position open
            e["payload"]["flat"] = False
            e["payload"]["position_units"] = 3.0
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# ---- (2) prove the safe-exit result ------------------------------------------------ #
def test_filled_safe_exit_flat_false_with_dust_residual_is_accepted(tmp_path):
    # DEVIATION (documented): the real Stage 1 engine emits flat=False for a sub-qty_step dust
    # residual (H-025 leaves ~9.99e-7). The reporter proves resolution by the residual POSITION being
    # within dust tolerance (not the raw flat boolean), so a dust residual is a valid resolved exit.
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_filled_events(man)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["flat"] = False
            e["payload"]["position_units"] = 9.99e-7
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_RISK_STOPPED and rep["net_pnl"] is not None


def test_filled_safe_exit_nonzero_position_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_filled_events(man)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["position_units"] = 5.0
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_filled_order_position_after_nonzero_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_filled_events(man)
    evs[8]["payload"]["position_after"] = 5.0        # the safe-exit order did not flatten
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_safe_exit_position_mismatch_with_order_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_filled_events(man)
    evs[8]["payload"]["position_after"] = 2.0
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["position_units"] = 0.0     # disagrees with the order's residual position
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


@pytest.mark.parametrize("status", ["partial", "rejected", "no_trade", "hold"])
def test_non_filled_safe_exit_with_open_position_is_report_invalidated(tmp_path, status):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_filled_events(man)
    evs[8]["payload"]["status"] = status
    evs[8]["payload"]["position_after"] = 1.0        # still holding
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"]["order_status"] = status
            e["payload"]["flat"] = False
            e["payload"]["position_units"] = 1.0
    # keep counts consistent with the changed order status
    fills = 1 if status not in ("filled",) else 2
    partials = 1 if status == "partial" else 0
    rejects = 1 if status == "rejected" else 0
    evs[-1]["payload"]["counts"] = dict(_zero_counts(), bars=3, signals=1, orders=2,
                                        fills=fills, partials=partials, rejects=rejects,
                                        risk_events=1, safe_exits=1)
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_real_filled_safe_exit_run_reports_risk_stopped(tmp_path):
    # the real engine safe-exit (default profile) truly flattens -> still a valid RISK_STOPPED report
    res, man = _risk_stopped_session(tmp_path)
    out = str(tmp_path / "out")
    rep = build_session_report(manifest=man, journal_path=res["journal_path"], output_dir=out)
    assert rep["verdict"] == V_RISK_STOPPED and rep["net_pnl"] is not None


# ---- (3) complete OHLC semantic validation ----------------------------------------- #
@pytest.mark.parametrize("mut", [
    {"high": 90.0},                 # high < low (low=99)
    {"high": 95.0, "open": 100.0},  # high < open
    {"high": 95.0, "close": 100.0}, # high < close
    {"low": 105.0},                 # low > open/close (open=close=100)
    {"low": 101.0},                 # low > open and close
])
def test_impossible_ohlc_relationship_is_report_invalidated(tmp_path, mut):
    man, *_ = _mk_manifest(_uptrend(5))
    bar = _bar(_T0, 100.0); bar.update(mut)          # base: open=close=100, high=101, low=99
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], [
        _ev(0, "session_created", _default_created_payload(man), _T0, "session_created"),
        _ev(1, "bar_ingested", bar, _T0, "bar:1"),
        _ev(2, "session_final", _final(man, COMPLETED, breaches=[], counts=dict(_zero_counts(), bars=1)),
            _T0 + _DAY, "session_final")])
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


# =========================================================================== #
# WP2A HARDENING ROUND 5 — safe_exit reconciled against final broker state.     #
# qty_step-derived dust tolerance (BTC/USD qty_step = 1e-6).                     #
# =========================================================================== #
def _fix_recon(evs, *, se_pu=None, order_pa=None, broker_pos=None, broker_mark=None,
               broker_eq=None, se_equity=None, se_mark=None):
    """Mutate positions/equity/mark while keeping the final-equity reconciliation valid, so the
    ONLY thing a test perturbs is the safe-exit three-way reconciliation."""
    fin = evs[-1]["payload"]; b = fin["broker"]
    if broker_mark is not None:
        b["mark_price"] = broker_mark
    if broker_eq is not None:
        b["equity"] = broker_eq
    if broker_pos is not None:
        b["position_units"] = broker_pos
    b["cash"] = b["equity"] - b["position_units"] * b["mark_price"]     # keep equity == cash+pos*mark
    fin["final_equity"] = b["equity"]
    fin["net_pnl"] = b["equity"] - fin["initial_capital"]
    fin["net_return"] = fin["net_pnl"] / fin["initial_capital"]
    if order_pa is not None:
        evs[8]["payload"]["position_after"] = order_pa
    for e in evs:
        if e["event_type"] == "safe_exit":
            if se_pu is not None:
                e["payload"]["position_units"] = se_pu
            if se_equity is not None:
                e["payload"]["equity"] = se_equity
            if se_mark is not None:
                e["payload"]["mark_price"] = se_mark
    return evs


def _report_filled(tmp_path, mutate):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_filled_events(man)
    mutate(evs)
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    return build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))


def test_dust_tolerance_is_bound_from_manifest_qty_step(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    jp = _jp(tmp_path)
    _hand_chain(jp, man["manifest_hash"], _risk_filled_events(man))
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_RISK_STOPPED
    se = rep["safe_exit"]
    assert se["dust_tolerance"] == man["execution_profile"]["qty_step"]
    assert se["dust_tolerance_source"] == "manifest.execution_profile.qty_step"


def test_residual_exactly_within_qty_step_tolerance_is_accepted(tmp_path):
    rep = _report_filled(tmp_path, lambda evs: _fix_recon(evs, se_pu=9e-7, order_pa=9e-7, broker_pos=9e-7))
    assert rep["verdict"] == V_RISK_STOPPED and rep["net_pnl"] is not None


def test_residual_just_above_tolerance_is_report_invalidated(tmp_path):
    rep = _report_filled(tmp_path, lambda evs: _fix_recon(evs, se_pu=1.1e-6, order_pa=1.1e-6, broker_pos=1.1e-6))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_safe_exit_zero_but_final_broker_open_is_report_invalidated(tmp_path):
    rep = _report_filled(tmp_path, lambda evs: _fix_recon(evs, se_pu=0.0, order_pa=0.0, broker_pos=5.0))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_final_broker_zero_but_safe_exit_open_is_report_invalidated(tmp_path):
    rep = _report_filled(tmp_path, lambda evs: _fix_recon(evs, se_pu=5.0, order_pa=5.0, broker_pos=0.0))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_order_position_after_zero_but_final_broker_open_is_report_invalidated(tmp_path):
    rep = _report_filled(tmp_path, lambda evs: _fix_recon(evs, se_pu=0.0, order_pa=0.0, broker_pos=5.0))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_safe_exit_equity_mismatch_with_final_broker_is_report_invalidated(tmp_path):
    rep = _report_filled(tmp_path, lambda evs: _fix_recon(evs, se_equity=100_500.0))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_safe_exit_mark_mismatch_with_final_broker_is_report_invalidated(tmp_path):
    rep = _report_filled(tmp_path, lambda evs: _fix_recon(evs, se_mark=999.0))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_missing_position_for_already_flat_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    for e in evs:
        if e["event_type"] == "safe_exit":
            e["payload"].pop("position_units", None)     # missing evidence must NOT read as flat
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)


def test_already_flat_position_disagrees_with_broker_is_report_invalidated(tmp_path):
    man, *_ = _mk_manifest(_uptrend(5))
    evs = _risk_none_events(man)
    evs[-1]["payload"]["broker"] = dict(evs[-1]["payload"]["broker"], position_units=5.0)
    evs[-1]["payload"]["broker"]["cash"] = evs[-1]["payload"]["broker"]["equity"] - 5.0 * evs[-1]["payload"]["broker"]["mark_price"]
    jp = _jp(tmp_path)
    _rebuild(jp, man["manifest_hash"], evs)
    rep = build_session_report(manifest=man, journal_path=jp, output_dir=str(tmp_path / "out"))
    assert rep["verdict"] == V_REPORT_INVALIDATED and _pnl_all_none(rep)
