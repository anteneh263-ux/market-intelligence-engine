"""H-030 contract tests only — no research evaluation or performance assertion."""
from __future__ import annotations

import os

import numpy as np

from app.paper.engine import strategy_ids as paper_strategy_ids
from app.paper.schema import resolve_target
from app.research_safety.input_audit import run_audit
from app.strategy_research.providers import DataRequest, get_provider
from app.strategy_research.specs import get_spec
from app.strategy_research.strategies import get_strategy


def _btc():
    # Repository fixture data, used only to exercise the rule mechanically.
    return get_provider("csv", root="data_csv").get(DataRequest(asset="BTC/USD", timeframe="1d")).df


def test_h030_frozen_contract_and_grid():
    s = get_strategy("H-030")
    assert s.declared_inputs == {"close"}
    assert s.direction == "long_short"
    assert s.default_params == {"lookback": 252}
    assert [p["lookback"] for p in s.param_grid()] == [21, 63, 126, 252]


def test_h030_rule_is_sign_of_trailing_return_and_causal():
    s = get_strategy("H-030")
    df = _btc()
    for n in (21, 252):
        pos = s.positions(df, {"lookback": n})
        mom = df["close"] / df["close"].shift(n) - 1.0
        expected = np.where(mom > 0, 1.0, np.where(mom < 0, -1.0, 0.0))
        assert np.array_equal(pos.to_numpy(), expected)
        assert (pos.iloc[:n] == 0.0).all()
    full = s.positions(df)
    for k in (600, len(df) // 2, len(df) - 3):
        assert np.array_equal(full.iloc[:k].to_numpy(),
                              s.positions(df.iloc[:k].reset_index(drop=True)).to_numpy())
    paper = np.array([resolve_target(s.direction, float(x)) for x in full])
    assert np.array_equal(paper, full.to_numpy())


def test_h030_input_audit_spec_and_preregistration_exist():
    s = get_strategy("H-030")
    audit = run_audit(s, _btc())
    assert audit["promotable"] is True and audit["read"] == ["close"]
    spec = get_spec("H-030")
    assert "separate from H-025" in spec["source"]
    assert "completed close" in spec["lookahead"]
    assert os.path.exists("docs/H-030_PREREGISTRATION.md")


def test_h030_is_evaluation_only_and_paper_universe_is_unchanged():
    ids = paper_strategy_ids()
    assert "H-030" not in ids
    assert len(ids) == 27      # unchanged: 29 H-strategies minus H-028 (Graveyard) and H-030
