"""WP2B-4 — Phase 4 Final Integration & Evidence Gate (TEST-MODE) tests.

Deterministic, read-only, fail-closed. Consumes the frozen Stage-1 engine / Stage-2A reporting / WP2B-0A /
WP2B-1 / WP2B-2 / WP2B-3 pipeline unchanged. Covers the consolidated R3 + R3A matrix: dependency contract,
expected-bindings schema + immutability, input structure, artifact presence, journal verification, Stage-2A
verified-before-identity precedence, authoritative-forward-session, concrete ref/fwd dataset hashes,
WP2B-1/2/3 reproduction, safety invariants, expected-binding match, deterministic double-build, WP2B-3
status mapping, exact directory-fsync errno policy + descriptor closure, exactly-three-artifact crash-safe
publication, byte-determinism, and the four pilots (real H-025 uptrend + risk-stop → NEEDS_MORE_DATA;
corrupted-artifact fail-closed; and a clearly-labelled SYNTHETIC CONTRACT PASS pilot → PHASE4_TECHNICALLY_
COMPLETE, which is NOT real-market / H-025 / profitability / robustness / Phase-5 evidence).
"""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import shutil

import pandas as pd
import pytest

import app.forward.phase4_final_gate as G
from app.forward.phase4_final_gate import (
    build_phase4_final_gate, load_phase4_final_gate, build_phase4_expected_bindings_candidate,
    Phase4GateError, _build_phase4_final_gate_from_artifacts, EXPECTED_BINDINGS_FIELDS,
)
from app.strategy_research.strategies import get_strategy
from app.paper.session.risk import freeze_risk_limits
from app.paper.session.manifest import build_session_manifest
from app.paper.session.engine import run_replay
from app.paper.session.profiles import freeze_execution_profile
from app.paper.session.ingest import dataset_hash
from app.paper.session.reporting import build_session_report
from app.paper.session.journal import Journal, read_verified_events
from app.forward.shadow.ranges import build_reference_ranges, freeze_range_policy
from app.forward.shadow.regime import DEFAULT_REGIME_POLICY, regime_policy_hash
from app.forward.shadow.consistency import DEFAULT_CONSISTENCY_POLICY, consistency_policy_hash
from app.forward.segment_pipeline import build_segment_report_from_journal
from app.forward.cost_analysis import build_cost_analysis_from_journal, CostAnalysisError
from app.forward.drift_classification import build_baseline_candidate_from_journal, classify_drift_from_journals
from app.forward.drift_classification import _sha as _dsha
from tests.test_drift_classification import _rt2, _ovl, _synth, _shift, _test_policy, _PINNED_POLICY_HASH
from tests.test_cost_analysis import _synth_manifest
from tests.test_segment_pipeline import _policy, _seg, _T0, _DAY


# =========================================================================== #
# real H-025 session builder (engine + Stage-2A report set in ONE dir)         #
# =========================================================================== #
def _h025_session(base, closes, tag, root):
    strat = get_strategy("H-025")
    prof = freeze_execution_profile("BTC/USD")
    rpol = freeze_range_policy(seed=42, block_length=3, n_resamples=100, min_unique_segments=6)
    segs = [{"segment_index": i, "metrics": {"return": 0.001 * (i % 7 - 3), "drawdown": -0.02,
             "trade_count": 5.0, "turnover": 1.0, "exposure_time": 0.5, "win_rate": 0.5,
             "avg_holding_period": 3.0, "long_short_balance": 1.0}} for i in range(24)]
    ref = build_reference_ranges(segs, policy=rpol)
    opens = [closes[0]] + closes[:-1]
    fr = pd.DataFrame([{"open_time": base + i * _DAY, "open": float(opens[i]),
                        "high": max(opens[i], closes[i]) + 1.0,
                        "low": max(0.01, min(opens[i], closes[i]) - 1.0),
                        "close": float(closes[i]), "volume": 1000.0} for i in range(len(closes))])
    man = build_session_manifest(strategy=strat, params={"length": 5}, asset="BTC/USD", timeframe="1d",
                                 data_source={"provider": "synthetic_test", "root": "n/a", "is_real": False},
                                 start_timestamp_ms=int(fr["open_time"].iloc[0]), initial_capital=100_000.0,
                                 position_sizing_policy={"type": "target_fraction_of_equity", "max_gross": 1.0},
                                 execution_profile=prof, absolute_risk_limits=freeze_risk_limits(max_drawdown=0.90),
                                 gap_stale_policy={"max_gap_bars": 2, "on_gap": "DATA_STOPPED"},
                                 regime_policy_hash=regime_policy_hash(DEFAULT_REGIME_POLICY),
                                 consistency_policy_hash=consistency_policy_hash(DEFAULT_CONSISTENCY_POLICY),
                                 reference_range_hash=ref["range_hash"], comparability_basis=ref["comparability_basis"],
                                 dataset_hash=dataset_hash(fr))
    d = os.path.join(str(root), tag)
    os.makedirs(d)
    res = run_replay(manifest=man, profile=prof, risk_limits=freeze_risk_limits(max_drawdown=0.90),
                     strategy=strat, params={"length": 5}, frame=fr, journal_dir=d)
    path = os.path.join(d, "journal.jsonl")
    build_session_report(manifest=man, journal_path=path, output_dir=d)     # report set in SAME dir
    ev = read_verified_events(path, man["manifest_hash"])
    ots = sorted(e["payload"]["open_time"] for e in ev if e["event_type"] == "bar_ingested")
    pol = _policy([_seg("s1", ots[0], ots[-1] + _DAY)])
    return d, path, man, pol, res["lifecycle_status"]


def _h025_inputs(root, closes, fwd_shift):
    rdir, rp, rman, rpol, rlc = _h025_session(_T0, closes, "ref", root)
    fdir, fp, fman, fpol, flc = _h025_session(_T0 + fwd_shift * _DAY, closes, "fwd", root)
    pol = _test_policy()
    exp_base = build_baseline_candidate_from_journal(rp, rman, rpol)["candidate_baseline_hash"]
    di = {"reference_journal_path": rp, "forward_journal_path": fp, "reference_manifest": rman,
          "forward_manifest": fman, "reference_segment_policy": rpol, "forward_segment_policy": fpol,
          "threshold_policy": pol, "expected_baseline_hash": exp_base,
          "expected_threshold_policy_hash": _dsha(pol), "classification_mode": "TEST"}
    eb = build_phase4_expected_bindings_candidate(fdir, fman, fpol, di)["expected_bindings_candidate"]
    return {"session_dir": fdir, "manifest": fman, "segment_policy": fpol, "drift_inputs": di,
            "expected_bindings": eb, "ref_lifecycle": rlc, "fwd_lifecycle": flc}


@pytest.fixture(scope="module")
def uptrend(tmp_path_factory):
    root = tmp_path_factory.mktemp("uptrend")
    return _h025_inputs(root, [100 + 3 * i for i in range(30)], 30)


@pytest.fixture(scope="module")
def riskstop_root(tmp_path_factory):
    # risk-stop needs a low drawdown limit; build with a dedicated helper (max_drawdown 0.10)
    return tmp_path_factory.mktemp("riskstop")


# =========================================================================== #
# synthetic private-path artifact builder (PASS + status mappings + negatives) #
# =========================================================================== #
_MH_REF, _MH_FWD = "a" * 64, "b" * 64
_DS_REF, _DS_FWD = "1" * 64, "2" * 64


def _write_hex_journal(events, path, mh):
    ev = copy.deepcopy(events)
    ev[0]["payload"]["manifest_hash"] = mh
    j = Journal(str(path), mh)
    for e in ev:
        j.append(e["event_type"], e["payload"], timestamp_ms=int(e["timestamp"]), event_id=e["event_id"])
    return str(path)


def _synth_manifest_full(base, mh, ds, sid):
    m = dict(_synth_manifest())
    m.update({"manifest_hash": mh, "start_timestamp_ms": base, "dataset_hash": ds, "session_id": "ps-" + sid,
              "strategy": {"strategy_hash": "s" * 64}, "param_hash": "p" * 64, "asset": "BTC/USD",
              "instrument_type": "crypto_spot", "execution_profile_hash": "e" * 64,
              "absolute_risk_limits_hash": "r" * 64,
              "data_source": {"provider": "synthetic_test", "is_real": False}, "live_execution_enabled": False})
    return m


def _synth_case(tmp, ref_opens, fwd_opens, *, ds_ref=_DS_REF, ds_fwd=_DS_FWD, mode="TEST",
                exp_thr=None, exp_base_bad=False, fwd_shift_days=12):
    rp = _write_hex_journal(_synth(_rt2(ref_opens)), os.path.join(tmp, "ref.jsonl"), _MH_REF)
    fwd_bars = fwd_opens if (fwd_opens and isinstance(fwd_opens[0], dict)) else _rt2(fwd_opens)
    fwd_events = _shift(_synth(fwd_bars), fwd_shift_days * _DAY)
    fp = _write_hex_journal(fwd_events, os.path.join(tmp, "fwd.jsonl"), _MH_FWD)
    rman = _synth_manifest_full(_T0, _MH_REF, ds_ref, "ref")
    fman = _synth_manifest_full(_T0 + fwd_shift_days * _DAY, _MH_FWD, ds_fwd, "fwd")
    rpol = _policy([_seg("s1", _T0, _T0 + len(ref_opens) * _DAY)])
    fwd_n = len(fwd_bars)
    fpol = _policy([_seg("s1", _T0 + fwd_shift_days * _DAY, _T0 + (fwd_shift_days + fwd_n) * _DAY)])
    pol = _test_policy()
    exp_base = build_baseline_candidate_from_journal(rp, rman, rpol)["candidate_baseline_hash"]
    if exp_base_bad:
        exp_base = "0" * 64
    exp_thr = exp_thr if exp_thr is not None else _dsha(pol)
    di = {"reference_journal_path": rp, "forward_journal_path": fp, "reference_manifest": rman,
          "forward_manifest": fman, "reference_segment_policy": rpol, "forward_segment_policy": fpol,
          "threshold_policy": pol, "expected_baseline_hash": exp_base,
          "expected_threshold_policy_hash": exp_thr, "classification_mode": mode}
    wp3 = classify_drift_from_journals(rp, fp, rman, fman, rpol, fpol, pol, exp_base, exp_thr, mode if mode == "TEST" else "TEST")
    seg = build_segment_report_from_journal(fp, fpol, fman)
    cost = build_cost_analysis_from_journal(fp, fpol, fman)
    fev = read_verified_events(fp, _MH_FWD)
    rev = read_verified_events(rp, _MH_REF)
    jfh = fev[-1]["event_hash"]
    sj, sm, srh = (hashlib.sha256(b"j").hexdigest(), hashlib.sha256(b"m").hexdigest(),
                   hashlib.sha256(b"r").hexdigest())
    v2a = {"session_id": fman["session_id"], "manifest_hash": _MH_FWD, "final_event_hash": jfh,
           "journal_verified": True, "report_hash": srh}
    eb = {"forward_manifest_hash": _MH_FWD, "forward_journal_final_hash": jfh, "stage2a_report_hash": srh,
          "stage2a_json_sha256": sj, "stage2a_markdown_sha256": sm, "wp2b1_pipeline_hash": seg["pipeline_hash"],
          "wp2b2_result_hash": cost["result_hash"],
          "wp2b2_session_result_hash": cost["session"]["session_result_hash"],
          "wp2b3_report_hash": wp3["report_hash"], "wp2b3_economic_content_hash": wp3["economic_content_hash"],
          "wp2b3_dependency_contract_hash": wp3["dependency_contract_hash"],
          "wp2b4_dependency_contract_hash": G._dependency_contract_hash(),
          "reference_manifest_hash": _MH_REF, "reference_journal_final_hash": rev[-1]["event_hash"],
          "reference_dataset_hash": ds_ref, "forward_dataset_hash": ds_fwd,
          "expected_baseline_hash": exp_base, "expected_threshold_policy_hash": exp_thr,
          "threshold_policy_hash": _dsha(pol)}
    kwargs = dict(verified_stage2a=v2a, forward_events=fev, forward_manifest=fman,
                  forward_segment_policy=fpol, wp2b1=seg, wp2b2=cost,
                  drift_inputs=di, expected_bindings=eb, stage2a_json_sha256=sj, stage2a_markdown_sha256=sm)
    return kwargs, eb, di, v2a, wp3


def _run_priv(tmp, kwargs, **over):
    kw = dict(kwargs)
    kw.update(over)
    kw["output_dir"] = os.path.join(tmp, "out")
    return _build_phase4_final_gate_from_artifacts(**kw)


# =========================================================================== #
# 1. real H-025 pilots (public path) + corrupted-artifact pilot                #
# =========================================================================== #
def test_pilot1_real_h025_uptrend_needs_more_data(uptrend, tmp_path):
    out = str(tmp_path / "out")
    rep = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                  uptrend["drift_inputs"], uptrend["expected_bindings"], out)
    assert uptrend["fwd_lifecycle"] == "COMPLETED"
    assert rep["validity_status"] == "VALID" and rep["evidence_status"] == "PARTIAL"
    assert rep["phase4_gate_status"] == "FAILED" and rep["action_status"] == "NEEDS_MORE_DATA"
    assert rep["reason_code"] == "WP2B3_NEEDS_MORE_DATA"
    assert all(c["status"] == "PASS" for c in rep["checks"])
    assert rep["safety"] == {"live_execution_enabled": False, "release_eligible": False,
                             "live_execution_eligible": False, "production_allowlist_empty": True}
    # persisted set: exactly three artifacts + load round-trip
    persisted = sorted(p for p in os.listdir(out) if not p.startswith("."))
    assert persisted == ["phase4_final_gate.complete", "phase4_final_gate.json", "phase4_final_gate.md"]
    assert load_phase4_final_gate(out) == rep


def test_pilot1_uptrend_replay_identical(uptrend, tmp_path):
    a = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "a"))
    b = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "b"))
    assert a["final_evidence_hash"] == b["final_evidence_hash"]
    # byte-identical json/md/marker across different output directories
    for name in ("phase4_final_gate.json", "phase4_final_gate.md", "phase4_final_gate.complete"):
        ba = open(os.path.join(str(tmp_path / "a"), name), "rb").read()
        bb = open(os.path.join(str(tmp_path / "b"), name), "rb").read()
        assert ba == bb


def test_pilot2_real_h025_riskstop_needs_more_data(riskstop_root):
    # dedicated risk-stop sessions (max_drawdown 0.10)
    def rs_session(base, tag):
        strat = get_strategy("H-025"); prof = freeze_execution_profile("BTC/USD")
        rpol = freeze_range_policy(seed=42, block_length=3, n_resamples=100, min_unique_segments=6)
        segs = [{"segment_index": i, "metrics": {"return": 0.0, "drawdown": -0.02, "trade_count": 5.0,
                 "turnover": 1.0, "exposure_time": 0.5, "win_rate": 0.5, "avg_holding_period": 3.0,
                 "long_short_balance": 1.0}} for i in range(24)]
        ref = build_reference_ranges(segs, policy=rpol)
        closes = [100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0]
        opens = [closes[0]] + closes[:-1]
        fr = pd.DataFrame([{"open_time": base + i * _DAY, "open": float(opens[i]),
                            "high": max(opens[i], closes[i]) + 1.0, "low": max(0.01, min(opens[i], closes[i]) - 1.0),
                            "close": float(closes[i]), "volume": 1000.0} for i in range(len(closes))])
        man = build_session_manifest(strategy=strat, params={"length": 5}, asset="BTC/USD", timeframe="1d",
                                     data_source={"provider": "synthetic_test", "root": "n/a", "is_real": False},
                                     start_timestamp_ms=int(fr["open_time"].iloc[0]), initial_capital=100_000.0,
                                     position_sizing_policy={"type": "target_fraction_of_equity", "max_gross": 1.0},
                                     execution_profile=prof, absolute_risk_limits=freeze_risk_limits(max_drawdown=0.10),
                                     gap_stale_policy={"max_gap_bars": 2, "on_gap": "DATA_STOPPED"},
                                     regime_policy_hash=regime_policy_hash(DEFAULT_REGIME_POLICY),
                                     consistency_policy_hash=consistency_policy_hash(DEFAULT_CONSISTENCY_POLICY),
                                     reference_range_hash=ref["range_hash"], comparability_basis=ref["comparability_basis"],
                                     dataset_hash=dataset_hash(fr))
        d = os.path.join(str(riskstop_root), tag); os.makedirs(d)
        res = run_replay(manifest=man, profile=prof, risk_limits=freeze_risk_limits(max_drawdown=0.10),
                         strategy=strat, params={"length": 5}, frame=fr, journal_dir=d)
        path = os.path.join(d, "journal.jsonl")
        build_session_report(manifest=man, journal_path=path, output_dir=d)
        ev = read_verified_events(path, man["manifest_hash"])
        ots = sorted(e["payload"]["open_time"] for e in ev if e["event_type"] == "bar_ingested")
        pol = _policy([_seg("s1", ots[0], ots[-1] + _DAY)])
        return d, path, man, pol, res["lifecycle_status"]

    rdir, rp, rman, rpol, rlc = rs_session(_T0, "ref")
    fdir, fp, fman, fpol, flc = rs_session(_T0 + 40 * _DAY, "fwd")
    assert rlc == "RISK_STOPPED" and flc == "RISK_STOPPED"
    pol = _test_policy()
    exp_base = build_baseline_candidate_from_journal(rp, rman, rpol)["candidate_baseline_hash"]
    di = {"reference_journal_path": rp, "forward_journal_path": fp, "reference_manifest": rman,
          "forward_manifest": fman, "reference_segment_policy": rpol, "forward_segment_policy": fpol,
          "threshold_policy": pol, "expected_baseline_hash": exp_base,
          "expected_threshold_policy_hash": _dsha(pol), "classification_mode": "TEST"}
    eb = build_phase4_expected_bindings_candidate(fdir, fman, fpol, di)["expected_bindings_candidate"]
    out = os.path.join(str(riskstop_root), "gateout")
    rep = build_phase4_final_gate(fdir, fman, fpol, di, eb, out)
    rep2 = build_phase4_final_gate(fdir, fman, fpol, di, eb, os.path.join(str(riskstop_root), "gateout2"))
    assert rep["validity_status"] == "VALID" and rep["action_status"] == "NEEDS_MORE_DATA"
    assert rep["phase4_gate_status"] == "FAILED" and rep["reason_code"] == "WP2B3_NEEDS_MORE_DATA"
    assert rep["final_evidence_hash"] == rep2["final_evidence_hash"]
    assert rep["safety"]["live_execution_enabled"] is False


def test_pilot3_corrupted_artifact_fails_closed(uptrend, tmp_path):
    # copy the forward session dir and corrupt exactly one byte of the Stage-2A JSON
    corrupt_dir = str(tmp_path / "corrupt_session")
    shutil.copytree(uptrend["session_dir"], corrupt_dir)
    jpath = os.path.join(corrupt_dir, "session_report.json")
    data = bytearray(open(jpath, "rb").read())
    data[len(data) // 2] ^= 0x01
    open(jpath, "wb").write(bytes(data))
    di = dict(uptrend["drift_inputs"])
    rep = build_phase4_final_gate(corrupt_dir, uptrend["manifest"], uptrend["segment_policy"], di,
                                  uptrend["expected_bindings"], str(tmp_path / "cout"))
    assert rep["validity_status"] == "INVALID" and rep["phase4_gate_status"] == "FAILED"
    assert rep["action_status"] == "INVALID" and rep["reason_code"] == "REPORT_SET_INVALID"
    # Stage-2A failed before AUTHORITATIVE_SESSION was consulted
    ch = {c["check_id"]: c["status"] for c in rep["checks"]}
    assert ch["STAGE2A_REPORT_SET"] == "FAIL" and ch["AUTHORITATIVE_SESSION"] == "NOT_EVALUATED"


# =========================================================================== #
# 2. SYNTHETIC CONTRACT PASS pilot (clearly synthetic; NOT H-025/real/profit)   #
# =========================================================================== #
def test_pilot4_synthetic_contract_pass(tmp_path):
    kwargs, eb, di, v2a, wp3 = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    assert wp3["validity_status"] == "VALID" and wp3["action_status"] == "WITHIN_THRESHOLD" \
        and wp3["evidence_status"] == "SUFFICIENT"
    rep = _run_priv(str(tmp_path), kwargs)
    assert rep["validity_status"] == "VALID" and rep["evidence_status"] == "COMPLETE"
    assert rep["phase4_gate_status"] == "PASSED" and rep["action_status"] == "PHASE4_TECHNICALLY_COMPLETE"
    assert rep["reason_code"] is None
    assert all(c["status"] == "PASS" for c in rep["checks"])
    assert rep["safety"] == {"live_execution_enabled": False, "release_eligible": False,
                             "live_execution_eligible": False, "production_allowlist_empty": True}
    assert load_phase4_final_gate(os.path.join(str(tmp_path), "out")) == rep


# =========================================================================== #
# 3. WP2B-3 status mapping                                                      #
# =========================================================================== #
def test_map_drift(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, _ovl([100, 110] * 6))
    rep = _run_priv(str(tmp_path), kwargs)
    assert rep["phase4_gate_status"] == "FAILED" and rep["action_status"] == "DRIFT_REVIEW_REQUIRED"
    assert rep["reason_code"] == "WP2B3_DRIFT" and rep["validity_status"] == "VALID"


def test_map_warning(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 103] * 6, [100, 112] * 6)
    rep = _run_priv(str(tmp_path), kwargs)
    assert rep["action_status"] == "WARNING_REVIEW_REQUIRED" and rep["reason_code"] == "WP2B3_WARNING"


def test_map_needs_more_data(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110, 100, 110], [100, 110, 100, 110])
    rep = _run_priv(str(tmp_path), kwargs)
    assert rep["action_status"] == "NEEDS_MORE_DATA" and rep["reason_code"] == "WP2B3_NEEDS_MORE_DATA"


def test_map_invalid(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6, exp_base_bad=True)
    rep = _run_priv(str(tmp_path), kwargs)
    assert rep["validity_status"] == "INVALID" and rep["action_status"] == "INVALID"
    assert rep["reason_code"] == "WP2B3_INVALID"


# =========================================================================== #
# 4. dependency contract + expected-bindings schema/immutability               #
# =========================================================================== #
def test_dependency_contract_mismatch(tmp_path, monkeypatch):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    monkeypatch.setitem(G.WP2B4_DEPENDENCY_CONTRACT, "metrics_v2.py", "0" * 64)
    rep = _run_priv(str(tmp_path), kwargs)
    assert rep["reason_code"] == "DEPENDENCY_CONTRACT_MISMATCH" and rep["phase4_gate_status"] == "FAILED"


@pytest.mark.parametrize("field", list(EXPECTED_BINDINGS_FIELDS))
def test_expected_bindings_malformed_missing(tmp_path, field):
    kwargs, eb, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    bad = dict(eb); bad.pop(field)
    rep = _run_priv(str(tmp_path), kwargs, expected_bindings=bad)
    assert rep["reason_code"] == "EXPECTED_BINDINGS_MALFORMED"


@pytest.mark.parametrize("val", ["not-hex", "A" * 64, "1" * 63, 123, None])
def test_expected_bindings_malformed_type(tmp_path, val):
    kwargs, eb, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    bad = dict(eb); bad["forward_manifest_hash"] = val
    rep = _run_priv(str(tmp_path), kwargs, expected_bindings=bad)
    assert rep["reason_code"] == "EXPECTED_BINDINGS_MALFORMED"


def test_missing_expected_bindings_object(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    rep = _run_priv(str(tmp_path), kwargs, expected_bindings={})
    assert rep["reason_code"] == "EXPECTED_BINDINGS_MALFORMED"


@pytest.mark.parametrize("field", ["forward_journal_final_hash", "wp2b1_pipeline_hash", "wp2b3_report_hash",
                                   "reference_manifest_hash", "stage2a_report_hash"])
def test_expected_binding_mismatch(tmp_path, field):
    kwargs, eb, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    bad = dict(eb); bad[field] = "f" * 64
    rep = _run_priv(str(tmp_path), kwargs, expected_bindings=bad)
    assert rep["reason_code"] == "EXPECTED_BINDING_MISMATCH"


def test_candidate_helper_returns_approved_false(uptrend):
    out = build_phase4_expected_bindings_candidate(uptrend["session_dir"], uptrend["manifest"],
                                                   uptrend["segment_policy"], uptrend["drift_inputs"])
    assert out["approved"] is False and "expected_bindings_candidate" in out
    assert set(out["expected_bindings_candidate"].keys()) == set(EXPECTED_BINDINGS_FIELDS)


def test_gate_never_writes_or_modifies_expected_bindings(tmp_path):
    kwargs, eb, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    before = copy.deepcopy(eb)
    _run_priv(str(tmp_path), kwargs, expected_bindings=eb)
    assert eb == before      # unchanged after the call


def test_gate_does_not_call_candidate_generation(tmp_path, monkeypatch):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    called = {"n": 0}
    real = G.build_phase4_expected_bindings_candidate

    def spy(*a, **k):
        called["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(G, "build_phase4_expected_bindings_candidate", spy)
    _run_priv(str(tmp_path), kwargs)
    assert called["n"] == 0


# =========================================================================== #
# 5. input structure (raise + publish nothing)                                 #
# =========================================================================== #
def test_nonpath_input_raises(uptrend, tmp_path):
    with pytest.raises(Phase4GateError):
        build_phase4_final_gate({"not": "path"}, uptrend["manifest"], uptrend["segment_policy"],
                                uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "o"))
    assert load_phase4_final_gate(str(tmp_path / "o")) is None


def test_report_bundle_raises(uptrend, tmp_path):
    di = dict(uptrend["drift_inputs"]); di["forward_journal_path"] = {"events": []}
    with pytest.raises(Phase4GateError):
        build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                di, uptrend["expected_bindings"], str(tmp_path / "o"))


def test_classification_mode_not_test_raises(uptrend, tmp_path):
    di = dict(uptrend["drift_inputs"]); di["classification_mode"] = "PRODUCTION"
    with pytest.raises(Phase4GateError):
        build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                di, uptrend["expected_bindings"], str(tmp_path / "o"))
    assert load_phase4_final_gate(str(tmp_path / "o")) is None


# =========================================================================== #
# 6. artifact presence / journal verification / stage2a precedence (public)     #
# =========================================================================== #
@pytest.mark.parametrize("art", ["journal.jsonl", "session_report.json", "session_report.md",
                                 "session_report.complete"])
def test_artifact_missing(uptrend, tmp_path, art):
    sd = str(tmp_path / "sess"); shutil.copytree(uptrend["session_dir"], sd)
    os.remove(os.path.join(sd, art))
    rep = build_phase4_final_gate(sd, uptrend["manifest"], uptrend["segment_policy"],
                                  uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "o"))
    ch = {c["check_id"]: c["status"] for c in rep["checks"]}
    if art == "journal.jsonl":
        assert rep["reason_code"] == "ARTIFACT_MISSING"
    else:
        assert rep["reason_code"] == "ARTIFACT_MISSING"
        assert ch["STAGE2A_REPORT_SET"] == "NOT_EVALUATED"     # presence fails before Stage-2A


def test_corrupted_journal(uptrend, tmp_path):
    sd = str(tmp_path / "sess"); shutil.copytree(uptrend["session_dir"], sd)
    jp = os.path.join(sd, "journal.jsonl")
    data = bytearray(open(jp, "rb").read()); data[10] ^= 0x01; open(jp, "wb").write(bytes(data))
    rep = build_phase4_final_gate(sd, uptrend["manifest"], uptrend["segment_policy"],
                                  uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "o"))
    assert rep["reason_code"] == "JOURNAL_VERIFICATION_FAILED"


def test_stage2a_verified_before_identity_use(uptrend, tmp_path):
    # tamper Stage-2A JSON (also perturbs its session_id) -> must fail REPORT_SET_INVALID, not AUTH
    sd = str(tmp_path / "sess"); shutil.copytree(uptrend["session_dir"], sd)
    jp = os.path.join(sd, "session_report.json")
    d = json.loads(open(jp).read()); d["session_id"] = "ps-tampered"
    open(jp, "w").write(json.dumps(d))
    rep = build_phase4_final_gate(sd, uptrend["manifest"], uptrend["segment_policy"],
                                  uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "o"))
    ch = {c["check_id"]: c["status"] for c in rep["checks"]}
    assert rep["reason_code"] == "REPORT_SET_INVALID"
    assert ch["STAGE2A_REPORT_SET"] == "FAIL" and ch["AUTHORITATIVE_SESSION"] == "NOT_EVALUATED"


# =========================================================================== #
# 7. authoritative-session + identity/dataset (private path)                    #
# =========================================================================== #
def test_authoritative_session_wrong_session(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    v2a = dict(kwargs["verified_stage2a"]); v2a["session_id"] = "ps-somebody-else"
    rep = _run_priv(str(tmp_path), kwargs, verified_stage2a=v2a)
    assert rep["reason_code"] == "AUTHORITATIVE_SESSION_MISMATCH"


def test_authoritative_session_journal_hash_mismatch(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    v2a = dict(kwargs["verified_stage2a"]); v2a["final_event_hash"] = "c" * 64
    rep = _run_priv(str(tmp_path), kwargs, verified_stage2a=v2a)
    assert rep["reason_code"] == "AUTHORITATIVE_SESSION_MISMATCH"


@pytest.mark.parametrize("ds", [None, "bad", "A" * 64, "1" * 63])
def test_dataset_identity_invalid(tmp_path, ds):
    kwargs, eb, di, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    fman = dict(kwargs["forward_manifest"]); fman["dataset_hash"] = ds
    bad_eb = dict(eb); bad_eb["forward_dataset_hash"] = ds if G._is_hex64(ds) else "2" * 64
    # keep eb schema valid (hex64) so we reach IDENTITY_BINDING, then the manifest dataset is invalid
    rep = _run_priv(str(tmp_path), kwargs, forward_manifest=fman, expected_bindings=bad_eb)
    assert rep["reason_code"] == "DATASET_IDENTITY_INVALID"


def test_binding_mismatch_equal_fields(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    fman = copy.deepcopy(kwargs["forward_manifest"]); fman["asset"] = "ETH/USD"   # differs from reference
    rep = _run_priv(str(tmp_path), kwargs, forward_manifest=fman)
    assert rep["reason_code"] == "BINDING_MISMATCH"


# =========================================================================== #
# 8. safety invariants                                                         #
# =========================================================================== #
def test_safety_live_execution_true(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    fman = copy.deepcopy(kwargs["forward_manifest"]); fman["live_execution_enabled"] = True
    rep = _run_priv(str(tmp_path), kwargs, forward_manifest=fman)
    assert rep["reason_code"] == "LIVE_EXECUTION_ENABLED"


def test_safety_allowlist_nonempty(tmp_path, monkeypatch):
    # F2 (HARDENING R2): the gate reads the allowlist from the AUTHORITATIVE runtime module object
    # `G._drift.APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES`, never a by-value snapshot. Monkeypatching
    # the module attribute (not any local alias) must fail the gate closed.
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    assert not hasattr(G, "APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES")   # no local snapshot exists
    monkeypatch.setattr(G._drift, "APPROVED_PRODUCTION_THRESHOLD_POLICY_HASHES", ("x" * 64,))
    rep = _run_priv(str(tmp_path), kwargs)
    ch = _ck(rep)
    assert rep["reason_code"] == "PRODUCTION_NOT_SUPPORTED_V4" and ch["SAFETY_INVARIANTS"] == "FAIL"
    assert ch["EXPECTED_BINDING_MATCH"] == "NOT_EVALUATED"
    _assert_check_invariant(rep)


def test_safety_global_live_execution_kill_switch(tmp_path, monkeypatch):
    # F3 (HARDENING R2): even with every manifest/journal live flag False, a process-wide enablement of
    # the authoritative paper-session global must fail the gate closed at step 12 SAFETY_INVARIANTS.
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    assert kwargs["forward_manifest"]["live_execution_enabled"] is False   # manifests remain False
    monkeypatch.setattr(G._psession, "LIVE_EXECUTION_ENABLED", True)
    rep = _run_priv(str(tmp_path), kwargs)
    ch = _ck(rep)
    assert rep["reason_code"] == "LIVE_EXECUTION_ENABLED" and ch["SAFETY_INVARIANTS"] == "FAIL"
    assert ch["EXPECTED_BINDING_MATCH"] == "NOT_EVALUATED" and ch["WP2B3_STATUS_MAPPING"] == "NOT_EVALUATED"
    assert rep["safety"]["release_eligible"] is False and rep["safety"]["live_execution_eligible"] is False
    assert rep["safety"]["live_execution_enabled"] is False
    _assert_check_invariant(rep)


def test_dataset_pin_precedence_at_step13_not_step8(tmp_path):
    # F1 (HARDENING R2): both manifests carry valid concrete lowercase-hex64 dataset hashes, so step 8
    # IDENTITY_BINDING PASSES. A DIFFERENT but well-formed hex64 forward_dataset_hash pinned in the
    # expected_bindings must surface ONLY at step 13 EXPECTED_BINDING_MATCH (dataset-pin precedence),
    # after WP2B-1/2/3 reproduction and SAFETY_INVARIANTS have all executed.
    kwargs, eb, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    bad_eb = dict(eb); bad_eb["forward_dataset_hash"] = "d" * 64          # valid hex64, != manifest dataset
    assert kwargs["forward_manifest"]["dataset_hash"] == _DS_FWD           # manifest dataset stays concrete/valid
    rep = _run_priv(str(tmp_path), kwargs, expected_bindings=bad_eb)
    ch = _ck(rep)
    assert ch["IDENTITY_BINDING"] == "PASS"
    for cid in ("WP2B1_REPRODUCTION", "WP2B2_REPRODUCTION", "WP2B3_REPRODUCTION", "SAFETY_INVARIANTS"):
        assert ch[cid] == "PASS"                                          # all run before the pin comparison
    assert ch["EXPECTED_BINDING_MATCH"] == "FAIL"
    assert rep["reason_code"] == "EXPECTED_BINDING_MISMATCH"
    _assert_check_invariant(rep)


def test_dataset_pin_reference_mismatch_at_step13(tmp_path):
    # F1 companion: a mismatched reference_dataset_hash pin (both manifests still valid/concrete) also
    # surfaces at step 13, never as a step-8 DATASET_IDENTITY_INVALID.
    kwargs, eb, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    bad_eb = dict(eb); bad_eb["reference_dataset_hash"] = "e" * 64
    rep = _run_priv(str(tmp_path), kwargs, expected_bindings=bad_eb)
    ch = _ck(rep)
    assert ch["IDENTITY_BINDING"] == "PASS" and ch["SAFETY_INVARIANTS"] == "PASS"
    assert ch["EXPECTED_BINDING_MATCH"] == "FAIL" and rep["reason_code"] == "EXPECTED_BINDING_MISMATCH"
    _assert_check_invariant(rep)


# =========================================================================== #
# 9. deterministic double-build + crash-safety + dir-fsync                      #
# =========================================================================== #
def test_deterministic_double_build_divergence(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    rep = _run_priv(str(tmp_path), kwargs, _fault="nondeterministic")
    assert rep["reason_code"] == "NONDETERMINISTIC_REPLAY"


@pytest.mark.parametrize("fault", ["after_json_stage", "after_md_stage", "between_publish"])
def test_interrupted_write_leaves_no_valid_marker(tmp_path, fault):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out")
    with pytest.raises(G._InjectedFault):
        _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out, _fault=fault))
    assert load_phase4_final_gate(out) is None


def test_tampered_marker_and_md_load_none(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out")
    _run_priv(str(tmp_path), kwargs)
    # tamper markdown -> load None
    mp = os.path.join(out, "phase4_final_gate.md")
    open(mp, "ab").write(b"x")
    assert load_phase4_final_gate(out) is None


@pytest.mark.parametrize("ec", [errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
                                errno.ENOSYS, errno.EINVAL])
def test_dir_fsync_recognized_unsupported_byte_identical(tmp_path, monkeypatch, ec):
    # baseline with real dir fsync
    os.makedirs(str(tmp_path / "a")); os.makedirs(str(tmp_path / "b"))
    ka, *_ = _synth_case(str(tmp_path / "a"), [100, 110] * 6, [100, 109] * 6)
    a = _run_priv(str(tmp_path / "a"), ka)
    # drive the REAL _fsync_dir classification: os.open(O_DIRECTORY) raises a recognized-unsupported errno
    real_open = os.open

    def topen(path, flags, *aa):
        if flags & os.O_DIRECTORY:
            raise OSError(ec, "unsupported")
        return real_open(path, flags, *aa)
    monkeypatch.setattr(os, "open", topen)
    kb, *_ = _synth_case(str(tmp_path / "b"), [100, 110] * 6, [100, 109] * 6)
    b = _run_priv(str(tmp_path / "b"), kb)
    assert a["final_evidence_hash"] == b["final_evidence_hash"]
    for name in ("phase4_final_gate.json", "phase4_final_gate.md", "phase4_final_gate.complete"):
        ba = open(os.path.join(str(tmp_path / "a"), "out", name), "rb").read()
        bb = open(os.path.join(str(tmp_path / "b"), "out", name), "rb").read()
        assert ba == bb


@pytest.mark.parametrize("ec", [errno.EACCES, errno.EPERM, errno.EIO, errno.ENOSPC, errno.EROFS, 9999])
def test_dir_fsync_error_aborts_no_marker(tmp_path, monkeypatch, ec):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out")
    real_open = os.open

    def topen(path, flags, *aa):
        if flags & os.O_DIRECTORY:
            raise OSError(ec, "boom")
        return real_open(path, flags, *aa)
    monkeypatch.setattr(os, "open", topen)
    with pytest.raises(OSError):
        _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out))
    assert load_phase4_final_gate(out) is None


def test_dir_fsync_descriptor_closed(tmp_path, monkeypatch):
    opened, closed = [], []
    real_open, real_close = os.open, os.close

    def topen(path, flags, *a):
        fd = real_open(path, flags, *a); opened.append(fd); return fd

    def tclose(fd):
        closed.append(fd); return real_close(fd)
    monkeypatch.setattr(os, "open", topen)
    monkeypatch.setattr(os, "close", tclose)
    # success path: descriptor opened for the dir must be closed
    d = str(tmp_path / "d"); os.makedirs(d)
    G._fsync_dir(d)
    assert opened and opened[-1] in closed

    # when fsync raises, the descriptor is still closed (finally)
    def bad_fsync(fd):
        raise OSError(errno.EIO, "io")
    monkeypatch.setattr(os, "fsync", bad_fsync)
    opened.clear(); closed.clear()
    with pytest.raises(OSError):
        G._fsync_dir(d)
    assert opened and opened[-1] in closed


def test_checks_have_no_detail_or_paths(uptrend, tmp_path):
    out = str(tmp_path / "o")
    rep = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                  uptrend["drift_inputs"], uptrend["expected_bindings"], out)
    for c in rep["checks"]:
        assert set(c.keys()) == {"check_id", "status"}
    blob = open(os.path.join(out, "phase4_final_gate.json")).read()
    assert "/tmp" not in blob and out not in blob     # no path contamination in persisted artifact


def test_persisted_gate_status_only_passed_or_failed(uptrend, tmp_path):
    rep = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                  uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "o"))
    assert rep["phase4_gate_status"] in ("PASSED", "FAILED")


# =========================================================================== #
# 15. HARDENING R1 — precedence (F1) + check-state integrity (F7)               #
# =========================================================================== #
def _ck(rep):
    return {c["check_id"]: c["status"] for c in rep["checks"]}


def _assert_check_invariant(rep):
    order = [c["check_id"] for c in rep["checks"]]
    assert order == list(G.CHECK_IDS)                       # exact persisted order
    st = [c["status"] for c in rep["checks"]]
    if "FAIL" not in st:
        assert all(s == "PASS" for s in st)                 # clean mapping -> all PASS
        return
    i = st.index("FAIL")
    assert all(s == "PASS" for s in st[:i])                 # everything before first FAIL is PASS
    assert st.count("FAIL") == 1                            # exactly one FAIL
    assert all(s == "NOT_EVALUATED" for s in st[i + 1:])    # everything after is NOT_EVALUATED


def test_authoritative_mismatch_plus_malformed_segment_policy_fails_at_step7(uptrend, tmp_path):
    di = dict(uptrend["drift_inputs"])
    bad_pol = _policy([_seg("s1", _T0, _T0 + 5 * _DAY)])     # != authoritative forward policy
    di["forward_segment_policy"] = bad_pol
    rep = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                  di, uptrend["expected_bindings"], str(tmp_path / "o"))
    ch = _ck(rep)
    assert rep["reason_code"] == "AUTHORITATIVE_SESSION_MISMATCH" and ch["AUTHORITATIVE_SESSION"] == "FAIL"
    assert ch["WP2B1_REPRODUCTION"] == "NOT_EVALUATED"
    _assert_check_invariant(rep)


def test_wp2b1_failure_leaves_10_15_not_evaluated(uptrend, tmp_path):
    # a multi-segment forward policy passes steps 7-8 but yields 2 WP2B-1 segments -> step 9 FAIL
    two = _policy([_seg("s1", _T0 + 30 * _DAY, _T0 + 45 * _DAY),
                   _seg("s2", _T0 + 45 * _DAY, _T0 + 60 * _DAY)])
    di = dict(uptrend["drift_inputs"]); di["forward_segment_policy"] = two
    rep = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], two, di,
                                  uptrend["expected_bindings"], str(tmp_path / "o"))
    ch = _ck(rep)
    assert ch["WP2B1_REPRODUCTION"] == "FAIL" and rep["reason_code"] == "UPSTREAM_HASH_MISMATCH"
    for cid in ("WP2B2_REPRODUCTION", "WP2B3_REPRODUCTION", "SAFETY_INVARIANTS", "EXPECTED_BINDING_MATCH",
                "DETERMINISTIC_DOUBLE_BUILD", "WP2B3_STATUS_MAPPING"):
        assert ch[cid] == "NOT_EVALUATED"
    _assert_check_invariant(rep)


def test_wp2b2_failure_leaves_11_15_not_evaluated(uptrend, tmp_path, monkeypatch):
    def raiser(*a, **k):
        raise CostAnalysisError("forced")
    monkeypatch.setattr(G, "build_cost_analysis_from_journal", raiser)
    rep = build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                  uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "o"))
    ch = _ck(rep)
    assert ch["WP2B1_REPRODUCTION"] == "PASS" and ch["WP2B2_REPRODUCTION"] == "FAIL"
    assert rep["reason_code"] == "UPSTREAM_HASH_MISMATCH"
    for cid in ("WP2B3_REPRODUCTION", "SAFETY_INVARIANTS", "EXPECTED_BINDING_MATCH",
                "DETERMINISTIC_DOUBLE_BUILD", "WP2B3_STATUS_MAPPING"):
        assert ch[cid] == "NOT_EVALUATED"
    _assert_check_invariant(rep)


def test_check_invariant_across_representative_reports(uptrend, tmp_path):
    reps = [build_phase4_final_gate(uptrend["session_dir"], uptrend["manifest"], uptrend["segment_policy"],
                                    uptrend["drift_inputs"], uptrend["expected_bindings"], str(tmp_path / "ok"))]
    s1 = str(tmp_path / "s1"); os.makedirs(s1)
    kwargs, *_ = _synth_case(s1, [100, 110] * 6, [100, 109] * 6)
    reps.append(_run_priv(s1, kwargs))            # PASS
    for r in reps:
        _assert_check_invariant(r)


# =========================================================================== #
# 16. HARDENING R1 — authoritative + temporal bindings (F2)                     #
# =========================================================================== #
def test_forward_journal_differs_from_authoritative(tmp_path):
    kwargs, eb, di, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    di2 = dict(di); di2["forward_journal_path"] = di["reference_journal_path"]   # points elsewhere
    rep = _run_priv(str(tmp_path), kwargs, drift_inputs=di2)
    assert rep["reason_code"] == "AUTHORITATIVE_SESSION_MISMATCH"


def test_same_journal_reused_as_reference_and_forward(tmp_path):
    kwargs, eb, di, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    di2 = dict(di); di2["reference_journal_path"] = di["forward_journal_path"]
    di2["reference_manifest"] = kwargs["forward_manifest"]
    rep = _run_priv(str(tmp_path), kwargs, drift_inputs=di2)
    assert rep["reason_code"] == "AUTHORITATIVE_SESSION_MISMATCH"


def test_forward_starts_before_reference_cutoff(tmp_path):
    # forward shifted only 6 days -> overlaps the 12-bar reference cutoff
    kwargs, eb, di, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6, fwd_shift_days=6)
    rep = _run_priv(str(tmp_path), kwargs)
    ch = _ck(rep)
    assert rep["reason_code"] == "BINDING_MISMATCH" and ch["IDENTITY_BINDING"] == "FAIL"


@pytest.mark.parametrize("rs,rc,fs,fe", [
    (True, 10, 20, 30), (1.0, 10, 20, 30), ("x", 10, 20, 30), (float("nan"), 10, 20, 30),
    (None, 10, 20, 30), (10, 10, 20, 30), (20, 10, 25, 30), (10, 20, 15, 25), (10, 20, 25, 25),
])
def test_validate_periods_rejects_malformed(rs, rc, fs, fe):
    assert G._validate_periods(rs, rc, fs, fe) == "BINDING_MISMATCH"


def test_validate_periods_accepts_valid():
    assert G._validate_periods(0, 12, 12, 24) is None


# =========================================================================== #
# 17. HARDENING R1 — closed WP2B-3 vocabulary (F3)                              #
# =========================================================================== #
_GOOD_WP3 = {"validity_status": "VALID", "evidence_status": "SUFFICIENT",
             "drift_status": "WITHIN_THRESHOLD", "action_status": "WITHIN_THRESHOLD"}


@pytest.mark.parametrize("field", ["validity_status", "evidence_status", "drift_status", "action_status"])
@pytest.mark.parametrize("bad", [None, True, "UNKNOWN", "__missing__"])
def test_wp3_vocab_bad_token(field, bad):
    w = dict(_GOOD_WP3)
    if bad == "__missing__":
        del w[field]
    else:
        w[field] = bad
    assert G._validate_wp3_vocab(w) == "METRIC_INVALID"


@pytest.mark.parametrize("w", [
    # original R1 contradictions (retained)
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "WITHIN_THRESHOLD", "action_status": "INVALID"},
    {"validity_status": "INVALID", "evidence_status": "SUFFICIENT", "drift_status": "WITHIN_THRESHOLD", "action_status": "WITHIN_THRESHOLD"},
    {"validity_status": "VALID", "evidence_status": "PARTIAL", "drift_status": "WITHIN_THRESHOLD", "action_status": "WITHIN_THRESHOLD"},
    # F4 (HARDENING R2) — the five REQUIRED adversarial cross-field contradictions
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "DRIFT", "action_status": "WITHIN_THRESHOLD"},            # drift=DRIFT / action=WITHIN_THRESHOLD
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "WITHIN_THRESHOLD", "action_status": "DRIFT"},            # drift=WITHIN_THRESHOLD / action=DRIFT
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "WARNING", "action_status": "WITHIN_THRESHOLD"},          # drift=WARNING / action=WITHIN_THRESHOLD
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "WARNING", "action_status": "NEEDS_MORE_DATA"},           # evidence=SUFFICIENT / action=NEEDS_MORE_DATA
    {"validity_status": "VALID", "evidence_status": "INSUFFICIENT", "drift_status": "WITHIN_THRESHOLD", "action_status": "WITHIN_THRESHOLD"},# evidence=INSUFFICIENT / action=WITHIN_THRESHOLD
    # additional cross-field contradictions the closed reachable-set also rejects
    {"validity_status": "VALID", "evidence_status": "INSUFFICIENT", "drift_status": "DRIFT", "action_status": "DRIFT"},                     # DRIFT can never be INSUFFICIENT
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "NOT_EVALUATED", "action_status": "NEEDS_MORE_DATA"},      # NEEDS_MORE_DATA can never be SUFFICIENT
    {"validity_status": "VALID", "evidence_status": "PARTIAL", "drift_status": "WITHIN_THRESHOLD", "action_status": "NEEDS_MORE_DATA"},      # NEEDS_MORE_DATA drift is WARNING/NOT_EVALUATED, never WITHIN_THRESHOLD
    {"validity_status": "INVALID", "evidence_status": "INSUFFICIENT", "drift_status": "NOT_EVALUATED", "action_status": "DRIFT"},            # INVALID validity requires INVALID action
])
def test_wp3_vocab_contradictions(w):
    assert G._validate_wp3_vocab(w) == "METRIC_INVALID"


@pytest.mark.parametrize("w", [
    # every tuple the frozen WP2B-3 classifier can actually emit must pass
    {"validity_status": "INVALID", "evidence_status": "INSUFFICIENT", "drift_status": "NOT_EVALUATED", "action_status": "INVALID"},
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "WITHIN_THRESHOLD", "action_status": "WITHIN_THRESHOLD"},
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "WARNING", "action_status": "WARNING"},
    {"validity_status": "VALID", "evidence_status": "SUFFICIENT", "drift_status": "DRIFT", "action_status": "DRIFT"},
    {"validity_status": "VALID", "evidence_status": "PARTIAL", "drift_status": "DRIFT", "action_status": "DRIFT"},            # frozen DRIFT-over-insufficient (DRIFT + PARTIAL)
    {"validity_status": "VALID", "evidence_status": "PARTIAL", "drift_status": "WARNING", "action_status": "NEEDS_MORE_DATA"},
    {"validity_status": "VALID", "evidence_status": "PARTIAL", "drift_status": "NOT_EVALUATED", "action_status": "NEEDS_MORE_DATA"},
    {"validity_status": "VALID", "evidence_status": "INSUFFICIENT", "drift_status": "NOT_EVALUATED", "action_status": "NEEDS_MORE_DATA"},
])
def test_wp3_vocab_valid_combos(w):
    assert G._validate_wp3_vocab(w) is None


def test_unknown_wp3_status_fails_closed_integration(tmp_path, monkeypatch):
    kwargs, eb, di, v2a, wp3 = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    real = classify_drift_from_journals

    def bad(*a, **k):
        r = dict(real(*a, **k)); r["action_status"] = "INVALID"    # VALID + action INVALID -> contradiction
        return r
    monkeypatch.setattr(G, "classify_drift_from_journals", bad)
    rep = _run_priv(str(tmp_path), kwargs)
    ch = _ck(rep)
    assert rep["validity_status"] == "INVALID" and rep["action_status"] == "INVALID"
    assert rep["reason_code"] == "METRIC_INVALID" and ch["WP2B3_STATUS_MAPPING"] == "FAIL"


@pytest.mark.parametrize("contra", [
    {"drift_status": "DRIFT", "action_status": "WITHIN_THRESHOLD"},
    {"drift_status": "WITHIN_THRESHOLD", "action_status": "DRIFT"},
    {"drift_status": "WARNING", "action_status": "WITHIN_THRESHOLD"},
    {"evidence_status": "SUFFICIENT", "action_status": "NEEDS_MORE_DATA", "drift_status": "WARNING"},
    {"evidence_status": "INSUFFICIENT", "action_status": "WITHIN_THRESHOLD", "drift_status": "WITHIN_THRESHOLD"},
])
def test_wp3_contradiction_fails_closed_integration(tmp_path, monkeypatch, contra):
    # F4 (HARDENING R2): each required adversarial contradiction, injected into an otherwise-valid
    # WP2B-3 report, must fail the gate closed at step 15 with a single canonical outcome and NO
    # production/live eligibility.
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    real = classify_drift_from_journals

    def bad(*a, **k):
        r = dict(real(*a, **k)); r.update(contra); return r
    monkeypatch.setattr(G, "classify_drift_from_journals", bad)
    rep = _run_priv(str(tmp_path), kwargs)
    ch = _ck(rep)
    assert ch["WP2B3_STATUS_MAPPING"] == "FAIL"
    assert rep["validity_status"] == "INVALID" and rep["action_status"] == "INVALID"
    assert rep["phase4_gate_status"] == "FAILED" and rep["reason_code"] == "METRIC_INVALID"
    assert rep["safety"]["release_eligible"] is False and rep["safety"]["live_execution_eligible"] is False
    assert rep["safety"]["live_execution_enabled"] is False
    _assert_check_invariant(rep)


# =========================================================================== #
# 18. HARDENING R1 — loader marker-identity + schema (F4)                       #
# =========================================================================== #
def _published(tmp):
    kwargs, *_ = _synth_case(str(tmp), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp), "out")
    _run_priv(str(tmp), kwargs)
    return out


def _resign(out, rep, *, marker_over=None):
    body = {k: v for k, v in rep.items() if k != "final_evidence_hash"}
    rep["final_evidence_hash"] = G._sha(body)
    jt = G._canonical_json(rep) + "\n"
    md = G._render_markdown(rep)
    open(os.path.join(out, "phase4_final_gate.json"), "w").write(jt)
    open(os.path.join(out, "phase4_final_gate.md"), "w").write(md)
    marker = {"schema_version": rep["schema_version"], "final_evidence_hash": rep["final_evidence_hash"],
              "json_sha256": hashlib.sha256(jt.encode()).hexdigest(),
              "markdown_sha256": hashlib.sha256(md.encode()).hexdigest(),
              "manifest_hash": rep["forward_identity"]["manifest_hash"], "session_id": rep["session_id"]}
    if marker_over:
        marker.update(marker_over)
    open(os.path.join(out, "phase4_final_gate.complete"), "w").write(G._canonical_json(marker) + "\n")


def test_load_ok_baseline(tmp_path):
    out = _published(tmp_path)
    assert load_phase4_final_gate(out) is not None


def test_load_marker_manifest_changed_none(tmp_path):
    out = _published(tmp_path); rep = load_phase4_final_gate(out)
    _resign(out, rep, marker_over={"manifest_hash": "z" * 64})
    assert load_phase4_final_gate(out) is None


def test_load_marker_session_changed_none(tmp_path):
    out = _published(tmp_path); rep = load_phase4_final_gate(out)
    _resign(out, rep, marker_over={"session_id": "ps-other"})
    assert load_phase4_final_gate(out) is None


def test_load_marker_schema_changed_none(tmp_path):
    out = _published(tmp_path); rep = load_phase4_final_gate(out)
    _resign(out, rep, marker_over={"schema_version": "wrong"})
    assert load_phase4_final_gate(out) is None


def test_load_report_session_changed_none(tmp_path):
    out = _published(tmp_path); rep = load_phase4_final_gate(out)
    old = rep["session_id"]; rep["session_id"] = "ps-changed"
    _resign(out, rep, marker_over={"session_id": old})     # marker keeps old session id
    assert load_phase4_final_gate(out) is None


def test_load_reordered_checks_none(tmp_path):
    out = _published(tmp_path); rep = load_phase4_final_gate(out)
    rep["checks"] = list(reversed(rep["checks"]))
    _resign(out, rep)
    assert load_phase4_final_gate(out) is None


def test_load_missing_check_none(tmp_path):
    out = _published(tmp_path); rep = load_phase4_final_gate(out)
    rep["checks"] = rep["checks"][:-1]
    _resign(out, rep)
    assert load_phase4_final_gate(out) is None


def test_load_not_evaluated_gate_status_none(tmp_path):
    out = _published(tmp_path); rep = load_phase4_final_gate(out)
    rep["phase4_gate_status"] = "NOT_EVALUATED"
    _resign(out, rep)
    assert load_phase4_final_gate(out) is None


# =========================================================================== #
# 19. HARDENING R1 — crash cleanup after marker (F5)                            #
# =========================================================================== #
@pytest.mark.parametrize("fault", ["dir_after_json", "dir_after_md", "marker_fsync", "dir_final"])
def test_crash_after_various_stages_no_valid_marker(tmp_path, fault):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out")
    with pytest.raises(G._InjectedFault):
        _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out, _fault=fault))
    assert load_phase4_final_gate(out) is None
    assert not os.path.exists(os.path.join(out, "phase4_final_gate.complete"))


# =========================================================================== #
# 20. HARDENING R1 — overwrite / recovery (F6)                                  #
# =========================================================================== #
def test_idempotent_identical_set(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out")
    a = _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out))
    b = _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out))   # idempotent no-op
    assert a["final_evidence_hash"] == b["final_evidence_hash"]
    assert load_phase4_final_gate(out) == a


def test_refuse_different_session(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out")
    a = _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out))
    # craft a genuinely different-session complete report and try to publish it into the same dir
    b = copy.deepcopy(a)
    b["session_id"] = "ps-other-session"
    b["forward_identity"] = dict(b["forward_identity"], manifest_hash="d" * 64)
    body = {k: v for k, v in b.items() if k != "final_evidence_hash"}
    b["final_evidence_hash"] = G._sha(body)
    with pytest.raises(Phase4GateError):
        G._write(b, out)


def test_refuse_same_session_different_content(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out")
    a = _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out))
    c = copy.deepcopy(a)                       # same session, mutated content
    c["reason_code"] = "MUTATED"
    body = {k: v for k, v in c.items() if k != "final_evidence_hash"}
    c["final_evidence_hash"] = G._sha(body)
    with pytest.raises(Phase4GateError):
        G._write(c, out)


def test_refuse_incomplete_set_without_recover(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out"); os.makedirs(out)
    open(os.path.join(out, "phase4_final_gate.json"), "w").write("{}")   # stray incomplete artifact
    with pytest.raises(Phase4GateError):
        _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out))


def test_recover_true_republishes_and_preserves_unrelated(tmp_path):
    kwargs, *_ = _synth_case(str(tmp_path), [100, 110] * 6, [100, 109] * 6)
    out = os.path.join(str(tmp_path), "out"); os.makedirs(out)
    open(os.path.join(out, "phase4_final_gate.json"), "w").write("{}")       # stray WP2B-4 artifact
    open(os.path.join(out, "unrelated.txt"), "w").write("keep me")            # unrelated file
    rep = _build_phase4_final_gate_from_artifacts(**dict(kwargs, output_dir=out, recover=True))
    assert load_phase4_final_gate(out) == rep
    assert open(os.path.join(out, "unrelated.txt")).read() == "keep me"       # untouched
