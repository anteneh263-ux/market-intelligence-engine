"""Phase-1 targeted tests for shadow-forward: lifecycle, frozen manifest, tamper-evident
ledger and checkpoint receipts.

Deterministic and offline. Imports ONLY the Phase-1 modules (lifecycle, manifest, ledger)
— no Phase-2 behaviour. Research and simulation only.
"""
from __future__ import annotations

import copy
import json
import threading

import numpy as np
import pandas as pd

from app.forward.shadow import lifecycle as lc
from app.forward.shadow.lifecycle import LifecycleError, derive_current_status
from app.forward.shadow.manifest import (
    ManifestError,
    build_shadow_manifest,
    manifest_hash,
    risk_rule_hash,
)
from app.forward.shadow.ledger import (
    GENESIS,
    LedgerError,
    ShadowLedger,
    checkpoint_receipt,
    verify_receipt,
)
from app.strategy_research.strategies.base import Strategy


class _Strat(Strategy):
    strategy_id = "SF-TEST"
    name = "shadow phase-1 test strategy"
    strategy_class = "test"
    direction = "long_short"
    default_params = {"n": 5}

    def positions(self, df, params=None):
        n = int((params or self.default_params)["n"])
        ma = df["close"].rolling(n, min_periods=1).mean()
        return np.sign(df["close"] - ma).fillna(0.0)


def _mk(**over):
    base = dict(
        strategy=_Strat(), params={"n": 5}, assets=["BTC/USD", "ETH/USD"], timeframe="1d",
        data_cutoff="2026-01-01", start_time="2026-01-02T00:00:00Z", test_mode="shadow",
        initial_lifecycle_status="SHADOW", cost_bps=5.0, slippage_bps=5.0,
        expected_ranges={"sharpe": {"p50": 0.01}},
        warning_thresholds={"max_drawdown": 0.5},
        absolute_stop_thresholds={"max_drawdown": 0.6},
        checkpoints=[30, 60, 90], min_calendar_days=60, min_trades=30,
        required_regimes=["trend", "range"], repo_root=None,
    )
    base.update(over)
    return build_shadow_manifest(**base)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_illegal_transition_rejected():
    for frm, to in [("SHADOW", "LIVE_READY"), ("REJECT", "PROMOTE"), (None, "LIVE_READY")]:
        try:
            lc.assert_transition(frm, to)
            assert False, f"{frm}->{to} should be illegal"
        except LifecycleError:
            pass
    lc.assert_transition("SHADOW", "PROMOTE")        # legal
    lc.assert_transition("PROMOTE", "LIVE_READY")    # legal


def test_terminal_cannot_reopen():
    for term in ("INVALID", "INVALIDATED"):
        for to in ("SHADOW", "PROMOTE", "LIVE_READY"):
            try:
                lc.assert_transition(term, to)
                assert False
            except LifecycleError:
                pass


def test_shadow_or_promote_to_invalid_allowed_for_methodology():
    # a methodological flaw discovered after admission -> INVALID (terminal)
    lc.assert_transition("SHADOW", "INVALID")
    lc.assert_transition("PROMOTE", "INVALID")
    assert lc.is_terminal("INVALID") and lc.is_terminal("INVALIDATED")


def test_derive_current_status_empty_history_is_initial():
    # continuity-validated derivation is covered in test_status_continuity_valid_and_invalid;
    # here we only assert the no-event base case returns the initial status.
    assert derive_current_status("SHADOW", []) == "SHADOW"


# --------------------------------------------------------------------------- #
# Frozen manifest — id derived from the COMPLETE frozen definition
# --------------------------------------------------------------------------- #
def test_id_and_hash_consistent_and_deterministic():
    a, b = _mk(), _mk()
    assert a["forward_test_id"] == b["forward_test_id"]
    assert manifest_hash(a) == manifest_hash(b)
    assert a["forward_test_id"].endswith(manifest_hash(a)[:12])
    assert "lifecycle_status" not in a and a["initial_lifecycle_status"] == "SHADOW"


def test_different_thresholds_produce_different_ids():
    base = _mk()
    warn = _mk(warning_thresholds={"max_drawdown": 0.4})
    absol = _mk(absolute_stop_thresholds={"max_drawdown": 0.5})
    assert base["forward_test_id"] != warn["forward_test_id"]
    assert base["forward_test_id"] != absol["forward_test_id"]
    # absolute stop feeds the risk-rule hash too
    assert base["risk_rule_hash"] != absol["risk_rule_hash"]


def test_different_costs_produce_different_ids():
    assert _mk(cost_bps=5.0)["forward_test_id"] != _mk(cost_bps=7.0)["forward_test_id"]
    assert _mk(slippage_bps=5.0)["forward_test_id"] != _mk(slippage_bps=9.0)["forward_test_id"]


def test_different_start_times_produce_different_ids():
    assert (_mk(start_time="2026-01-02T00:00:00Z")["forward_test_id"]
            != _mk(start_time="2026-02-02T00:00:00Z")["forward_test_id"])


def test_different_expected_ranges_produce_different_ids():
    assert (_mk(expected_ranges={"sharpe": {"p50": 0.01}})["forward_test_id"]
            != _mk(expected_ranges={"sharpe": {"p50": 0.02}})["forward_test_id"])


def test_different_checkpoints_and_regimes_change_id():
    assert _mk(checkpoints=[30, 60, 90])["forward_test_id"] != _mk(checkpoints=[20, 40])["forward_test_id"]
    assert _mk(required_regimes=["trend", "range"])["forward_test_id"] != \
        _mk(required_regimes=["trend"])["forward_test_id"]


def test_reordered_assets_same_manifest_and_id():
    from app.forward.manifest import canonical_json
    a = _mk(assets=["BTC/USD", "ETH/USD"])
    b = _mk(assets=["ETH/USD", "BTC/USD"])
    assert a["assets"] == ["BTC/USD", "ETH/USD"] == b["assets"]     # stored sorted
    assert a["forward_test_id"] == b["forward_test_id"]
    assert canonical_json(a) == canonical_json(b)


def test_strategy_files_included_in_identity():
    m = _mk()
    assert m["strategy_files"]
    assert all({"role", "module", "sha256"} <= set(f) for f in m["strategy_files"])
    assert all(f.get("sha256") for f in m["strategy_files"])


def test_manifest_validation_errors():
    bad = [
        dict(checkpoints=[60, 30]),          # unsorted
        dict(checkpoints=[30, 30]),          # duplicate
        dict(checkpoints=[0, 30]),           # non-positive
        dict(test_mode="bogus"),             # invalid mode
        dict(initial_lifecycle_status="BOGUS"),
        dict(test_mode="shadow", initial_lifecycle_status="PROMOTE"),   # incompatible
        dict(cost_bps=float("inf")),         # non-finite
        dict(absolute_stop_thresholds={"x": float("nan")}),
        dict(min_trades=-1),
    ]
    for over in bad:
        try:
            _mk(**over)
            assert False, f"expected ManifestError for {over}"
        except ManifestError:
            pass


def test_status_transition_does_not_mutate_frozen_manifest(tmp_path):
    m = _mk()
    frozen_copy = copy.deepcopy(m)
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    mh = manifest_hash(m)
    lg.append(timestamp="t1", actor="system", event_type="admit", old_status=None,
              new_status="SHADOW", reason="admitted", manifest_hash=mh)
    lg.append(timestamp="t2", actor="alice", event_type="transition", old_status="SHADOW",
              new_status="PROMOTE", reason="promoted", manifest_hash=mh)
    assert derive_current_status(m["initial_lifecycle_status"], lg.read()) == "PROMOTE"
    assert m == frozen_copy          # the frozen manifest was never rewritten


# --------------------------------------------------------------------------- #
# Tamper-evident ledger
# --------------------------------------------------------------------------- #
def _seed(tmp_path, n=3, mh="M1"):
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    for i in range(n):
        # ordinary observations carry NULL status (non-transition events)
        lg.append(timestamp=f"t{i}", actor="system", event_type="observe",
                  old_status=None, new_status=None, reason=f"obs{i}", manifest_hash=mh)
    return lg


def test_event_mutation_breaks_verification(tmp_path):
    lg = _seed(tmp_path)
    lines = open(lg.path).read().splitlines()
    d = json.loads(lines[0]); d["reason"] = "TAMPERED"; lines[0] = json.dumps(d)
    open(lg.path, "w").write("\n".join(lines) + "\n")
    v = ShadowLedger(lg.path).verify(manifest_hash="M1")
    assert not v["ok"] and any(e["kind"] == "event_hash_mismatch" for e in v["errors"])


def test_interior_event_deletion_detected(tmp_path):
    lg = _seed(tmp_path, n=4)
    lines = open(lg.path).read().splitlines()
    del lines[1]                                       # delete an interior event
    open(lg.path, "w").write("\n".join(lines) + "\n")
    v = ShadowLedger(lg.path).verify(manifest_hash="M1")
    assert not v["ok"] and any(e["kind"] == "broken_previous_hash" for e in v["errors"])


def test_event_reordering_detected(tmp_path):
    lg = _seed(tmp_path, n=3)
    lines = open(lg.path).read().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    open(lg.path, "w").write("\n".join(lines) + "\n")
    v = ShadowLedger(lg.path).verify(manifest_hash="M1")
    assert not v["ok"] and any(e["kind"] in ("broken_previous_hash", "non_sequential_event_id")
                               for e in v["errors"])


def test_manifest_mismatch_detected(tmp_path):
    lg = _seed(tmp_path, mh="M1")
    v = ShadowLedger(lg.path).verify(manifest_hash="M2")
    assert not v["ok"] and any(e["kind"] == "manifest_mismatch" for e in v["errors"])


def test_missing_manifest_hash_not_treated_as_match(tmp_path):
    # craft a raw ledger whose event lacks a manifest_hash; verify(manifest_hash=...) must
    # NOT accept it as matching
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    from app.forward.shadow.ledger import make_event
    from app.forward.manifest import canonical_json
    e = make_event(seq=1, timestamp="t", actor="system", event_type="observe",
                   old_status=None, new_status="SHADOW", reason="obs",
                   manifest_hash="", evidence_hash=None, previous_event_hash=GENESIS)
    open(lg.path, "w").write(canonical_json(e) + "\n")
    v = lg.verify(manifest_hash="M1")
    assert not v["ok"]
    kinds = {x["kind"] for x in v["errors"]}
    assert "empty_required_field" in kinds or "manifest_mismatch" in kinds


def test_corrupt_ledger_refuses_append(tmp_path):
    lg = _seed(tmp_path, n=2)
    lines = open(lg.path).read().splitlines()
    d = json.loads(lines[0]); d["reason"] = "TAMPERED"; lines[0] = json.dumps(d)
    open(lg.path, "w").write("\n".join(lines) + "\n")
    try:
        ShadowLedger(lg.path).append(timestamp="t9", actor="system", event_type="observe",
                                     old_status="SHADOW", new_status="SHADOW",
                                     reason="obs", manifest_hash="M1")
        assert False, "append to a corrupt ledger must be refused"
    except LedgerError:
        pass


def test_append_requires_nonempty_fields(tmp_path):
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    for bad in (dict(actor=""), dict(event_type=""), dict(reason=""), dict(manifest_hash="")):
        kw = dict(timestamp="t", actor="system", event_type="observe", old_status=None,
                  new_status="SHADOW", reason="obs", manifest_hash="M1")
        kw.update(bad)
        try:
            lg.append(**kw)
            assert False, f"expected LedgerError for {bad}"
        except LedgerError:
            pass


def test_concurrent_appends_do_not_fork_the_chain(tmp_path):
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    n = 12
    barrier = threading.Barrier(n)
    errors: list = []

    def worker(i):
        try:
            barrier.wait()
            lg.append(timestamp=f"t{i}", actor="system", event_type="observe",
                      old_status=None, new_status=None, reason=f"obs{i}",
                      manifest_hash="M1")
        except Exception as exc:                       # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    v = lg.verify(manifest_hash="M1")
    assert v["ok"] and v["n_events"] == n              # single linear chain, no fork
    ids = [e["event_id"] for e in lg.read()]
    assert ids == [f"E-{i:05d}" for i in range(1, n + 1)]


# --------------------------------------------------------------------------- #
# Checkpoint receipts + final-event deletion
# --------------------------------------------------------------------------- #
def test_checkpoint_receipt_contains_chain_head_and_is_hashable(tmp_path):
    lg = _seed(tmp_path, n=3, mh="M1")
    r = checkpoint_receipt(forward_test_id="SF-x", checkpoint="30", timestamp="t",
                           chain_head_hash=lg.head_hash(), manifest_hash="M1",
                           n_events=3, n_observations=3)
    assert r["chain_head_hash"] == lg.head_hash()
    assert verify_receipt(r)
    r2 = dict(r); r2["n_events"] = 99                  # tamper the receipt
    assert not verify_receipt(r2)


def test_final_event_deletion_detected_with_receipt(tmp_path):
    lg = _seed(tmp_path, n=3, mh="M1")
    receipt = checkpoint_receipt(forward_test_id="SF-x", checkpoint="30", timestamp="t",
                                 chain_head_hash=lg.head_hash(), manifest_hash="M1",
                                 n_events=3, n_observations=3)
    # delete the FINAL event — the truncated chain is still internally consistent
    lines = open(lg.path).read().splitlines()
    del lines[-1]
    open(lg.path, "w").write("\n".join(lines) + "\n")
    plain = ShadowLedger(lg.path).verify(manifest_hash="M1")
    assert plain["ok"], "a local chain alone cannot detect final-event deletion"
    against = ShadowLedger(lg.path).verify_against_receipt(
        receipt, expected_forward_test_id="SF-x", expected_manifest_hash="M1")
    assert not against["ok"]
    kinds = {e["kind"] for e in against["errors"]}
    assert "event_count_mismatch" in kinds or "chain_head_mismatch" in kinds


# --------------------------------------------------------------------------- #
# Fix 1 — a ledger is bound to exactly one manifest
# --------------------------------------------------------------------------- #
def test_ledger_bound_to_one_manifest(tmp_path):
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    lg.append(timestamp="t1", actor="system", event_type="observe", old_status=None,
              new_status=None, reason="obs", manifest_hash="M1")
    before = open(lg.path).read()
    try:
        lg.append(timestamp="t2", actor="system", event_type="observe", old_status=None,
                  new_status=None, reason="obs", manifest_hash="M2")   # different manifest
        assert False, "an M1 ledger must refuse an M2 append"
    except LedgerError:
        pass
    assert open(lg.path).read() == before                     # original ledger unchanged
    assert lg.verify(manifest_hash="M1")["ok"]                # and still valid


# --------------------------------------------------------------------------- #
# Fix 2 — lifecycle continuity when deriving current status
# --------------------------------------------------------------------------- #
def _hist(*pairs):
    return [{"old_status": o, "new_status": n} for o, n in pairs]


def test_status_continuity_valid_and_invalid():
    # SHADOW -> PROMOTE works (genesis then transition)
    assert derive_current_status("SHADOW", _hist((None, "SHADOW"), ("SHADOW", "PROMOTE"))) == "PROMOTE"
    # ordinary observations (null/null) do not affect status
    assert derive_current_status("SHADOW", _hist((None, None), (None, None))) == "SHADOW"
    # SHADOW -> LIVE_READY fails (illegal transition)
    for bad in [
        _hist(("SHADOW", "LIVE_READY")),                      # illegal transition
        _hist(("PROMOTE", "LIVE_READY")),                     # old_status != current (SHADOW)
        _hist(("SHADOW", "BOGUS")),                           # unknown new_status
        [{"old_status": "SHADOW", "new_status": None}],       # non-transition with old set
    ]:
        try:
            derive_current_status("SHADOW", bad)
            assert False, f"expected LifecycleError for {bad}"
        except LifecycleError:
            pass


def test_terminal_cannot_reopen_via_history():
    hist = _hist((None, "SHADOW"), ("SHADOW", "INVALID"), ("INVALID", "SHADOW"))
    try:
        derive_current_status("SHADOW", hist)
        assert False, "INVALID must not reopen"
    except LifecycleError:
        pass


def test_verified_current_status_verifies_before_deriving(tmp_path):
    from app.forward.shadow.ledger import verified_current_status
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    mh = "M1"
    lg.append(timestamp="t1", actor="system", event_type="admit", old_status=None,
              new_status="SHADOW", reason="admitted", manifest_hash=mh)
    lg.append(timestamp="t2", actor="alice", event_type="transition", old_status="SHADOW",
              new_status="PROMOTE", reason="promoted", manifest_hash=mh)
    assert verified_current_status(lg, manifest_hash=mh, initial_status="SHADOW") == "PROMOTE"
    # tamper -> derivation is refused because hash verification runs first
    lines = open(lg.path).read().splitlines()
    d = json.loads(lines[0]); d["reason"] = "X"; lines[0] = json.dumps(d)
    open(lg.path, "w").write("\n".join(lines) + "\n")
    try:
        verified_current_status(ShadowLedger(lg.path), manifest_hash=mh, initial_status="SHADOW")
        assert False
    except LedgerError:
        pass


# --------------------------------------------------------------------------- #
# Fix 3 — complete file-drift detection + CWD-independent identity
# --------------------------------------------------------------------------- #
def test_frozen_file_hash_drift_detected():
    from app.forward.shadow.manifest import frozen_field_drift
    m = _mk()
    assert frozen_field_drift(m, _Strat())["ok"]              # no drift
    tampered = copy.deepcopy(m)
    tampered["strategy_files"][0]["sha256"] = "deadbeef"      # simulate a changed file hash
    drift = frozen_field_drift(tampered, _Strat())
    assert not drift["ok"]
    assert any(c.get("issue") == "hash_changed" for c in drift["changes"])


def test_identity_is_working_directory_independent(tmp_path):
    import os
    cwd = os.getcwd()
    try:
        id_a = _mk()["forward_test_id"]
        os.chdir(str(tmp_path))                              # different working directory
        id_b = _mk()["forward_test_id"]
    finally:
        os.chdir(cwd)
    assert id_a == id_b
    # identity uses stable roles + module + sha256, never a CWD-relative path
    for f in _mk()["strategy_files"]:
        assert set(f) == {"role", "module", "sha256"}


# --------------------------------------------------------------------------- #
# Fix 4 — manifest-integrity validation
# --------------------------------------------------------------------------- #
def test_verify_manifest_integrity_detects_id_tamper():
    from app.forward.shadow.manifest import verify_manifest_integrity
    m = _mk()
    assert verify_manifest_integrity(m)["ok"]
    tampered = copy.deepcopy(m)
    tampered["forward_test_id"] = "SF-SF-TEST-000000000000"   # change ONLY the id
    res = verify_manifest_integrity(tampered)
    assert not res["ok"]
    assert any(e["kind"] == "forward_test_id_mismatch" for e in res["errors"])
    # wrong version + missing field are also caught
    bad_ver = copy.deepcopy(m); bad_ver["manifest_version"] = "x"
    assert not verify_manifest_integrity(bad_ver)["ok"]


def test_tightened_basic_validation():
    for over in [
        dict(assets=["BTC/USD", "BTC/USD"]),                 # duplicate
        dict(assets=["BTC/USD", "  "]),                      # blank
        dict(cost_bps=-1.0),                                 # negative
        dict(slippage_bps=-0.1),
        dict(required_regimes=["trend", "trend"]),           # duplicate
        dict(required_regimes=[]),                           # empty
        dict(position_limits={"max_gross_exposure": float("inf")}),
    ]:
        try:
            _mk(**over)
            assert False, f"expected ManifestError for {over}"
        except ManifestError:
            pass


# --------------------------------------------------------------------------- #
# Fix 5 — receipts bound to the expected experiment
# --------------------------------------------------------------------------- #
def test_receipt_from_another_experiment_rejected(tmp_path):
    lg = _seed(tmp_path, n=3, mh="M1")
    good = checkpoint_receipt(forward_test_id="SF-A", checkpoint="30", timestamp="t",
                              chain_head_hash=lg.head_hash(), manifest_hash="M1",
                              n_events=3, n_observations=3)
    # a well-formed, correctly-hashed receipt for a DIFFERENT experiment must be rejected
    other = checkpoint_receipt(forward_test_id="SF-B", checkpoint="30", timestamp="t",
                               chain_head_hash=lg.head_hash(), manifest_hash="M1",
                               n_events=3, n_observations=3)
    ok = lg.verify_against_receipt(good, expected_forward_test_id="SF-A",
                                   expected_manifest_hash="M1")
    assert ok["ok"]
    bad = lg.verify_against_receipt(other, expected_forward_test_id="SF-A",
                                    expected_manifest_hash="M1")
    assert not bad["ok"]
    assert any(e["kind"] == "receipt_experiment_mismatch" for e in bad["errors"])
    # a receipt bound to a different manifest is likewise rejected
    wrong_m = lg.verify_against_receipt(good, expected_forward_test_id="SF-A",
                                        expected_manifest_hash="M2")
    assert not wrong_m["ok"]


# --------------------------------------------------------------------------- #
# Round-3 integrity fixes: genesis semantics, non-empty timestamp, bidirectional
# file-role drift.
# --------------------------------------------------------------------------- #
def test_genesis_semantics_single_and_first():
    # valid initial genesis followed by a legal transition
    assert derive_current_status("SHADOW", _hist((None, "SHADOW"), ("SHADOW", "PROMOTE"))) == "PROMOTE"
    for bad in [
        _hist((None, "SHADOW"), (None, "SHADOW")),                       # repeated genesis
        _hist((None, "SHADOW"), ("SHADOW", "PROMOTE"), (None, "PROMOTE")),  # genesis after transition
    ]:
        try:
            derive_current_status("SHADOW", bad)
            assert False, f"expected LifecycleError for {bad}"
        except LifecycleError:
            pass


def test_append_requires_nonempty_timestamp(tmp_path):
    lg = ShadowLedger(str(tmp_path / "events.jsonl"))
    try:
        lg.append(timestamp="", actor="system", event_type="observe", old_status=None,
                  new_status=None, reason="obs", manifest_hash="M1")
        assert False, "empty timestamp must be rejected"
    except LedgerError:
        pass


def test_file_drift_bidirectional_role_comparison():
    from app.forward.shadow.manifest import frozen_field_drift
    m = _mk()
    # missing current role: frozen carries a role the current mapping cannot produce
    a = copy.deepcopy(m)
    a["strategy_files"].append({"role": "phantom", "module": "x", "sha256": "abc"})
    da = frozen_field_drift(a, _Strat())
    assert not da["ok"] and any(c.get("issue") == "missing_current_role" for c in da["changes"])
    # unexpected current role: drop a frozen role so the current mapping has an extra one
    b = copy.deepcopy(m)
    b["strategy_files"] = [f for f in b["strategy_files"] if f["role"] != "strategy_class_source"]
    db = frozen_field_drift(b, _Strat())
    assert not db["ok"] and any(c.get("issue") == "unexpected_current_role" for c in db["changes"])
    # duplicate role in the frozen manifest
    c = copy.deepcopy(m)
    c["strategy_files"].append(dict(c["strategy_files"][0]))
    dc = frozen_field_drift(c, _Strat())
    assert not dc["ok"] and any(x.get("issue") == "duplicate_frozen_role" for x in dc["changes"])
