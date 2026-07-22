"""Target-only control comparison.

A candidate is compared to a deliberately simple control that uses ONLY the target
asset's own price (no external signals), on the SAME asset(s) and period. This shows
whether the candidate adds value over trivial price-only exposure. It is a CONTROL
COMPARISON, not a causal claim.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from app.features.indicators import ema
from app.strategy_research import EvalRequest, MATRICES, evaluate
from app.strategy_research.strategies.base import Strategy

_CONTROLS_REGISTERED = False


class TargetOnlyControl(Strategy):
    """Target-only control: a slow price-vs-EMA trend on the target asset's OWN close.
    Uses no external signals - the minimal price-only baseline a candidate must beat."""

    strategy_id = "CTRL-TARGET-ONLY"
    name = "Target-only momentum control"
    strategy_class = "control"
    direction = "long_short"
    default_params = {"length": 50}
    declared_inputs = {"close"}

    def positions(self, df: pd.DataFrame, params: Optional[Dict] = None) -> pd.Series:
        p = {**self.default_params, **(params or {})}
        n = int(p["length"])
        m = ema(df["close"], n).to_numpy()
        c = df["close"].to_numpy()
        pos = np.where(c > m, 1.0, np.where(c < m, -1.0, 0.0))
        pos[np.isnan(m)] = 0.0
        return pd.Series(pos, index=df.index)

    def param_grid(self) -> List[Dict]:
        return [dict(self.default_params)]


def ensure_controls_registered() -> None:
    """Register the built-in control strategy on first use (no import side effects,
    and its non-'H-' id never affects the H-strategy corpus count)."""
    global _CONTROLS_REGISTERED
    if _CONTROLS_REGISTERED:
        return
    from app.strategy_research.strategies import register_strategy
    register_strategy(TargetOnlyControl())
    _CONTROLS_REGISTERED = True


def _headline_total_return(rep, cost: float, slip: float) -> Optional[float]:
    rows = [r for r in rep.condition_rows
            if r.get("cost_bps") == cost and r.get("slippage_bps") == slip]
    return float(np.mean([r["total_return"] for r in rows])) if rows else None


def compare(candidate_id: str, control_id: str, assets: Sequence[str],
            timeframe: str = "1d", matrix: str = "srp-matrix-v2", provider: str = "csv",
            provider_kwargs: Optional[Dict] = None, sharpe_tol: float = 0.05) -> Dict:
    """Compare a candidate to a control on the SAME asset(s)/period.

    verdict: candidate_wins | control_wins | inconclusive (|dSharpe| <= sharpe_tol).
    This is a control comparison and makes NO causal claim."""
    ensure_controls_registered()
    m = MATRICES.get(matrix, MATRICES["v2"])
    pk = provider_kwargs or {}

    def run(sid: str):
        return evaluate(EvalRequest(strategy_id=sid, provider=provider, assets=list(assets),
                        timeframe=timeframe, matrix_version=m.version), m, pk)

    cand, ctrl = run(candidate_id), run(control_id)
    ch, th = cand.headline, ctrl.headline
    c_ret = _headline_total_return(cand, m.headline_cost_bps, m.headline_slippage_bps)
    t_ret = _headline_total_return(ctrl, m.headline_cost_bps, m.headline_slippage_bps)

    d_sharpe = float((ch.get("oos_sharpe") or 0.0) - (th.get("oos_sharpe") or 0.0))
    d_dd = float((ch.get("max_drawdown") or 0.0) - (th.get("max_drawdown") or 0.0))
    d_ret = (float(c_ret - t_ret) if (c_ret is not None and t_ret is not None) else None)

    if d_sharpe > sharpe_tol:
        verdict = "candidate_wins"
    elif d_sharpe < -sharpe_tol:
        verdict = "control_wins"
    else:
        verdict = "inconclusive"

    return {
        "kind": "target_control_comparison",
        "note": "Control comparison only - NOT a causal claim.",
        "candidate_id": candidate_id, "control_id": control_id,
        "assets": list(assets), "timeframe": timeframe, "matrix": m.version,
        "candidate": {"oos_sharpe": ch.get("oos_sharpe"),
                      "oos_sharpe_annual": ch.get("oos_sharpe_annual"),
                      "total_return": c_ret, "max_drawdown": ch.get("max_drawdown"),
                      "num_trades": ch.get("num_trades"), "verdict": cand.verdict},
        "control": {"oos_sharpe": th.get("oos_sharpe"),
                    "oos_sharpe_annual": th.get("oos_sharpe_annual"),
                    "total_return": t_ret, "max_drawdown": th.get("max_drawdown"),
                    "num_trades": th.get("num_trades"), "verdict": ctrl.verdict},
        "delta": {"sharpe": round(d_sharpe, 6),
                  "total_return": (round(d_ret, 6) if d_ret is not None else None),
                  "max_drawdown": round(d_dd, 6)},
        "sharpe_tolerance": sharpe_tol,
        "verdict": verdict,
    }
