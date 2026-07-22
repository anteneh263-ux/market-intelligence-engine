"""Tests for the research-safety gates & trial auditing. Offline (CSV provider on the
bundled data_csv) and deterministic. Covers target-control, trial ledger, input audit,
and the promotion gate."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.research_safety import (TrialLedger, run_audit, compare, evaluate_promotion,
                                 decide, TargetOnlyControl)
from app.research_safety.promotion_gate import GateStatus, Decision
from app.research_safety.trial_ledger import STATUS_TECHNICAL_FAILURE
from app.strategy_research.strategies import register_strategy, get_strategy
from app.strategy_research.strategies.base import Strategy
from app.strategy_research.providers import DataRequest, get_provider

PK = {"root": "data_csv"}
ASSETS = ["BTC/USD", "ETH/USD", "SPY", "XAU/USD"]


def _btc():
    return get_provider("csv", root="data_csv").get(DataRequest(asset="BTC/USD", timeframe="1d")).df


# --------------------------------------------------------------------------- #
# 1. Target-only control
# --------------------------------------------------------------------------- #
def test_candidate_vs_target_only_control():
    rec = compare("H-025", "CTRL-TARGET-ONLY", ASSETS, provider_kwargs=PK)
    assert rec["verdict"] in {"candidate_wins", "control_wins", "inconclusive"}
    assert rec["candidate_id"] == "H-025" and rec["control_id"] == "CTRL-TARGET-ONLY"
    assert "sharpe" in rec["delta"] and "max_drawdown" in rec["delta"] and "total_return" in rec["delta"]
    assert "causal" not in rec["note"].lower() or "not a causal claim" in rec["note"].lower()


def test_equal_result_is_inconclusive():
    rec = compare("H-020", "H-020", ASSETS, provider_kwargs=PK)   # same strategy both sides
    assert rec["delta"]["sharpe"] == 0.0
    assert rec["verdict"] == "inconclusive"


# --------------------------------------------------------------------------- #
# 2. Full DSR trial-ledger
# --------------------------------------------------------------------------- #
def test_all_valid_param_trials_count_in_dsr(tmp_path):
    tl = TrialLedger(root=str(tmp_path), clock=lambda: "2026-01-01T00:00:00Z")
    tl.record("H-100", "candidate")
    tl.record("CTRL", "control")
    tl.record("H-100", "param_combo", params={"n": 20})
    tl.record("H-100", "param_combo", params={"n": 30})
    tl.record("G-0001", "discovery_variant")
    assert tl.count_dsr_trials() == 5
    s = tl.summary()
    assert s["total_trials"] == 5 and s["dsr_trials"] == 5 and s["excluded_trials"] == 0
    assert s["by_type"]["param_combo"] == 2


def test_technical_failures_are_excluded(tmp_path):
    tl = TrialLedger(root=str(tmp_path), clock=lambda: "2026-01-01T00:00:00Z")
    tl.record("H-100", "candidate")                                    # valid -> counts
    rec = tl.record("H-101", "candidate", status=STATUS_TECHNICAL_FAILURE)  # never produced a result
    assert rec["in_dsr"] is False and rec["excluded_reason"]
    assert tl.count_dsr_trials() == 1                                   # the failure does NOT count
    assert tl.summary()["excluded_trials"] == 1


# --------------------------------------------------------------------------- #
# 3. Declared-versus-actual input audit
# --------------------------------------------------------------------------- #
def test_declared_and_actual_inputs_match():
    a = run_audit(TargetOnlyControl(), _btc())          # declares {'close'}, reads {'close'}
    assert a["read"] == ["close"] and a["declared"] == ["close"]
    assert a["deviations"]["used_not_declared"] == [] and a["promotable"] is True


def test_undeclared_input_blocks_promotion():
    a = run_audit(TargetOnlyControl(), _btc(), declared={"open"})   # reads close, only declared open
    assert "close" in a["deviations"]["used_not_declared"]
    assert a["promotable"] is False


def test_unused_central_input_is_visible():
    a = run_audit(TargetOnlyControl(), _btc(), declared={"close", "volume"}, central={"volume"})
    assert "volume" in a["deviations"]["central_not_read"]      # declared-central but never read
    assert a["promotable"] is False


# --------------------------------------------------------------------------- #
# 4. Promotion gate
# --------------------------------------------------------------------------- #
def test_promotion_approved_when_all_mandatory_pass():
    gates = [{"gate": g, "status": GateStatus.PASS.value, "mandatory": True,
              "measured": None, "threshold": None, "explanation": ""} for g in
             ("valid_backtest", "max_drawdown", "min_trades", "dsr_overfitting",
              "input_audit", "target_control", "paper_backtest_signal_match", "signal_timing_close_next_bar")]
    assert decide(gates) == Decision.PROMOTE


def test_not_available_is_not_pass():
    gates = [{"gate": "a", "status": GateStatus.PASS.value, "mandatory": True},
             {"gate": "b", "status": GateStatus.NOT_AVAILABLE.value, "mandatory": True}]
    assert decide(gates) == Decision.HOLD              # NA blocks PROMOTE, never counts as PASS
    gates.append({"gate": "c", "status": GateStatus.FAIL.value, "mandatory": True})
    assert decide(gates) == Decision.REJECT            # a FAIL dominates


def test_promotion_rejected_on_high_drawdown():
    rep = evaluate_promotion("H-028", ["BTC/USD"], provider_kwargs=PK, write=False)  # Hull MA, huge DD
    dd = [g for g in rep["gates"] if g["gate"] == "max_drawdown"][0]
    assert dd["status"] == "FAIL"
    assert rep["decision"] == "REJECT"


def test_promotion_blocked_when_paper_and_backtest_signals_mismatch():
    class _LookAhead(Strategy):
        strategy_id = "LOOKAHEAD-TEST"; name = "look-ahead"; strategy_class = "test"
        direction = "long_short"; declared_inputs = {"close"}
        def positions(self, df, params=None):
            c = df["close"]
            fut = c.shift(-1)                          # reads the FUTURE bar -> non-causal
            return pd.Series(np.where(fut > c, 1.0, -1.0), index=df.index)
        def param_grid(self):
            return [dict()]
    register_strategy(_LookAhead())
    rep = evaluate_promotion("LOOKAHEAD-TEST", ["BTC/USD"], provider_kwargs=PK, write=False)
    pm = [g for g in rep["gates"] if g["gate"] == "paper_backtest_signal_match"][0]
    assert pm["status"] == "FAIL"                      # tail signals don't match (look-ahead)
    assert rep["decision"] == "REJECT"
