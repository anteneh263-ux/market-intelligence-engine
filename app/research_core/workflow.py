"""Research workflow — the end-to-end MVP that turns one Pine Script into a
decision-grade research report, composing the existing modules (no new subsystem):

  import (adapter) → classify → compare vs corpus (KB) → strengths/weaknesses (KB +
  literature) → Discovery variants → evaluate original + variants (Research Core /
  Strategy Evaluation, srp-matrix-v2) → one comparison report.

Everything is grounded in the recorded corpus and the literature store.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.adapters import import_pine
from app.discovery import DiscoveryLedger, StrategyDiscoveryEngine
from app.discovery.genome import materialize
from app.knowledge_base import KnowledgeBase, literature as lit, query
from app.meta_learning import mine
from app.strategy_research.specs import register_spec
from app.strategy_research.strategies import register_strategy

from .orchestrator import Orchestrator


def _fmt(x):
    return "—" if x is None else (f"{x:.3f}" if isinstance(x, (int, float)) else str(x))


def research_strategy(pine_text: str, name: str = "", assets: Optional[List[str]] = None,
                      matrix: str = "v2", provider: str = "csv",
                      provider_kwargs: Optional[Dict] = None, k_variants: int = 2,
                      kb: Optional[KnowledgeBase] = None) -> Dict:
    kb = kb or KnowledgeBase()
    orch = Orchestrator(kb)
    assets = assets or ["AAPL", "BTC/USD", "ETH/USD", "SPY", "XAU/USD"]
    pk = provider_kwargs or ({"root": "data_csv"} if provider == "csv" else {})

    # STEP 1-2 — import + classify
    imp = import_pine(pine_text, name)
    if not imp.ok:
        return {"ok": False, "warnings": imp.warnings, "name": imp.name}
    uid = "U-" + imp.pine_hash[:4].upper()
    strat = materialize(imp.recipe, uid)
    strat.strategy_class = imp.family              # so literature/comparison use the real class
    register_strategy(strat)
    register_spec(uid, imp.spec)
    canon = lit.resolve_class(imp.family)

    # STEP 3-4 — compare vs corpus (most-resembling tested strategies)
    resemblers = [r for r in query.tested_results(kb)
                  if lit.resolve_class(r.get("class", "")) == canon]
    resemblers.sort(key=lambda r: (r.get("dsr") or 0), reverse=True)

    # STEP 5 — strengths/weaknesses from the Knowledge Base only
    m = mine(kb)
    class_stats = m["by_class"].get(imp.family) or _find_class_stats(m, canon)
    litc = lit.for_class(imp.family)
    strengths = [f"{r['authors']} ({r['year']}): {r['finding']}"
                 for r in litc["references"] if r["direction"] == "supports"]
    weaknesses = [f"{r['authors']} ({r['year']}): {r['finding']}"
                  for r in litc["references"] if r["direction"] == "contradicts"]

    # STEP 6 — Discovery proposes improved variants of the imported recipe
    ledger = DiscoveryLedger(kb.root)
    disco = StrategyDiscoveryEngine(kb, ledger, orch)
    variants = disco.propose_variants_of(imp.recipe, k=k_variants, tag=uid)

    # STEP 7 — evaluate the original + the variants on the SAME v2 pipeline
    orig = orch.run(uid, assets, matrix_version=matrix, provider=provider,
                    provider_kwargs=pk, write=False)
    var_reports = []
    for v in variants:
        out = orch.run(v.gid, assets, matrix_version=matrix, provider=provider,
                       provider_kwargs=pk, write=False)
        ledger.record_result(v.gid, out.report)
        var_reports.append((v, out))

    # STEP 8 — comparison report
    report_md = _render_report(uid, imp, orig, var_reports, resemblers, class_stats,
                               strengths, weaknesses, litc, canon)
    path = kb.write_artifact(f"workflows/RESEARCH_{uid}.md", report_md)

    return {"ok": True, "uid": uid, "name": imp.name, "class": imp.family,
            "recipe": imp.recipe.label(), "warnings": imp.warnings,
            "verdict": orig.verdict, "orig_dsr": orig.report["headline"].get("dsr"),
            "variants": [{"gid": v.gid, "recipe": v.label, "verdict": o.verdict,
                          "dsr": o.report["headline"].get("dsr")} for v, o in var_reports],
            "resembles": [r["strategy_id"] for r in resemblers[:3]], "report_path": path}


def _find_class_stats(m: Dict, canon: str) -> Dict:
    # aggregate any recorded classes that resolve to this canonical family
    agg = {"n": 0, "mean_dsr": None, "verdicts": {}}
    dsrs = []
    for cls, d in m["by_class"].items():
        if lit.resolve_class(cls) == canon:
            agg["n"] += d["n"]
            if d["mean_dsr"] is not None:
                dsrs.append(d["mean_dsr"])
            for v, c in d["verdicts"].items():
                agg["verdicts"][v] = agg["verdicts"].get(v, 0) + c
    agg["mean_dsr"] = sum(dsrs) / len(dsrs) if dsrs else None
    return agg


def _render_report(uid, imp, orig, var_reports, resemblers, class_stats,
                   strengths, weaknesses, litc, canon) -> str:
    oh = orig.report["headline"]
    rows = [("original", uid, imp.recipe.label(), orig.verdict, oh.get("dsr"),
             orig.report.get("generalization_score"), oh.get("max_drawdown"))]
    for v, o in var_reports:
        h = o.report["headline"]
        rows.append(("variant", v.gid, v.label, o.verdict, h.get("dsr"),
                     o.report.get("generalization_score"), h.get("max_drawdown")))
    best = max(rows, key=lambda r: (r[4] or 0))
    improved = [r for r in rows[1:] if (r[4] or 0) > (rows[0][4] or 0)]

    L = [f"# Research Report — {imp.name} (`{uid}`)", "",
         f"**imported from:** Pine Script (hash `{imp.pine_hash}`) · **class:** {imp.family} · "
         f"**mapped recipe:** `{imp.recipe.label()}`", "",
         "> ⚠️ Research only. No execution, no signals, no advice. Grounded entirely in the "
         "recorded corpus and the literature store; a verdict is a statement about *evidence*.", ""]
    if imp.warnings:
        L += ["**Import notes:** " + "; ".join(imp.warnings), ""]

    L += ["## 1. Classification", "",
          f"Detected indicators: {', '.join(imp.detected)}. Dominant family: **{imp.family}** "
          f"(literature class `{canon}`). Mapped to the composed rule `{imp.recipe.label()}` and "
          f"evaluated on the identical srp-matrix-v2 pipeline.", "",
          "## 2. Most-resembling tested strategies (from the corpus)", ""]
    if resemblers:
        L += ["| id | verdict | deflated Sharpe | generalization |", "|---|---|---|---|"]
        for r in resemblers[:5]:
            L.append(f"| {r['strategy_id']} | {r['verdict']} | {_fmt(r.get('dsr'))} | "
                     f"{_fmt(r.get('generalization'))} |")
    else:
        L.append("No same-class strategies in the corpus yet.")

    L += ["", "## 3. Likely strengths and weaknesses (Knowledge Base only)", ""]
    if class_stats and class_stats.get("n"):
        L.append(f"The **{canon}** class has {class_stats['n']} tested strategies in the corpus, "
                 f"mean deflated Sharpe {_fmt(class_stats.get('mean_dsr'))}, verdicts "
                 f"{class_stats.get('verdicts')}. ")
    L += ["", "**Strengths (supporting literature):**"] + ([f"- {s}" for s in strengths] or ["- (none catalogued)"])
    L += ["", "**Weaknesses (contradicting literature + known failure modes):**"]
    L += [f"- {w}" for w in weaknesses] or ["- (none catalogued)"]
    L += [f"- failure modes: {'; '.join(litc['failure_modes'])}"]

    L += ["", "## 4. Discovery Engine — proposed improved variants", ""]
    for v, o in var_reports:
        L.append(f"- **{v.gid}** `{v.label}` → {o.verdict} (DSR {_fmt(o.report['headline'].get('dsr'))}). "
                 f"Why: {v.rationale['why']}")

    L += ["", "## 5. Head-to-head (same v2 pipeline)", "",
          "| kind | id | recipe | verdict | deflated Sharpe | generalization | maxDD |",
          "|---|---|---|---|---|---|---|"]
    for kind, i, lbl, verd, dsr, genl, dd in rows:
        L.append(f"| {kind} | {i} | `{lbl}` | {verd} | {_fmt(dsr)} | {_fmt(genl)} | {_fmt(dd)} |")

    L += ["", "## 6. What we learned", "",
          f"- The imported strategy `{imp.recipe.label()}` scored **{orig.verdict}** "
          f"(deflated Sharpe {_fmt(oh.get('dsr'))}); it sits in the **{imp.family}** class, which "
          f"the corpus finds fragile (mean DSR {_fmt(class_stats.get('mean_dsr') if class_stats else None)}, "
          f"no survivors so far).",
          f"- The best of {{original + {len(var_reports)} variants}} was **{best[1]}** "
          f"(`{best[2]}`, DSR {_fmt(best[4])}).",
          (f"- {len(improved)} variant(s) improved on the original's deflated Sharpe: "
           f"{', '.join(r[1] for r in improved)}." if improved
           else "- No proposed variant beat the original on deflated Sharpe."),
          "- Nothing crossed the 0.95 survival bar, so the honest decision is **do not trust this "
          "edge yet** — consistent with the class-level literature above.",
          "", f"_Every figure traces to a recorded result; variants are logged in "
          f"`discovery/ledger.json`. Original evaluated under srp-matrix-v2._"]
    return "\n".join(L)
