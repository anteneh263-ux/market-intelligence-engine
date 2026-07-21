"""Auto-update the knowledge base from an EvaluationReport.

After STEP 5 runs, this module writes STEP 5/6 results into the strategy's
dossier, updates the INDEX row, and updates the meta-hypothesis tallies — so the
knowledge base stays in lock-step with the evidence with no manual editing.

Data-integrity rule (enforced here): if the report came from synthetic data
(`is_real=False`), the verdict is NOT written as evidence. An illustrative note is
recorded instead and the dossier verdict stays "Not Yet Tested".
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict

from .evaluation.pipeline import EvaluationReport

# Strategy -> meta-hypothesis and each meta's registered members (from
# STRATEGY_UNIVERSE_REGISTRATION.md). The metas are FRAGILITY claims, so a
# "Not Supported"/"Graveyard" verdict SUPPORTS the meta and a "Supported"
# verdict CONTRADICTS it.
STRATEGY_META: Dict[str, str] = {
    "H-001": "M-001", "H-010": "M-001", "H-012": "M-001", "H-013": "M-001",
    "H-015": "M-001", "H-022": "M-001", "H-025": "M-001", "H-026": "M-001",
    "H-002": "M-002", "H-006": "M-002", "H-007": "M-002", "H-011": "M-002",
    "H-003": "M-003", "H-004": "M-003", "H-009": "M-003", "H-020": "M-003",
    "H-021": "M-003",
    "H-005": "M-004", "H-018": "M-004", "H-019": "M-004", "H-024": "M-004",
    "H-008": "M-005", "H-014": "M-005", "H-016": "M-005", "H-017": "M-005",
    "H-023": "M-006",
}
META_MEMBERS: Dict[str, int] = {}
for _sid, _mid in STRATEGY_META.items():
    META_MEMBERS[_mid] = META_MEMBERS.get(_mid, 0) + 1


def _meta_vote(verdict: str) -> str:
    if verdict in ("Not Supported", "Graveyard"):
        return "supports"        # confirms the fragility claim
    if verdict == "Supported":
        return "contradicts"
    return "neither"

def _find_kb_root() -> str:
    """Locate the knowledge-base directory robustly (production-safe).

    Order: MIE_KB_ROOT env var → walk up from this file looking for a
    'knowledge_base/' (packaged layout) or 'trading_research/knowledge_base/'
    (dev layout) → fall back to <project_root>/knowledge_base.
    """
    env = os.environ.get("MIE_KB_ROOT")
    if env:
        return env

    def _is_corpus(d: str) -> bool:
        # distinguish the corpus from the app/knowledge_base CODE package
        return os.path.isdir(d) and (
            os.path.isfile(os.path.join(d, "INDEX.md"))
            or os.path.isdir(os.path.join(d, "literature"))
            or os.path.isdir(os.path.join(d, "results")))

    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        for cand in (os.path.join(here, "knowledge_base"),
                     os.path.join(here, "trading_research", "knowledge_base")):
            if _is_corpus(cand):
                return cand
        here = os.path.dirname(here)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "knowledge_base")


_KB_ROOT = _find_kb_root()


def _fmt(x, pct=False):
    try:
        return f"{x*100:.1f}%" if pct else f"{x:.3f}"
    except Exception:
        return str(x)


def render_step5_markdown(rep: EvaluationReport) -> str:
    h = rep.headline
    lines = [
        f"**Run:** provider `{rep.provider}` (is_real={rep.is_real}) · "
        f"request `{rep.request_hash}` · matrix `{rep.matrix_hash}` "
        f"(`{rep.matrix_version}`) · assets: {', '.join(rep.assets_evaluated) or '—'}",
        "",
    ]
    if not rep.is_real:
        lines += ["> ⚠️ **ILLUSTRATIVE — synthetic data. Not evidence. Verdict not recorded "
                  "in the knowledge base.**", ""]
    lines += [
        "| Metric (out-of-sample, headline cost+slippage) | Value |",
        "|---|---|",
        f"| OOS Sharpe (annualised) | {_fmt(h.get('oos_sharpe_annual', 0))} |",
        f"| Deflated Sharpe (prob. edge is real) | {_fmt(h.get('dsr', 0))} |",
        f"| Sharpe 95% CI (per-period) | [{_fmt(h.get('sharpe_ci95',[0,0])[0])}, "
        f"{_fmt(h.get('sharpe_ci95',[0,0])[1])}] |",
        f"| Beats buy-and-hold | {h.get('beats_buy_hold')} |",
        f"| Beats matched random-timing (p95) | {h.get('beats_random')} |",
        f"| Max drawdown (OOS) | {_fmt(h.get('max_drawdown',0), pct=True)} |",
        f"| OOS trades / bars | {h.get('num_trades',0)} / {h.get('n_bars',0)} |",
        f"| Robustness / Stability / Generalization | {_fmt(rep.robustness_score)} / "
        f"{_fmt(rep.stability_score)} / {_fmt(rep.generalization_score)} |",
        f"| Confidence | {_fmt(rep.confidence_score)} |",
        "",
        f"**Mechanical verdict:** **{rep.verdict}**"
        + (f" — cause of death: {rep.cause_of_death}" if rep.cause_of_death else ""),
        "",
        "Reasons: " + "; ".join(rep.verdict_reasons),
    ]
    if rep.notes:
        lines += ["", "Notes:"] + [f"- {n}" for n in rep.notes]
    return "\n".join(lines)


def update_dossier_file(path: str, rep: EvaluationReport) -> bool:
    """Write rendered STEP 5 results into the dossier.

    Version control: a v1 run updates the original '## STEP 5' section. A v2 (or
    later) run NEVER overwrites v1 — it inserts/updates a dedicated
    '## STEP 5 · <version>' section just before STEP 6, so v1 stays as history."""
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        text = f.read()
    block = render_step5_markdown(rep)
    ver = rep.matrix_version

    if ver.endswith("v1"):
        pattern = r"(## STEP 5 —[^\n]*\n)(.*?)(\n## STEP 6)"
        repl = r"\1\n" + block.replace("\\", "\\\\") + r"\3"
        new_text, n = re.subn(pattern, repl, text, flags=re.DOTALL)
        if n == 0:
            return False
    else:
        header = f"## STEP 5 · {ver} — Scientific Evaluation"
        section = f"{header}\n\n{block}\n\n"
        if header in text:
            # Replace the existing versioned section (up to the next '## ' header).
            pat = re.compile(rf"{re.escape(header)}\n.*?(?=\n## )", re.DOTALL)
            new_text = pat.sub(section.rstrip() + "\n", text)
        else:
            # Insert right before STEP 6, leaving the v1 STEP 5 untouched.
            new_text, n = re.subn(r"(\n## STEP 6)", "\n" + section + r"\1", text, count=1)
            if n == 0:
                return False
    with open(path, "w") as f:
        f.write(new_text)
    return True


def update_index(index_path: str, rep: EvaluationReport) -> bool:
    """Update the INDEX row for this strategy (verdict, robustness, generalization)."""
    if not os.path.exists(index_path) or not rep.is_real:
        return False
    with open(index_path, "r") as f:
        text = f.read()
    sid = rep.strategy_id
    # Match a table row beginning with the strategy id and rewrite trailing score cells.
    row_re = re.compile(rf"(\|\s*\**{re.escape(sid)}\**\s*\|[^\n]*)")
    def _rewrite(m):
        cells = [c.strip() for c in m.group(1).split("|")]
        # cells: ['', id, name, class, status, verdict, robustness, generalization, link, '']
        if len(cells) >= 9:
            vtag = "v2" if rep.matrix_version.endswith("v2") else "v1"
            cells[4] = f"STEP 1–5 done ({vtag})"
            cells[5] = f"{rep.verdict} ({vtag})"
            cells[6] = _fmt(rep.robustness_score)
            cells[7] = _fmt(rep.generalization_score)
        return "| " + " | ".join(cells[1:-1]) + " |"
    new_text, n = row_re.subn(_rewrite, text)
    if n:
        with open(index_path, "w") as f:
            f.write(new_text)
    return bool(n)


def update_meta(kb_root: str, rep: EvaluationReport) -> bool:
    """Idempotently record this strategy's verdict in meta_ledger.json and refresh
    the meta-hypothesis status cells in INDEX.md. Real data only."""
    if not rep.is_real:
        return False
    mid = STRATEGY_META.get(rep.strategy_id)
    if not mid:
        return False
    ledger_path = os.path.join(kb_root, "meta_ledger.json")
    ledger = {}
    if os.path.exists(ledger_path):
        try:
            ledger = json.load(open(ledger_path))
        except Exception:
            ledger = {}
    ledger[rep.strategy_id] = {
        "meta": mid, "verdict": rep.verdict, "vote": _meta_vote(rep.verdict),
        "robustness": rep.robustness_score, "request_hash": rep.request_hash,
    }
    with open(ledger_path, "w") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)

    # Recompute per-meta tallies and rewrite the INDEX meta-status cells.
    tallies: Dict[str, Dict[str, int]] = {}
    for sid, rec in ledger.items():
        m = rec["meta"]
        t = tallies.setdefault(m, {"tested": 0, "supports": 0, "contradicts": 0})
        t["tested"] += 1
        if rec["vote"] in t:
            t[rec["vote"]] += 1

    index_path = os.path.join(kb_root, "INDEX.md")
    if not os.path.exists(index_path):
        return True
    text = open(index_path).read()
    for m, t in tallies.items():
        members = META_MEMBERS.get(m, 0)
        status = (f"{t['tested']}/{members} tested — {t['supports']} support / "
                  f"{t['contradicts']} contradict")
        row_re = re.compile(rf"(\|\s*\**{re.escape(m)}\**\s*\|[^\n]*\|)([^|\n]*)(\|\s*\n)")
        text = row_re.sub(lambda mo: mo.group(1) + f" {status} " + mo.group(3), text)
    with open(index_path, "w") as f:
        f.write(text)
    return True


def write_results(kb_root: str, rep: EvaluationReport) -> str:
    """Write a machine-readable results record: knowledge_base/results/<id>.json.
    This is the structured source of truth that `load_dossier` reads."""
    rdir = os.path.join(kb_root, "results")
    os.makedirs(rdir, exist_ok=True)
    h = rep.headline or {}
    record = {
        "strategy_id": rep.strategy_id, "name": rep.strategy_name,
        "class": rep.strategy_class, "verdict": rep.verdict,
        "is_real": rep.is_real, "provider": rep.provider,
        "assets": rep.assets_evaluated,
        "sharpe": h.get("oos_sharpe_annual"),
        "sharpe_oos_per_period": h.get("oos_sharpe"),
        "dsr": h.get("dsr"), "max_drawdown": h.get("max_drawdown"),
        "beats_buy_hold": h.get("beats_buy_hold"),
        "beats_random": h.get("beats_random"),
        "num_trades": h.get("num_trades"), "n_bars": h.get("n_bars"),
        "robustness": rep.robustness_score, "stability": rep.stability_score,
        "generalization": rep.generalization_score, "confidence": rep.confidence_score,
        "request_hash": rep.request_hash, "matrix_hash": rep.matrix_hash,
        "verdict_reasons": rep.verdict_reasons, "cause_of_death": rep.cause_of_death,
    }
    record["matrix_version"] = rep.matrix_version
    # Archive per version, and keep <id>.json as the 'latest' pointer.
    for path in (os.path.join(rdir, f"{rep.strategy_id}.{rep.matrix_version}.json"),
                 os.path.join(rdir, f"{rep.strategy_id}.json")):
        with open(path, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
    return os.path.join(rdir, f"{rep.strategy_id}.{rep.matrix_version}.json")


def load_dossier(strategy_id: str, kb_root: str | None = None) -> Dict:
    """Load the machine-readable results record for a strategy (written by the
    engine on the last real run). Returns {} if none exists yet."""
    root = kb_root or _KB_ROOT
    path = os.path.join(root, "results", f"{strategy_id.upper()}.json")
    if not os.path.exists(path):
        return {"strategy_id": strategy_id.upper(), "verdict": "Not Yet Tested"}
    return json.load(open(path))


def apply_report(rep: EvaluationReport, kb_root: str | None = None) -> Dict:
    """Write STEP 5 into the dossier, update the INDEX row, refresh meta-hypothesis
    tallies, and write a machine-readable results record. Synthetic runs update
    only the STEP 5 section (flagged illustrative) and a results record marked
    is_real=False; they never touch INDEX verdicts or meta tallies."""
    root = kb_root or _KB_ROOT
    result = {"dossier_written": False, "index_written": False,
              "meta_written": False, "results_written": False, "kb_root": root}
    if os.path.isdir(root):
        for fn in os.listdir(root):
            if fn.startswith(rep.strategy_id) and fn.endswith(".md"):
                result["dossier_written"] = update_dossier_file(os.path.join(root, fn), rep)
                break
        result["index_written"] = update_index(os.path.join(root, "INDEX.md"), rep)
        result["meta_written"] = update_meta(root, rep)
        result["results_path"] = write_results(root, rep)
        result["results_written"] = True
    return result
