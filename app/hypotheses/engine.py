"""Hypothesis Engine — define testable market hypotheses.

A hypothesis is a machine-readable rule spec plus human-readable metadata.
Conditions are evaluated by the backtest engine against the feature DataFrame.

Condition grammar (each condition is a dict):
    {"left": <feature>, "op": <operator>, "right": <feature|number>}

Operators:
    ">", "<", ">=", "<="        elementwise comparison
    "cross_above", "cross_below" left crosses right between prior and current bar

Entry fires when ALL entry conditions are true on a bar.
Exit is a fixed holding period (in candles) — simple, transparent, and
avoids look-ahead. `direction` is long or short.
"""
from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from app.config import ASSETS, TIMEFRAMES
from app.database.models import Hypothesis


def hypothesis_templates() -> List[dict]:
    """The core research hypotheses required by the spec."""
    return [
        {
            "name": "Oversold reversal above trend",
            "logic": "If RSI < 30 while price is above the EMA200 (long-term uptrend), "
                     "test a long: mean-reversion bounces are more reliable with the trend.",
            "direction": "long",
            "hold": 12,
            "conditions": [
                {"left": "rsi", "op": "<", "right": 30},
                {"left": "close", "op": ">", "right": "ema200"},
            ],
        },
        {
            "name": "Overbought fade below trend",
            "logic": "If RSI > 70 while price is below the EMA200 (long-term downtrend), "
                     "test a short: overbought rallies tend to fail in a downtrend.",
            "direction": "short",
            "hold": 12,
            "conditions": [
                {"left": "rsi", "op": ">", "right": 70},
                {"left": "close", "op": "<", "right": "ema200"},
            ],
        },
        {
            "name": "Volume-spike EMA50 breakout",
            "logic": "If volume spikes (>50% jump) as price breaks above the EMA50, "
                     "test momentum continuation on the long side.",
            "direction": "long",
            "hold": 6,
            "conditions": [
                {"left": "volume_change", "op": ">", "right": 0.5},
                {"left": "close", "op": "cross_above", "right": "ema50"},
            ],
        },
        {
            "name": "Volatility-expansion breakout",
            "logic": "If volatility expands sharply above its recent average after a "
                     "quiet regime, test breakout continuation in the direction of the move.",
            "direction": "long",
            "hold": 6,
            "conditions": [
                {"left": "volatility", "op": ">", "right": "vol_ma"},
                {"left": "ret_1", "op": ">", "right": 0},
            ],
        },
    ]


def generate_default_hypotheses(db: Session) -> List[Hypothesis]:
    """Create the full grid of template hypotheses across assets/timeframes."""
    created: List[Hypothesis] = []
    for tpl in hypothesis_templates():
        for asset in ASSETS:
            for tf in TIMEFRAMES:
                h = Hypothesis(
                    name=f"{tpl['name']} · {asset} {tf}",
                    logic=tpl["logic"],
                    conditions={"entry": tpl["conditions"], "hold": tpl["hold"]},
                    direction=tpl["direction"],
                    timeframe=tf,
                    asset=asset,
                )
                db.add(h)
                created.append(h)
    db.commit()
    for h in created:
        db.refresh(h)
    return created


def create_hypothesis(db: Session, *, name: str, logic: str, conditions: dict,
                      direction: str, timeframe: str, asset: str) -> Hypothesis:
    h = Hypothesis(
        name=name, logic=logic, conditions=conditions,
        direction=direction, timeframe=timeframe, asset=asset,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h
