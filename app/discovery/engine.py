"""Strategy Discovery Engine — the closed learning loop.

    Knowledge Base → Meta-learning → Discovery Engine → new hypothesis
        → Research Core / Strategy Evaluation → updated Knowledge Base → (better next round)

Every proposal is frozen (recipe hash) and stamped with the discovery-policy
version + a full rationale before it is tested, so it is pre-registered and
auditable. Generated strategies are recorded in the SEPARATE discovery ledger,
never in the pre-registered TradingView catalogue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.knowledge_base import KnowledgeBase
from app.research_core import Orchestrator
from app.strategy_research.strategies import register_strategy

from . import generator
from .ledger import DiscoveryLedger
from .policy import DISCOVERY_POLICY_VERSION, policy_hash
from .traits import TraitStats, score_breakdown


@dataclass
class Proposal:
    gid: str
    label: str
    priority: float
    rationale: Dict = field(default_factory=dict)


class StrategyDiscoveryEngine:
    def __init__(self, kb: Optional[KnowledgeBase] = None,
                 ledger: Optional[DiscoveryLedger] = None,
                 orchestrator: Optional[Orchestrator] = None):
        self.kb = kb or KnowledgeBase()
        self.ledger = ledger or DiscoveryLedger(self.kb.root)
        self.orch = orchestrator or Orchestrator(self.kb)

    # -- generate (no testing) ------------------------------------------------ #
    def propose(self, k: int, round_no: int, max_vote: int = 2,
                persist: bool = True) -> List[Proposal]:
        """Generate & prioritise the top-k candidate recipes with full rationale.
        persist=True freezes them into the ledger (pre-registration) and registers
        them so they can be tested; persist=False is a dry-run preview."""
        stats = TraitStats.from_corpus(self.kb, self.ledger.entries())
        picks = generator.prioritize(stats, self.ledger, k, max_vote)
        proposals: List[Proposal] = []
        for recipe, _score in picks:
            bd = score_breakdown(recipe, stats)
            rationale = {
                "policy_version": DISCOVERY_POLICY_VERSION, "policy_hash": policy_hash(),
                "recipe_hash": recipe.hash(), "score": bd["score"],
                "components": bd["components"], "traits": bd["traits"],
                "corpus_n_at_proposal": bd["corpus_n"], "round": round_no,
                "why": _explain(bd),
            }
            if persist:
                gid = self.ledger.record_generated(recipe, round_no, rationale)
                register_strategy(self.ledger.rehydrate(gid))   # make it runnable
            else:
                gid = "(preview)"
            proposals.append(Proposal(gid, recipe.label(), bd["score"], rationale))
        return proposals

    # -- targeted variants of a given recipe (used by the research workflow) --- #
    def propose_variants_of(self, base: "Recipe", k: int = 3, tag: str = "") -> List[Proposal]:
        """Generate improved neighbours of a base recipe using fixed operators
        (add a regime gate; pivot the primary to a higher-DSR-family block; combine
        with a complementary block), scored by learned priors. Frozen + recorded
        with a rationale that names the base it improves on."""
        from .genome import Recipe
        stats = TraitStats.from_corpus(self.kb, self.ledger.entries())
        primary = base.blocks[0]
        candidates = [
            Recipe("FILTER", (primary,), gate="adx_gate"),          # + regime gate
            Recipe("FILTER", ("donchian",), gate="adx_gate"),       # pivot to breakout + gate
            Recipe("VOTE", tuple(sorted(set(base.blocks) | {"keltner"}))[:3]),  # + complementary
            Recipe("VOTE", tuple(sorted(set(base.blocks) | {"supertrend"}))[:3]),
        ]
        picks, out = [], []
        for r in candidates:
            if r.hash() == base.hash() or self.ledger.has_recipe(r):
                continue
            picks.append((r, score_breakdown(r, stats)))
        picks.sort(key=lambda rs: (-rs[1]["score"], rs[0].hash()))
        for recipe, bd in picks[:k]:
            rationale = {
                "policy_version": DISCOVERY_POLICY_VERSION, "policy_hash": policy_hash(),
                "recipe_hash": recipe.hash(), "score": bd["score"], "components": bd["components"],
                "traits": bd["traits"], "corpus_n_at_proposal": bd["corpus_n"],
                "variant_of": tag or base.label(), "why": _explain(bd) + f" (variant of {tag or base.label()})",
            }
            gid = self.ledger.record_generated(recipe, round_no=-1, rationale=rationale)
            register_strategy(self.ledger.rehydrate(gid))
            out.append(Proposal(gid, recipe.label(), bd["score"], rationale))
        return out

    # -- one full round: generate → test → record → learn --------------------- #
    def run_round(self, k: int, assets: List[str], *, round_no: Optional[int] = None,
                  matrix_version: str = "v2", provider: str = "csv",
                  provider_kwargs: Optional[Dict] = None, max_vote: int = 2) -> Dict:
        rn = round_no if round_no is not None else len(self.ledger.data["rounds"]) + 1
        proposals = self.propose(k, rn, max_vote)
        for pr in proposals:
            out = self.orch.run(pr.gid, assets, matrix_version=matrix_version,
                                provider=provider, provider_kwargs=provider_kwargs or {},
                                write=False)                 # do NOT touch the H-corpus
            self.ledger.record_result(pr.gid, out.report)
        summary = self.ledger.close_round(rn, [p.gid for p in proposals])
        summary["budget"] = self.ledger.budget()
        summary["policy_version"] = DISCOVERY_POLICY_VERSION
        return summary

    def survival_history(self) -> List[Dict]:
        return list(self.ledger.data["rounds"])

    def render_report(self) -> str:
        ents = self.ledger.entries()
        tested = self.ledger.tested()
        by_dsr = sorted(tested, key=lambda e: (e["result"] or {}).get("dsr") or 0, reverse=True)
        L = ["# Discovery Report — Generated Hypotheses", "",
             f"**policy:** {DISCOVERY_POLICY_VERSION} (`{policy_hash()}`) · "
             f"**budget:** {self.ledger.budget()} (generated / tested) · "
             f"**survivors:** {sum(1 for e in tested if e['survived'])}", "",
             "> ⚠️ Research only. Auto-generated hypotheses, recorded separately from the "
             "pre-registered TradingView universe. Grounded, frozen, and auditable.", "",
             "## Survival by round (is the generator improving?)", "",
             "| round | generated | tested | survived | survival rate | best DSR |",
             "|---|---|---|---|---|---|"]
        for r in self.ledger.data["rounds"]:
            L.append(f"| {r['round']} | {r['generated']} | {r['tested']} | {r['survived']} | "
                     f"{r['survival_rate']:.2f} | {r['best_dsr']:.3f} |")
        L += ["", "## Top generated hypotheses (by deflated Sharpe)", "",
              "| id | recipe | verdict | DSR | priority | why proposed |",
              "|---|---|---|---|---|---|"]
        for e in by_dsr[:10]:
            dsr = (e["result"] or {}).get("dsr")
            L.append(f"| {e['id']} | `{e['recipe']['label']}` | {e['verdict']} | "
                     f"{('%.3f' % dsr) if dsr is not None else '—'} | {e['priority']:.3f} | "
                     f"{e['rationale']['why']} |")
        L += ["", f"_Every hypothesis traces to its recipe hash and the `{DISCOVERY_POLICY_VERSION}` "
              "policy that proposed it; see `discovery/ledger.json` and `discovery/results/`._"]
        return "\n".join(L)


def _explain(bd: Dict) -> str:
    t = bd["traits"]; c = bd["components"]
    return (f"family '{t['family']}' (survival {c['family_non_fragile_rate']:.2f}, "
            f"meanDSR {c['family_mean_dsr']:.2f}), turnover '{t['turnover']}' "
            f"(survival {c['turnover_non_fragile_rate']:.2f})"
            + ("; +regime-gate" if c['gate_bonus'] else "")
            + ("; +multi-block" if c['multi_block_bonus'] else "")
            + f" → priority {bd['score']:.3f}")
