"""Mechanical verdict — no discretion.

Given the frozen matrix output, one of four labels is returned by fixed rules:

  Graveyard      failed a hard robustness gate (too few trades, or catastrophic
                 OOS drawdown). Cause of death recorded.
  Inconclusive   underpowered: not enough OOS data/trades to decide.
  Supported      OOS deflated Sharpe clears the bar AND beats buy-and-hold and
                 matched random-timing on risk-adjusted terms AND is robust across
                 a majority of conditions.
  Not Supported  everything else (the honest default).

The verdict is a statement about EVIDENCE, never a recommendation to trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .frozen import FrozenMatrix


@dataclass
class Verdict:
    label: str
    reasons: List[str]
    cause_of_death: str | None = None


def decide(summary: Dict, matrix: FrozenMatrix) -> Verdict:
    reasons: List[str] = []
    headline = summary["headline"]
    n_trades = headline["num_trades"]
    n_bars = headline["n_bars"]
    oos_sharpe = headline["oos_sharpe"]
    dsr = headline["dsr"]
    bh = headline["oos_sharpe"]  # placeholder guard; real BH handled in pipeline
    beats_bh = headline["beats_buy_hold"]
    beats_rand = headline["beats_random"]
    robustness = summary["robustness_score"]

    # 1) Hard gates -> Graveyard.
    if headline["max_drawdown"] > matrix.max_drawdown_gate:
        return Verdict(
            "Graveyard",
            [f"OOS max drawdown {headline['max_drawdown']*100:.1f}% exceeds gate "
             f"{matrix.max_drawdown_gate*100:.0f}%"],
            cause_of_death="catastrophic drawdown out-of-sample",
        )

    # 2) Underpowered -> Inconclusive.
    if n_trades < matrix.min_trades or n_bars < 200:
        return Verdict(
            "Inconclusive",
            [f"Underpowered: {n_trades} OOS trades (< {matrix.min_trades}) / "
             f"{n_bars} OOS bars"],
        )

    # 3) Positive case -> Supported (all must hold).
    conds = {
        "deflated Sharpe clears bar": dsr >= matrix.dsr_threshold,
        "OOS Sharpe positive": oos_sharpe > 0,
        "beats buy-and-hold (risk-adjusted)": beats_bh,
        "beats matched random-timing": beats_rand,
        "robust across majority of conditions": robustness >= 0.5,
    }
    if all(conds.values()):
        return Verdict("Supported", [f"met: {k}" for k in conds])

    for k, ok in conds.items():
        if not ok:
            reasons.append(f"failed: {k}")
    return Verdict("Not Supported", reasons)
