"""H-029 contract tests only — no research evaluation or performance assertion."""
from __future__ import annotations

import os

import numpy as np

from app.paper.schema import resolve_target
from app.research_safety.input_audit import run_audit
from app.strategy_research.providers import DataRequest, get_provider
from app.strategy_research.specs import get_spec
from app.strategy_research.strategies import get_strategy


def _btc():
    return get_provider("csv", root="data_csv").get(
        DataRequest(asset="BTC/USD", timeframe="1d")
    ).df


def test_h029_frozen_contract_and_grid():
    strategy = get_strategy("H-029")
    assert strategy.declared_inputs == {"close"}
    assert strategy.direction == "long_short"
    assert strategy.default_params == {
        "trend_lookback": 252,
        "volatility_lookback": 60,
        "volatility_baseline": 252,
        "volatility_multiplier": 1.5,
    }
    grid = strategy.param_grid()
    assert len(grid) == 4
    assert {p["trend_lookback"] for p in grid} == {126, 252}
    assert {p["volatility_lookback"] for p in grid} == {30, 60}
    assert {p["volatility_baseline"] for p in grid} == {252}
    assert {p["volatility_multiplier"] for p in grid} == {1.5}


def test_h029_is_discrete_causal_and_paper_compatible():
    strategy = get_strategy("H-029")
    df = _btc()
    full = strategy.positions(df)
    assert set(np.unique(full)) <= {-1.0, 0.0, 1.0}
    for k in (600, len(df) // 2, len(df) - 3):
        truncated = strategy.positions(df.iloc[:k].reset_index(drop=True))
        assert np.array_equal(full.iloc[:k].to_numpy(), truncated.to_numpy())
    paper = np.array([resolve_target(strategy.direction, float(x)) for x in full])
    assert np.array_equal(paper, full.to_numpy())


def test_h029_input_audit_and_spec_are_complete():
    strategy = get_strategy("H-029")
    audit = run_audit(strategy, _btc())
    assert audit["promotable"] is True
    assert audit["read"] == ["close"]
    spec = get_spec("H-029")
    assert "relative volatility gate" in spec["filters"]
    assert "completed close" in spec["lookahead"]
    assert os.path.exists("docs/H-029_PREREGISTRATION.md")
