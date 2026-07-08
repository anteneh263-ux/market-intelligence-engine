"""End-to-end research-workflow test (Pine import → classify → compare → variants →
evaluate → report) against a TEMP knowledge base and CSV fixtures."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.adapters import import_pine
from app.knowledge_base import KnowledgeBase
from app.research_core.workflow import research_strategy

PINE = """//@version=5
strategy("BB + RSI MR")
basis = ta.sma(close, 20)
dev = 2.0 * ta.stdev(close, 20)
upper = basis + dev
lower = basis - dev
r = ta.rsi(close, 14)
longCond = close < lower and r < 30
shortCond = close > upper and r > 70
if (longCond)
    strategy.entry("L", strategy.long)
if (shortCond)
    strategy.entry("S", strategy.short)
"""


def _csv(tmp, sym, n=520):
    price = 100 + np.cumsum(np.sin(np.arange(n) / 7.0)) + np.linspace(0, 18, n)
    pd.DataFrame({"timestamp": [1_600_000_000 + i * 86400 for i in range(n)],
                  "open": price, "high": price + 1.5, "low": price - 1.5,
                  "close": price, "volume": 1}).to_csv(tmp / f"{sym}_1d.csv", index=False)


def test_pine_import_maps_and_confirmation_to_filter():
    imp = import_pine(PINE)
    assert imp.ok and imp.family == "mean-reversion"
    assert imp.recipe.rule == "FILTER"                 # 'and' → confirmation
    assert set(imp.recipe.blocks) | {imp.recipe.gate} >= {"rsi_rev", "bb_rev"}
    assert imp.warnings                                 # importer always flags heuristic mapping


def test_pine_import_unrecognized_returns_todo():
    imp = import_pine("//@version=5\nstrategy('x')\nfoo = bar(baz)\n")
    assert not imp.ok and any("TODO" in w for w in imp.warnings)


def test_full_workflow_end_to_end(tmp_path):
    data = tmp_path / "d"; data.mkdir(); kbroot = tmp_path / "kb"; kbroot.mkdir()
    for s in ("AAPL", "SPY"):
        _csv(data, s)
    kb = KnowledgeBase(root=str(kbroot))
    r = research_strategy(PINE, name="BB+RSI", assets=["AAPL", "SPY"], matrix="v2",
                          provider_kwargs={"root": str(data)}, k_variants=2, kb=kb)
    assert r["ok"] and r["uid"].startswith("U-")
    assert r["class"] == "mean-reversion"
    assert r["verdict"] in {"Supported", "Not Supported", "Inconclusive", "Graveyard"}
    assert len(r["variants"]) == 2
    for v in r["variants"]:
        assert v["verdict"] in {"Supported", "Not Supported", "Inconclusive", "Graveyard"}
    # a comparison report was written into the KB workflows area
    import os
    assert os.path.exists(r["report_path"])
    md = open(r["report_path"]).read()
    assert "What we learned" in md and "Head-to-head" in md
