"""Learning regressions with fixture evidence only; no market evaluations."""
import json

import pytest

from app.discovery import DiscoveryLedger, StrategyDiscoveryEngine
from app.discovery.genome import Recipe
from app.discovery.learning_v2 import LearningStatsV2, policy_hash
from app.discovery.policy import policy_hash as v1_hash
from app.discovery.traits import TraitStats, avoid_reason
from app.knowledge_base import KnowledgeBase


class Corpus:
    def __init__(self, results=()):
        self.results = results

    def all_results(self):
        return self.results


def entry(verdict="Supported", real=True):
    return {"verdict": verdict, "traits": {"family": "trend", "turnover": "med"},
            "result": {"is_real": real, "dsr": 0.9}}


@pytest.mark.parametrize("verdict", ["Inconclusive", "Not Yet Tested", None])
def test_uncertain_outcomes_do_not_reward_or_ban(verdict):
    stats = LearningStatsV2.from_corpus(Corpus(), [entry(verdict)] * 10)
    assert stats.n == 0
    assert stats._rate(stats.by_family, "trend") == (0.5, 0.05)
    assert avoid_reason(Recipe("VOTE", ("momentum", "ma_cross")), stats) is None


@pytest.mark.parametrize("real", [False, None, "true", "false", 1])
def test_non_boolean_or_synthetic_evidence_is_excluded_in_both_sources(real):
    h = {"verdict": "Supported", "class": "trend-following", "is_real": real, "dsr": 0.9}
    assert LearningStatsV2.from_corpus(Corpus([h]), [entry(real=real)]).n == 0


def test_missing_provenance_is_excluded():
    g = entry()
    del g["result"]["is_real"]
    assert LearningStatsV2.from_corpus(Corpus(), [g]).n == 0


def test_real_conclusive_evidence_changes_rates_and_avoidance():
    stats = LearningStatsV2.from_corpus(Corpus(), [entry(), entry("Not Supported"), entry("Graveyard")])
    assert stats.n == 3
    assert stats._rate(stats.by_family, "trend")[0] == pytest.approx(1 / 3)
    failed = entry("Not Supported")
    failed["result"]["dsr"] = 0.0
    stats = LearningStatsV2.from_corpus(Corpus(), [failed] * 5)
    assert avoid_reason(Recipe("VOTE", ("momentum", "ma_cross")), stats)


def test_real_h_results_are_used_and_inconclusive_dsr_is_excluded():
    h = {"verdict": "Supported", "class": "trend-following", "is_real": True, "dsr": 0.2}
    stats = LearningStatsV2.from_corpus(Corpus([h]), [entry("Inconclusive")])
    assert stats.n == 1
    assert stats._rate(stats.by_family, "trend") == (1.0, 0.2)


def test_v1_behavior_and_distinct_policy_identity():
    assert TraitStats.from_corpus(Corpus(), [entry("Inconclusive")]).n == 1
    assert policy_hash() == policy_hash()
    assert policy_hash() != v1_hash()


def test_engine_records_v2_identity_without_rewriting_ledger(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    ledger = DiscoveryLedger(str(tmp_path))
    gid = ledger.record_generated(Recipe("VOTE", ("ma_cross", "momentum")), 1,
                                  {"policy_version": "discovery-v1"})
    before = (tmp_path / "discovery" / "ledger.json").read_bytes()
    engine = StrategyDiscoveryEngine(kb, ledger, learning_version="discovery-v2")
    preview = engine.propose(1, 2, persist=False)
    assert preview[0].rationale["policy_version"] == "discovery-v2"
    assert preview[0].rationale["policy_hash"] == policy_hash()
    assert (tmp_path / "discovery" / "ledger.json").read_bytes() == before
    proposal = engine.propose(1, 2)[0]
    stored = json.loads((tmp_path / "discovery" / "ledger.json").read_text())
    assert stored["entries"][gid]["rationale"]["policy_version"] == "discovery-v1"
    assert stored["entries"][proposal.gid]["rationale"]["policy_hash"] == policy_hash()
    assert ledger.budget() == {"generated": 2, "tested": 0}


def test_unknown_version_fails_before_creating_storage(tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(ValueError):
        StrategyDiscoveryEngine(KnowledgeBase(str(root)), learning_version="invalid")
    assert not root.exists()


def test_engine_uses_eligible_evidence_for_proposals_and_variants(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    h = {"verdict": "Inconclusive", "class": "trend-following", "is_real": True, "dsr": 0.99}
    path = results / "H-FIXTURE.json"
    path.write_text(json.dumps(h))
    kb = KnowledgeBase(str(tmp_path))
    ledger = DiscoveryLedger(str(tmp_path))
    gid = ledger.record_generated(Recipe("VOTE", ("ma_cross", "momentum")), 1, {})
    ledger.data["entries"][gid].update(entry(real=False))
    engine = StrategyDiscoveryEngine(kb, ledger, learning_version="discovery-v2")
    uncertain = engine.propose(1000, 2, persist=False)
    assert all(p.rationale["corpus_n_at_proposal"] == 0 for p in uncertain)
    h["verdict"] = "Supported"
    path.write_text(json.dumps(h))
    informed = engine.propose(1000, 2, persist=False)
    assert all(p.rationale["corpus_n_at_proposal"] == 1 for p in informed)
    before = {p.label: p.priority for p in uncertain}
    assert any(p.priority != before[p.label] for p in informed)
    variants = engine.propose_variants_of(Recipe("VOTE", ("ma_cross", "momentum")))
    assert variants
    for p in variants:
        assert p.rationale["corpus_n_at_proposal"] == 1
        assert p.rationale["policy_version"] == "discovery-v2"
        assert p.rationale["policy_hash"] == policy_hash()


def test_default_engine_uses_v2_and_explicit_v1_remains_available(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    ledger = DiscoveryLedger(str(tmp_path))
    engine = StrategyDiscoveryEngine(kb, ledger)
    assert engine.stats_type is LearningStatsV2
    proposal = engine.propose(1, 1)[0]
    assert proposal.rationale["policy_version"] == "discovery-v2"
    assert proposal.rationale["policy_hash"] == policy_hash()
    legacy = StrategyDiscoveryEngine(kb, ledger, learning_version="discovery-v1")
    assert legacy.stats_type is TraitStats
    assert legacy.learning_hash() == v1_hash()


def test_cli_preview_reports_active_v2_without_writing_evidence(tmp_path, monkeypatch, capsys):
    from app import cli

    kb = KnowledgeBase(str(tmp_path))
    monkeypatch.setattr(cli, "KnowledgeBase", lambda: kb)
    assert cli.main(["discover", "--propose-only", "--k", "1"]) == 0
    output = capsys.readouterr().out
    assert f"discovery policy discovery-v2 ({policy_hash()})" in output
    assert not (tmp_path / "discovery" / "ledger.json").exists()
    assert not list(tmp_path.rglob("*.json"))
