"""Integration tests for the layered platform:
Research Core (orchestrator + versioning + modules) → Knowledge Base →
Meta-learning → AI Research Assistant. Uses a TEMP knowledge base and a CSV
fixture; never touches the real corpus."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.assistant import ResearchAssistant
from app.knowledge_base import KnowledgeBase, query
from app.meta_learning import mine
from app.research_core import Orchestrator, module_registry, versioning


def _fixture_csv(tmp_path, sym="AAPL", n=500):
    price = 100 + np.cumsum(np.sin(np.arange(n) / 6.0)) + np.linspace(0, 15, n)
    pd.DataFrame({
        "timestamp": [1_600_000_000 + i * 86400 for i in range(n)],
        "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1,
    }).to_csv(tmp_path / f"{sym}_1d.csv", index=False)


def test_versioning_v1_preserved_v4_active():
    v1 = versioning.resolve("v1"); v2 = versioning.resolve("v2")
    assert v1.hash() == "a740f3acbf6572d6"          # v1 immutable
    assert v1.pooling == "concatenation" and v2.pooling == "per_asset"
    assert versioning.ACTIVE_VERSION == versioning.resolve("v4").version


def test_module_registry_has_strategy_evaluation():
    mods = {m["name"] for m in module_registry.list_modules()}
    assert "strategy_evaluation" in mods
    # Count only permanent H-strategies; generated G-#### entries (registered by
    # discovery/workflow tests in the same process) can vary and must not affect this.
    catalogue = module_registry.get_module("strategy_evaluation").catalogue()
    h_strategies = [s for s in catalogue if s.startswith("H-")]
    assert len(h_strategies) == 29  # H-002..H-030 (H-030 pre-registered 2026-10-23)


def test_orchestrator_flow_records_and_learns(tmp_path):
    csv_root = tmp_path / "data"; csv_root.mkdir(); kb_root = tmp_path / "kb"; kb_root.mkdir()
    _fixture_csv(csv_root, "AAPL"); _fixture_csv(csv_root, "SPY")
    kb = KnowledgeBase(root=str(kb_root))
    orch = Orchestrator(kb)
    out = orch.run("H-013", ["AAPL", "SPY"], matrix_version="v2",
                   provider_kwargs={"root": str(csv_root)})
    assert out.matrix_version == "srp-matrix-v2"
    assert out.verdict in {"Supported", "Not Supported", "Inconclusive", "Graveyard"}
    assert out.recorded["results_written"]
    # KB now has a real result for H-013
    r = kb.result("H-013")
    assert r["is_real"] is True and r["matrix_version"] == "srp-matrix-v2"
    # meta-learning sees exactly the one real result
    report = mine(kb)
    assert report["n_tested"] == 1


def test_synthetic_never_counts_as_evidence(tmp_path):
    kb_root = tmp_path / "kb"; kb_root.mkdir()
    kb = KnowledgeBase(root=str(kb_root))
    orch = Orchestrator(kb)
    orch.run("H-022", ["BTC/USD", "ETH/USD"], matrix_version="v2", provider="synthetic")
    # a synthetic result file exists, but it must NOT count as tested evidence
    assert kb.result("H-022").get("is_real") is False
    assert query.tested_results(kb) == []
    assert mine(kb)["n_tested"] == 0


def test_assistant_is_grounded(tmp_path):
    csv_root = tmp_path / "data"; csv_root.mkdir(); kb_root = tmp_path / "kb"; kb_root.mkdir()
    _fixture_csv(csv_root, "AAPL")
    kb = KnowledgeBase(root=str(kb_root))
    Orchestrator(kb).run("H-018", ["AAPL"], matrix_version="v2",
                         provider_kwargs={"root": str(csv_root)})
    a = ResearchAssistant(kb).answer("give me a summary of the corpus")
    assert "1 strategies tested" in a.text
    assert a.citations
    ans2 = ResearchAssistant(kb).answer("tell me about H-018")
    assert "H-018" in ans2.text and ans2.evidence
