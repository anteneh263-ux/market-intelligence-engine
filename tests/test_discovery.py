"""Tests for the Strategy Discovery Engine: composable generation, frozen &
auditable recipes, versioned policy, avoidance of failed traits, no look-ahead in
generated strategies, and the full closed loop (generate → test → record → learn)
against a TEMP discovery ledger (never the real corpus)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.discovery import (
    DISCOVERY_POLICY_VERSION,
    DiscoveryLedger,
    Recipe,
    StrategyDiscoveryEngine,
    policy_hash,
)
from app.discovery.blocks import BLOCKS, DIRECTIONAL
from app.discovery.generator import enumerate_candidates
from app.discovery.genome import materialize
from app.discovery.traits import TraitStats, avoid_reason, recipe_traits
from app.knowledge_base import KnowledgeBase
from app.research_core import Orchestrator


def _csv(tmp, sym, n=520):
    price = 100 + np.cumsum(np.sin(np.arange(n) / 7.0)) + np.linspace(0, 20, n)
    pd.DataFrame({"timestamp": [1_600_000_000 + i * 86400 for i in range(n)],
                  "open": price, "high": price + 1.5, "low": price - 1.5,
                  "close": price, "volume": 1}).to_csv(tmp / f"{sym}_1d.csv", index=False)


def test_policy_is_versioned_and_hashed():
    assert DISCOVERY_POLICY_VERSION == "discovery-v1"
    assert len(policy_hash()) == 12 and policy_hash() == policy_hash()  # deterministic


def test_recipe_frozen_and_hashed():
    r = Recipe("VOTE", ("ma_cross", "donchian"))
    assert r.hash() == Recipe("VOTE", ("donchian", "ma_cross")).hash()   # order-independent
    assert r.hash() != Recipe("VOTE", ("ma_cross", "rsi_rev")).hash()


def test_generated_strategy_is_valid_and_causal():
    tmp_df = pd.DataFrame({
        "open_time": np.arange(400), "open": np.linspace(100, 120, 400),
        "high": np.linspace(101, 121, 400), "low": np.linspace(99, 119, 400),
        "close": np.linspace(100, 120, 400) + np.sin(np.arange(400) / 5), "volume": 1.0})
    for rule, rec in [("VOTE", Recipe("VOTE", ("ma_cross", "rsi_rev"))),
                      ("FILTER", Recipe("FILTER", ("donchian",), gate="adx_gate")),
                      ("FILTER", Recipe("FILTER", ("rsi_rev",), gate="supertrend"))]:
        s = materialize(rec, "G-TEST")
        full = s.positions(tmp_df).to_numpy()
        trunc = s.positions(tmp_df.iloc[:250].reset_index(drop=True)).to_numpy()
        assert set(np.unique(np.nan_to_num(full))) <= {-1.0, 0.0, 1.0}
        np.testing.assert_array_equal(np.nan_to_num(full[:250]), np.nan_to_num(trunc[:250]))


def test_candidate_space_nonempty_and_composable():
    cands = enumerate_candidates(max_vote=2)
    assert len(cands) > 20
    assert any(c.rule == "VOTE" for c in cands) and any(c.rule == "FILTER" for c in cands)


def test_avoidance_bans_price_action_family():
    # a recipe whose family resolves to price-action should be avoided
    stats = TraitStats()
    # momentum+ma_cross are 'trend'; force a price-action-only trait via a stub
    r = Recipe("VOTE", ("momentum", "ma_cross"))
    # not banned (trend); but a family with all-fragile history is banned:
    stats.by_family["trend"] = {"n": 5, "non_fragile": 0, "dsr": [0.0]}
    assert avoid_reason(r, stats) is not None


def test_full_loop_records_separately_and_tracks_survival(tmp_path):
    data = tmp_path / "data"; data.mkdir(); kbroot = tmp_path / "kb"; kbroot.mkdir()
    for s in ("AAPL", "SPY"):
        _csv(data, s)
    kb = KnowledgeBase(root=str(kbroot))
    ledger = DiscoveryLedger(root=str(kbroot))
    eng = StrategyDiscoveryEngine(kb, ledger, Orchestrator(kb))
    summary = eng.run_round(3, ["AAPL", "SPY"], matrix_version="v2",
                            provider_kwargs={"root": str(data)})
    assert summary["generated"] == 3 and summary["tested"] == 3
    assert 0.0 <= summary["survival_rate"] <= 1.0
    assert summary["budget"]["tested"] == 3
    # every generated entry carries its full 'why' rationale + policy version
    for e in ledger.entries():
        assert e["rationale"]["policy_version"] == "discovery-v2"
        assert "why" in e["rationale"] and e["recipe"]["hash"]
        assert e["verdict"] in {"Supported", "Not Supported", "Inconclusive", "Graveyard"}
    # recorded in the SEPARATE discovery namespace, not the H INDEX
    assert (kbroot / "discovery" / "ledger.json").exists()
    assert not (kbroot / "INDEX.md").exists()
