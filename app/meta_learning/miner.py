"""Meta-learning — mine the corpus for patterns that no single strategy reveals.

Consumes the Knowledge Base (read-only) and produces a structured learning
report: verdict distribution, per-class behaviour, best generalizers, fragility
traits, and meta-hypothesis tallies. This is the layer that turns "we tested N
strategies" into "here is what separates robust from fragile."

Deterministic and grounded — every number traces to a recorded result.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from app.knowledge_base import KnowledgeBase, query
from app.strategy_research.dossier import STRATEGY_META, _meta_vote


def _safe_mean(xs):
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def mine(kb: KnowledgeBase) -> Dict:
    results = query.tested_results(kb)
    report: Dict = {
        "n_tested": len(results),
        "verdicts": query.verdict_counts(kb),
        "by_class": {},
        "top_generalizers": [],
        "leaderboard_dsr": [],
        "meta_hypotheses": {},
        "fragility_traits": {},
    }
    if not results:
        return report

    for cls, rs in query.by_class(kb).items():
        report["by_class"][cls] = {
            "n": len(rs),
            "verdicts": {v: sum(1 for r in rs if r["verdict"] == v)
                         for v in {r["verdict"] for r in rs}},
            "mean_dsr": _safe_mean([r.get("dsr") for r in rs]),
            "mean_generalization": _safe_mean([r.get("generalization") for r in rs]),
            "mean_oos_sharpe_ann": _safe_mean([r.get("sharpe") for r in rs]),
        }

    report["top_generalizers"] = [
        {"id": r["strategy_id"], "class": r.get("class"),
         "generalization": r.get("generalization"), "dsr": r.get("dsr"),
         "verdict": r["verdict"]}
        for r in query.generalizers(kb, 0.0)[:8]
    ]
    report["leaderboard_dsr"] = [
        {"id": r["strategy_id"], "dsr": r.get("dsr"), "verdict": r["verdict"]}
        for r in query.leaderboard(kb, "dsr", 8)
    ]

    # Meta-hypothesis tallies from the ledger (fragility claims).
    ledger = kb.meta_ledger()
    metas: Dict[str, Dict] = {}
    for sid, rec in ledger.items():
        m = rec["meta"]
        t = metas.setdefault(m, {"tested": 0, "supports": 0, "contradicts": 0, "members": []})
        t["tested"] += 1
        if rec["vote"] in ("supports", "contradicts"):
            t[rec["vote"]] += 1
        t["members"].append(sid)
    report["meta_hypotheses"] = metas

    # Fragility-trait associations (what travels with a bad verdict).
    frag = [r for r in results if r["verdict"] in ("Graveyard", "Not Supported")]
    report["fragility_traits"] = {
        "fraction_fragile": len(frag) / len(results),
        "mean_trades_fragile": _safe_mean([r.get("num_trades") for r in frag]),
        "mean_maxdd_fragile": _safe_mean([r.get("max_drawdown") for r in frag]),
        "note": "regime-dependence is tracked in CROSS_ASSET_GENERALIZATION.md; "
                "per-regime fields will fold into results as the corpus grows.",
    }
    return report


def render_report(report: Dict) -> str:
    L = ["# Meta-Learning Report — Corpus Patterns", "",
         f"**strategies tested:** {report['n_tested']}", "",
         "> ⚠️ Research only. Grounded in recorded Knowledge-Base results; no advice.", "",
         "## Verdict distribution", ""]
    for v, c in sorted(report["verdicts"].items()):
        L.append(f"- {v}: {c}")
    L += ["", "## Behaviour by strategy class", "",
          "| class | n | mean DSR | mean generalization | mean OOS Sharpe (ann) | verdicts |",
          "|---|---|---|---|---|---|"]
    for cls, d in sorted(report["by_class"].items()):
        vv = ", ".join(f"{k}:{n}" for k, n in d["verdicts"].items())
        L.append(f"| {cls} | {d['n']} | {_fmt(d['mean_dsr'])} | "
                 f"{_fmt(d['mean_generalization'])} | {_fmt(d['mean_oos_sharpe_ann'])} | {vv} |")
    L += ["", "## Top generalizers (by generalization score)", ""]
    for r in report["top_generalizers"]:
        L.append(f"- {r['id']} ({r['class']}): generalization={_fmt(r['generalization'])}, "
                 f"DSR={_fmt(r['dsr'])} → {r['verdict']}")
    L += ["", "## Meta-hypotheses (fragility claims)", "",
          "| meta | tested | support | contradict | members |", "|---|---|---|---|---|"]
    for m, t in sorted(report["meta_hypotheses"].items()):
        L.append(f"| {m} | {t['tested']} | {t['supports']} | {t['contradicts']} | "
                 f"{', '.join(sorted(t['members']))} |")
    ft = report["fragility_traits"]
    L += ["", "## Fragility summary", "",
          f"- fraction fragile (Graveyard/Not Supported): {_fmt(ft.get('fraction_fragile'))}",
          f"- mean OOS trades among fragile: {_fmt(ft.get('mean_trades_fragile'))}",
          f"- mean OOS max drawdown among fragile: {_fmt(ft.get('mean_maxdd_fragile'))}",
          "", f"_{ft.get('note','')}_", ""]
    return "\n".join(L)


def _fmt(x):
    return "—" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))
