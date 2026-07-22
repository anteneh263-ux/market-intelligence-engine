"""Deterministic research-safety batch runner.

Runs the SAME safety checks over EVERY registered strategy, with identical assets, date
range, costs and slippage, and records the result as exactly one candidate trial per
strategy in the existing append-only trial ledger. For each strategy it:

  1. evaluates through the frozen pipeline (unchanged calculations);
  2. runs the input audit;
  3. runs the target-only control comparison;
  4. runs the promotion gate;
  5. records ONE candidate trial (technical failures with in_dsr = false);

then writes an atomic, timestamped JSON report plus ``latest.json`` under the existing
research-safety data directory.

Guarantees:
  * Strategies are discovered dynamically (no hard-coded id list).
  * The strategy order is deterministic (sorted by id) and the JSON is canonical.
  * Re-running the identical batch does NOT duplicate trials — each trial carries a
    content-derived id and the ledger is idempotent on it.
  * It NEVER starts a forward test, touches a broker, or changes any existing
    evaluation, threshold, strategy behaviour or historical result.

Research only: no live trading, no broker, no order execution, no private keys.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Callable, Dict, List, Optional, Sequence

from app.strategy_research import EvalRequest, MATRICES, evaluate
from app.strategy_research.dossier import _KB_ROOT
from app.strategy_research.providers import DataRequest, get_provider
from app.strategy_research.strategies import available_strategies, get_strategy

from .input_audit import run_audit
from .promotion_gate import evaluate_promotion
from .target_control import compare
from .trial_ledger import (STATUS_TECHNICAL_FAILURE, STATUS_VALID, TrialLedger,
                           _atomic_write, _utc_now)

DEFAULT_CONTROL_ID = "CTRL-TARGET-ONLY"
_ROW_KEYS = ("strategy_id", "status", "oos_sharpe", "total_return", "max_drawdown",
             "num_trades", "dsr", "control_verdict", "promotion_decision",
             "input_audit_promotable", "in_dsr", "trial_id", "failure_reasons")


def content_trial_id(strategy_id: str, assets: Sequence[str], timeframe: str,
                     matrix: str, cost_bps: float, slippage_bps: float,
                     trial_type: str = "candidate") -> str:
    """Deterministic, content-derived trial id. Identical batch settings for a strategy
    always yield the same id, which is what makes the ledger idempotent on re-run."""
    core = {"strategy": strategy_id, "assets": sorted(assets), "timeframe": timeframe,
            "matrix": matrix, "cost_bps": float(cost_bps), "slippage_bps": float(slippage_bps),
            "trial_type": trial_type}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return "TC-" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def _round(x) -> Optional[float]:
    try:
        return round(float(x), 6)
    except (TypeError, ValueError):
        return None


def _row(strategy_id: str, *, status: str, headline: Optional[Dict] = None,
         control_verdict: Optional[str] = None, promotion_decision: Optional[str] = None,
         input_audit_promotable=None, in_dsr: bool = False, trial_id: str = "",
         failure_reasons: Optional[List[str]] = None) -> Dict:
    """Build one report row with a FIXED key schema (identical keys for valid rows and
    technical failures, so the JSON is uniform and deterministic)."""
    h = headline or {}
    return {
        "strategy_id": strategy_id,
        "status": status,
        "oos_sharpe": _round(h.get("oos_sharpe")),
        "total_return": _round(h.get("total_return")),
        "max_drawdown": _round(h.get("max_drawdown")),
        "num_trades": int(h.get("num_trades") or 0),
        "dsr": _round(h.get("dsr")),
        "control_verdict": control_verdict,
        "promotion_decision": promotion_decision,
        "input_audit_promotable": input_audit_promotable,
        "in_dsr": bool(in_dsr),
        "trial_id": trial_id,
        "failure_reasons": list(failure_reasons or []),
    }


def _discover(control_id: str, explicit: Optional[Sequence[str]]) -> List[str]:
    """Discover the strategies to evaluate. Dynamic by default (every registered id),
    excluding control strategies. Deterministically ordered."""
    ids = list(explicit) if explicit is not None else available_strategies()
    keep: List[str] = []
    for sid in ids:
        if sid == control_id:
            continue
        try:
            if getattr(get_strategy(sid), "strategy_class", "") == "control":
                continue
        except Exception:
            continue
        keep.append(sid)
    return sorted(keep)


def _process_one(sid: str, assets: List[str], timeframe: str, m, matrix_name: str,
                 provider: str, pk: Dict, control_id: str, ledger: TrialLedger,
                 cost_bps: float, slippage_bps: float) -> Dict:
    tid = content_trial_id(sid, assets, timeframe, matrix_name, cost_bps, slippage_bps)

    # 1) frozen evaluation (calculations unchanged) --------------------------- #
    try:
        rep = evaluate(EvalRequest(strategy_id=sid, provider=provider, assets=list(assets),
                                   timeframe=timeframe, matrix_version=m.version), m, pk)
    except Exception as e:                       # technical failure: never a valid result
        ledger.record(sid, "candidate", status=STATUS_TECHNICAL_FAILURE, trial_id=tid,
                      reason=f"evaluation raised {type(e).__name__}: {e}")
        return _row(sid, status="technical_failure", in_dsr=False, trial_id=tid,
                    failure_reasons=[f"evaluation raised {type(e).__name__}: {e}"])

    if not rep.is_real or not rep.assets_evaluated:
        reason = ("provider is not real market data" if not rep.is_real
                  else "no asset had enough data to evaluate")
        ledger.record(sid, "candidate", status=STATUS_TECHNICAL_FAILURE, trial_id=tid,
                      reason=reason)
        return _row(sid, status="technical_failure", in_dsr=False, trial_id=tid,
                    failure_reasons=[reason])

    h = rep.headline
    strat = get_strategy(sid)

    # 2) input audit --------------------------------------------------------- #
    audit_promotable = None
    declared = getattr(strat, "declared_inputs", None)
    if declared is not None:
        try:
            df0 = get_provider(provider, **pk).get(
                DataRequest(asset=rep.assets_evaluated[0], timeframe=timeframe)).df
            audit_promotable = bool(run_audit(strat, df0, declared=declared)["promotable"])
        except Exception:
            audit_promotable = None

    # 3) target-only control comparison -------------------------------------- #
    try:
        control_verdict = compare(sid, control_id, assets, timeframe=timeframe,
                                  matrix=matrix_name, provider=provider,
                                  provider_kwargs=pk)["verdict"]
    except Exception as e:
        control_verdict = f"error:{type(e).__name__}"

    # 4) promotion gate ------------------------------------------------------ #
    try:
        promo = evaluate_promotion(sid, assets, control_id=control_id, timeframe=timeframe,
                                   matrix=matrix_name, provider=provider,
                                   provider_kwargs=pk, write=False)
        decision = promo["decision"]
        failing = [g["gate"] for g in promo["gates"]
                   if g["status"] in ("FAIL", "NOT_AVAILABLE")]
    except Exception as e:
        decision = "ERROR"
        failing = [f"promotion gate raised {type(e).__name__}"]

    # 5) record exactly one valid candidate trial (idempotent on tid) -------- #
    ledger.record(sid, "candidate", status=STATUS_VALID, params=dict(strat.default_params),
                  trial_id=tid)

    return _row(sid, status=rep.verdict, headline=h, control_verdict=control_verdict,
                promotion_decision=decision, input_audit_promotable=audit_promotable,
                in_dsr=True, trial_id=tid, failure_reasons=failing)


def run_safety_batch(assets: Sequence[str], *, timeframe: str = "1d",
                     matrix: str = "srp-matrix-v2", provider: str = "csv",
                     provider_kwargs: Optional[Dict] = None,
                     control_id: str = DEFAULT_CONTROL_ID, root: Optional[str] = None,
                     strategies: Optional[Sequence[str]] = None,
                     clock: Callable[[], str] = _utc_now, write: bool = True) -> Dict:
    """Evaluate every registered strategy under identical settings and return a
    deterministic report. Writes a timestamped report + latest.json when write=True.
    NEVER starts a forward test."""
    assets = [a for a in assets]
    pk = provider_kwargs or ({"root": "data_csv"} if provider == "csv" else {})
    m = MATRICES.get(matrix, MATRICES["v2"])
    cost_bps = float(m.headline_cost_bps)
    slippage_bps = float(m.headline_slippage_bps)

    ledger = TrialLedger(root=root, clock=clock)
    ids = _discover(control_id, strategies)

    matrix_name = m.version                    # canonical id (stable across 'v2' alias)
    rows = [_process_one(sid, assets, timeframe, m, matrix_name, provider, pk, control_id,
                         ledger, cost_bps, slippage_bps) for sid in ids]

    decisions = [r["promotion_decision"] for r in rows]
    summary = {
        "n_strategies": len(rows),
        "n_valid": sum(1 for r in rows if r["status"] != "technical_failure"),
        "n_technical_failures": sum(1 for r in rows if r["status"] == "technical_failure"),
        "n_promote": decisions.count("PROMOTE"),
        "n_hold": decisions.count("HOLD"),
        "n_reject": decisions.count("REJECT"),
    }
    report = {
        "kind": "research_safety_batch",
        "generated_at": clock(),
        "assets": list(assets),
        "timeframe": timeframe,
        "matrix": m.version,
        "provider": provider,
        "cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
        "control_id": control_id,
        "strategies": rows,
        "summary": summary,
        "ledger": ledger.summary(),
        "note": ("Research only — no forward test was started, no broker, no orders, no "
                 "private keys. A verdict/decision is a statement about evidence, not advice."),
    }

    if write:
        base = os.path.join(root or _KB_ROOT, "research_safety", "batches")
        stamp = clock().replace("-", "").replace(":", "")
        text = json.dumps(report, indent=2, sort_keys=True, default=str)
        _atomic_write(os.path.join(base, f"batch-{stamp}.json"), text)
        _atomic_write(os.path.join(base, "latest.json"), text)
        report["report_path"] = os.path.join(base, f"batch-{stamp}.json")
        report["latest_path"] = os.path.join(base, "latest.json")

    return report


def format_batch_human(report: Dict) -> str:
    """Compact human-readable summary table."""
    lines: List[str] = []
    lines.append(f"research-safety batch — {report['generated_at']}  "
                 f"matrix={report['matrix']}  assets={','.join(report['assets'])}  "
                 f"cost={report['cost_bps']}bps slip={report['slippage_bps']}bps")
    hdr = (f"{'strategy':<8} {'status':<15} {'sharpe':>8} {'return':>9} {'maxDD':>7} "
           f"{'trades':>6} {'DSR':>6} {'control':<15} {'promo':<8} reasons")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    def _f(x, w, p=4):
        return (f"{x:>{w}.{p}f}" if isinstance(x, (int, float)) else f"{'—':>{w}}")

    for r in report["strategies"]:
        reasons = ",".join(r["failure_reasons"][:4]) + (
            "…" if len(r["failure_reasons"]) > 4 else "")
        lines.append(
            f"{r['strategy_id']:<8} {str(r['status']):<15} "
            f"{_f(r['oos_sharpe'], 8)} {_f(r['total_return'], 9)} "
            f"{_f(r['max_drawdown'], 7, 3)} {int(r['num_trades'] or 0):>6} "
            f"{_f(r['dsr'], 6, 3)} {str(r['control_verdict'] or '—'):<15} "
            f"{str(r['promotion_decision'] or '—'):<8} {reasons}")
    s = report["summary"]
    lines.append("-" * len(hdr))
    lines.append(f"{s['n_strategies']} strategies · {s['n_valid']} valid · "
                 f"{s['n_technical_failures']} technical failures · "
                 f"promote {s['n_promote']} / hold {s['n_hold']} / reject {s['n_reject']}")
    lg = report["ledger"]
    lines.append(f"ledger: {lg['total_trials']} trials · {lg['dsr_trials']} count toward DSR · "
                 f"{lg['excluded_trials']} excluded")
    if report.get("latest_path"):
        lines.append(f"report: {report['latest_path']}")
    lines.append("Research only — no forward test started, no broker, no orders.")
    return "\n".join(lines)
