"""Deterministic dossier generator — part of the Market Intelligence Engine.

Assembles a standardized STEP 1-4 scientific dossier from STRUCTURED INPUTS ONLY:

    strategy specification (specs.py) + live class/params/grid (registry)
    + literature (Knowledge Base literature store)
    + pre-registered evaluation protocol (frozen matrix)

It is a pure assembler: fixed template, slot-filled from data. No wall-clock, no
randomness, no free-text generation, no invented literature or claims. Identical
inputs → byte-identical output (verified). Every dossier carries a provenance
footer (spec hash, literature version, protocol version+hash, generator version)
so it is fully auditable.

Runs for ANY strategy — TradingView built-in, AI-generated, or user-submitted —
before it enters STEP 5.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, Tuple

from app.knowledge_base import literature as lit
from app.strategy_research.dossier import STRATEGY_META, _KB_ROOT
from app.strategy_research.specs import get_spec
from app.strategy_research.strategies import get_strategy
from .versioning import resolve

DOSSIER_GENERATOR_VERSION = "dossier-gen-v1"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _protocol(matrix) -> Dict:
    return {
        "version": matrix.version, "hash": matrix.hash(), "pooling": matrix.pooling,
        "cost_bps_levels": list(matrix.cost_bps_levels),
        "slippage_bps_levels": list(matrix.slippage_bps_levels),
        "n_splits": matrix.n_splits, "min_train": matrix.min_train,
        "headline_cost_bps": matrix.headline_cost_bps,
        "headline_slippage_bps": matrix.headline_slippage_bps,
        "dsr_threshold": matrix.dsr_threshold, "bh_q": matrix.bh_q,
        "max_drawdown_gate": matrix.max_drawdown_gate, "min_trades": matrix.min_trades,
        "random_seeds": matrix.random_seeds,
        "regime_window": matrix.regime_window, "regime_crisis_q": matrix.regime_crisis_q,
    }


def _inputs(strategy_id: str, matrix) -> Dict:
    s = get_strategy(strategy_id)
    spec = get_spec(strategy_id)
    return {
        "strategy_id": s.strategy_id, "name": s.name, "class": s.strategy_class,
        "direction": s.direction,
        "params": dict(sorted(s.default_params.items())),
        "grid_size": len(s.param_grid()),
        "spec": spec,
        "literature": lit.for_class(s.strategy_class),
        "literature_version": lit.literature_version(),
        "protocol": _protocol(matrix),
        "meta": STRATEGY_META.get(s.strategy_id),
        "generator": ("dossier-gen-v2" if matrix.version == "srp-matrix-v4"
                      else DOSSIER_GENERATOR_VERSION),
    }


def _hash(inputs: Dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _bullets(items):
    return "\n".join(f"- {x}" for x in items)


def render(strategy_id: str, matrix=None) -> Tuple[str, str]:
    """Return (filename, markdown). Deterministic for fixed (strategy, matrix)."""
    matrix = matrix or resolve("v2")
    I = _inputs(strategy_id, matrix)
    h = _hash(I)
    sp, P, L = I["spec"], I["protocol"], I["literature"]
    params = ", ".join(f"{k}={v}" for k, v in I["params"].items()) or "none"

    lit_lines = "\n".join(
        f"- **{r['authors']} ({r['year']})** — *{r['direction']}:* {r['finding']}"
        for r in L["references"]) or "- (no catalogued literature for this class)"
    src_lines = "\n".join(
        f"- {r['authors']} ({r['year']}), *{r['title']}*, {r['venue']} — {r['url']}"
        for r in L["references"]) or "- (none)"
    meta = I["meta"]
    step7 = (f"Bears on **{meta}** (see INDEX.md meta-hypotheses). {I['strategy_id']}'s "
             f"out-of-sample result becomes one vote for or against {meta}."
             if meta else "Meta-hypothesis mapping: (unassigned).")

    timing_statement = sp.get('lookahead', 'engine shifts positions forward one bar, so look-ahead is structurally impossible')
    if I["generator"] == "dossier-gen-v2":
        timing_statement = (
            "Signals must use only information available by their bar close. "
            "The engine applies them one bar later; this prevents same-bar execution "
            "but does not validate signal causality. Prefix/future-perturbation tests "
            "are finite checks, and point-in-time data availability must be verified separately"
        )

    md = f"""# Strategy Dossier — {I['strategy_id']}: {I['name']}

**strategy_id:** {I['strategy_id']} · **source:** {sp.get('source', 'unknown')} · **class:** {I['class']} · **direction:** {I['direction']}
**status:** STEPs 1–4 auto-generated (deterministic); STEP 5 **pre-registered, pending evaluation**; verdict **Not Yet Tested**
**engine:** `app.strategy_research` · **protocol:** `{P['version']}` (`{P['hash']}`, pooling={P['pooling']})

> ⚠️ Research only. No execution, no signals, no advice. Auto-generated by
> `{I['generator']}` from structured inputs; every statement is grounded in the recorded
> specification or the literature store. A verdict is a statement about *evidence*.

---

## STEP 1 — Strategy Analysis

Assembled from the recorded specification (`app/strategy_research/specs.py`); the engine
implementation is the frozen, auditable source of truth.

- **Indicators:** {"; ".join(sp.get('indicators', [])) or 'n/a'}
- **Entry (long):** {sp.get('entry_long', 'n/a')}
- **Entry (short):** {sp.get('entry_short', 'n/a')}
- **Exit:** {sp.get('exit', 'n/a')}
- **Filters:** {sp.get('filters', 'none')}
- **Parameters (canonical defaults):** {params}
- **Risk management:** {sp.get('risk', 'none')}
- **Position sizing:** {sp.get('sizing', 'n/a')}
- **Implicit assumptions (the real targets of testing):**
{_bullets(sp.get('assumptions', [])) or '- (none recorded)'}
- **Script-integrity / look-ahead check:** {timing_statement}.

## STEP 2 — Falsifiable Hypothesis

**{I['strategy_id']}.** *This rule produces positive risk-adjusted returns net of realistic costs,
and this edge (a) survives walk-forward out-of-sample, (b) generalizes across assets not used to
select its parameters, and (c) is not explained by the number of parameterizations tried
(data-snooping budget = {I['grid_size']} configuration(s)).*

**Kill criteria (pre-committed, frozen before any run):** **Not Supported** if the out-of-sample
**deflated Sharpe < {P['dsr_threshold']}**, **or** the edge appears in a minority of asset/cost
conditions, **or** it fails to beat **buy-and-hold** and **matched random-timing (95th percentile)**
on risk-adjusted terms. **Graveyard** if out-of-sample max drawdown exceeds the
{int(P['max_drawdown_gate']*100)}% gate or fewer than {P['min_trades']} trades occur.

## STEP 3 — Research Review (evidence, not truth)

Class-level literature for **{L['canonical_class']}** (from the Knowledge Base literature store
`{I['literature_version']}`; real citations only). Papers are evidence, not truth — data-snooping
and publication bias apply to them too.

{lit_lines}

**Expected failure modes (predicted before testing):** {"; ".join(L['failure_modes']) or 'n/a'}.

## STEP 4 — Experiment Design (pre-registered; frozen before any run)

Executed by the Market Intelligence Engine through the **identical** `{P['version']}` pipeline used
for every strategy (hash `{P['hash']}`):

- **Data:** real OHLCV via a swappable provider, across multiple assets **including assets not used
  to select parameters** (generalization).
- **Walk-forward:** {P['n_splits']}-split expanding-window; parameters re-selected in-sample each
  fold, evaluated out-of-sample; OOS segments stitched into the only curve the verdict trusts.
- **Data-snooping budget:** {I['grid_size']} parameter configuration(s), priced into the deflated Sharpe.
- **Transaction costs:** {P['cost_bps_levels']} bps/side. **Slippage:** {P['slippage_bps_levels']} bps.
  Headline scenario = {P['headline_cost_bps']}+{P['headline_slippage_bps']} bps.
- **Benchmarks:** buy-and-hold **and** matched random-timing ({P['random_seeds']} seeds; must beat the 95th pct).
- **Regimes:** trend / range / crisis labelled ex-ante from price only (window {P['regime_window']},
  crisis quantile {P['regime_crisis_q']}); never by outcome.
- **Primary metric (frozen):** out-of-sample **deflated Sharpe** ≥ {P['dsr_threshold']}.
  **Secondary:** max drawdown, profit factor, turnover, regime-conditional performance, bootstrap Sharpe CI.
- **Multiplicity:** Benjamini–Hochberg FDR (q={P['bh_q']}) across the strategy universe.
- **Pooling:** {P['pooling']} (multi-asset aggregation).

**Registered; not alterable after seeing results.** Run:
`python -m app.cli evaluate --target {I['strategy_id']} --assets ... --matrix {P['version'][-2:]}`

## STEP 5 — Scientific Evaluation

**Pending evaluation.** The engine writes results here automatically on a real-OHLCV run
(`{P['version']}`). Synthetic-data output is labelled illustrative and never sets the verdict.

## STEP 6 — Knowledge-Base Record

| Field | Value |
|---|---|
| robustness_score | pending |
| stability_score | pending |
| generalization_score | pending |
| risk_adjusted | pending (deflated Sharpe / maxDD / profit factor / #trades) |
| failure_modes | *predicted:* {"; ".join(L['failure_modes'][:4]) or 'n/a'} |
| confidence | pending |
| verdict | **Not Yet Tested** |
| literature | {L['canonical_class']} class ({len(L['references'])} refs) |
| provenance_hash | assigned at STEP 5 run |

## STEP 7 — Contribution to Learning

{step7}

---

**Sources:**
{src_lines}

---
*Auto-generated by `{I['generator']}` · inputs hash `{h}` · literature `{I['literature_version']}` ·
protocol `{P['version']}` (`{P['hash']}`). Deterministic: identical inputs yield identical output.*
"""
    filename = f"{I['strategy_id']}_{_slug(I['name'])}.md"
    return filename, md


def input_hash(strategy_id: str, matrix=None) -> str:
    return _hash(_inputs(strategy_id, matrix or resolve("v2")))


def ensure_dossier(strategy_id: str, kb_root: str | None = None, matrix=None,
                   overwrite: bool = False) -> Dict:
    """Generate the dossier if missing (or if overwrite). Never clobbers an existing
    hand-authored dossier unless overwrite=True. Returns {path, written, existed}."""
    root = kb_root or _KB_ROOT
    sid = strategy_id.upper()
    existing = None
    if os.path.isdir(root):
        for fn in os.listdir(root):
            if fn.startswith(sid + "_") and fn.endswith(".md"):
                existing = os.path.join(root, fn)
                break
    if existing and not overwrite:
        return {"path": existing, "written": False, "existed": True}
    filename, md = render(sid, matrix)
    path = os.path.join(root, filename)
    with open(path, "w") as f:
        f.write(md)
    return {"path": path, "written": True, "existed": bool(existing)}
