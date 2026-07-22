"""Tests for the deterministic research-safety batch runner (app.research_safety.batch).

Offline (CSV provider on the bundled data_csv, plus the synthetic provider for the
technical-failure path) and deterministic. Covers: dynamic discovery, equal evaluation
settings, successful trial registration, technical-failure exclusion, idempotent
re-runs, deterministic ordering, and report output.

Research only — the batch never starts a forward test, touches a broker, or changes any
existing evaluation, threshold, strategy behaviour or historical result.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from app.research_safety.batch import (_discover, content_trial_id, format_batch_human,
                                        run_safety_batch)
from app.research_safety.trial_ledger import TrialLedger
from app.strategy_research.strategies import (available_strategies, get_strategy,
                                              register_strategy)
from app.strategy_research.strategies.base import Strategy

PK = {"root": "data_csv"}
CLK = lambda: "2026-07-22T00:00:00Z"     # frozen clock -> deterministic timestamps


class _BatchDummy(Strategy):
    """A tiny valid, causal, long-only strategy used to prove discovery is dynamic."""
    strategy_id = "BX-001"
    name = "batch discovery dummy"
    strategy_class = "test"
    direction = "long_only"
    default_params = {"n": 10}

    def positions(self, df, params=None):
        n = int((params or self.default_params)["n"])
        ma = df["close"].rolling(n, min_periods=1).mean()
        return (df["close"] > ma).astype(float)


# --------------------------------------------------------------------------- #
# 1. Discovery is dynamic (no hard-coded id list) and excludes controls
# --------------------------------------------------------------------------- #
def test_discovery_is_dynamic_and_excludes_controls():
    base = _discover("CTRL-TARGET-ONLY", None)
    # matches the LIVE registry (minus controls), sorted — nothing hard-coded
    live = sorted(s for s in available_strategies()
                  if getattr(get_strategy(s), "strategy_class", "") != "control")
    assert base == live
    assert base == sorted(base) and len(base) >= 20
    assert "CTRL-TARGET-ONLY" not in base

    # registering a new strategy makes it appear -> discovery really is dynamic
    register_strategy(_BatchDummy())
    after = _discover("CTRL-TARGET-ONLY", None)
    assert "BX-001" in after
    assert set(after) - set(base) == {"BX-001"}


# --------------------------------------------------------------------------- #
# 2. Every strategy is evaluated with identical settings
# --------------------------------------------------------------------------- #
def test_equal_evaluation_settings(tmp_path):
    rep = run_safety_batch(["BTC/USD", "ETH/USD"], provider="csv", provider_kwargs=PK,
                           root=str(tmp_path), strategies=["H-002", "H-005"],
                           clock=CLK, write=False)
    assert rep["assets"] == ["BTC/USD", "ETH/USD"]
    assert rep["cost_bps"] == 5.0 and rep["slippage_bps"] == 5.0
    assert rep["matrix"] == "srp-matrix-v2" and rep["timeframe"] == "1d"
    # each trial id is keyed by the SAME (assets, timeframe, matrix, cost, slippage)
    for r in rep["strategies"]:
        assert r["trial_id"] == content_trial_id(
            r["strategy_id"], rep["assets"], rep["timeframe"], rep["matrix"],
            rep["cost_bps"], rep["slippage_bps"], data_sha256=rep["data_sha256"])


# --------------------------------------------------------------------------- #
# 3. A successful evaluation registers exactly one candidate trial (in_dsr=True)
# --------------------------------------------------------------------------- #
def test_successful_registration(tmp_path):
    rep = run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                           root=str(tmp_path), strategies=["H-005"], clock=CLK, write=False)
    row = rep["strategies"][0]
    assert row["status"] != "technical_failure" and row["in_dsr"] is True
    ents = TrialLedger(root=str(tmp_path)).entries()
    cand = [e for e in ents if e["strategy"] == "H-005" and e["trial_type"] == "candidate"]
    assert len(cand) == 1                       # exactly one candidate trial
    assert cand[0]["in_dsr"] is True
    # the row carries the full required schema
    for k in ("strategy_id", "status", "oos_sharpe", "total_return", "max_drawdown",
              "num_trades", "dsr", "control_verdict", "promotion_decision",
              "failure_reasons"):
        assert k in row


# --------------------------------------------------------------------------- #
# 4. Technical failures are recorded but excluded (in_dsr=False)
# --------------------------------------------------------------------------- #
def test_technical_failures_excluded(tmp_path):
    # the synthetic provider is not real market data -> every strategy is a
    # technical failure that never produced a valid result
    rep = run_safety_batch(["BTC/USD"], provider="synthetic", root=str(tmp_path),
                           strategies=["H-002", "H-005"], clock=CLK, write=False)
    for r in rep["strategies"]:
        assert r["status"] == "technical_failure" and r["in_dsr"] is False
        assert r["failure_reasons"]
    tl = TrialLedger(root=str(tmp_path))
    ents = tl.entries()
    assert len(ents) == 2 and all(e["in_dsr"] is False for e in ents)
    s = tl.summary()
    assert s["dsr_trials"] == 0 and s["excluded_trials"] == 2
    assert rep["summary"]["n_technical_failures"] == 2 and rep["summary"]["n_valid"] == 0


# --------------------------------------------------------------------------- #
# 5. Re-running the identical batch does not duplicate trials (idempotent)
# --------------------------------------------------------------------------- #
def test_idempotent_rerun(tmp_path):
    args = dict(provider="csv", provider_kwargs=PK, root=str(tmp_path),
                strategies=["H-002", "H-005"], clock=CLK, write=False)
    a = run_safety_batch(["BTC/USD"], **args)
    n1 = len(TrialLedger(root=str(tmp_path)).entries())
    b = run_safety_batch(["BTC/USD"], **args)                 # identical re-run
    n2 = len(TrialLedger(root=str(tmp_path)).entries())
    assert n1 == n2 and n1 > 2                                 # controls/param combos included once
    assert [r["trial_id"] for r in a["strategies"]] == [r["trial_id"] for r in b["strategies"]]


# --------------------------------------------------------------------------- #
# 6. Deterministic ordering (input order is irrelevant; output sorted + identical)
# --------------------------------------------------------------------------- #
def test_deterministic_ordering(tmp_path):
    r1 = run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                          root=str(tmp_path / "a"), strategies=["H-005", "H-002"],
                          clock=CLK, write=False)
    r2 = run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                          root=str(tmp_path / "b"), strategies=["H-002", "H-005"],
                          clock=CLK, write=False)
    ids1 = [r["strategy_id"] for r in r1["strategies"]]
    assert ids1 == ["H-002", "H-005"]                          # sorted regardless of input
    assert ids1 == [r["strategy_id"] for r in r2["strategies"]]
    # identical inputs + frozen clock -> byte-identical strategy JSON
    assert json.dumps(r1["strategies"], sort_keys=True) == \
        json.dumps(r2["strategies"], sort_keys=True)


# --------------------------------------------------------------------------- #
# 7. Report output: atomic timestamped file + latest.json, deterministic content
# --------------------------------------------------------------------------- #
def test_report_output(tmp_path):
    rep = run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK,
                           root=str(tmp_path), strategies=["H-002"], clock=CLK, write=True)
    base = os.path.join(str(tmp_path), "research_safety", "batches")
    stamped = os.path.join(base, "batch-20260722T000000Z.json")
    latest = os.path.join(base, "latest.json")
    assert os.path.exists(stamped) and os.path.exists(latest)
    # both files are identical, deterministic JSON
    with open(stamped) as f:
        a = f.read()
    with open(latest) as f:
        b = f.read()
    assert a == b
    doc = json.loads(b)
    assert doc["kind"] == "research_safety_batch" and doc["control_id"] == "CTRL-TARGET-ONLY"
    row = doc["strategies"][0]
    for k in ("strategy_id", "status", "oos_sharpe", "total_return", "max_drawdown",
              "num_trades", "dsr", "control_verdict", "promotion_decision",
              "failure_reasons"):
        assert k in row
    # human summary renders the key facts
    txt = format_batch_human(rep)
    assert "research-safety batch" in txt and "H-002" in txt


# --------------------------------------------------------------------------- #
# 8. The batch never starts a forward test (no forward artifacts are written)
# --------------------------------------------------------------------------- #
def test_batch_starts_no_forward_test(tmp_path):
    run_safety_batch(["BTC/USD"], provider="csv", provider_kwargs=PK, root=str(tmp_path),
                     strategies=["H-002", "H-005"], clock=CLK, write=True)
    assert not os.path.exists(os.path.join(str(tmp_path), "forward_tests"))
