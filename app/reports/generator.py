"""Research Report Engine — write a structured markdown report per backtest.

Sections: Observation, Hypothesis, Evidence, Result, Weaknesses,
Counterarguments, Next experiment.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import DISCLAIMER, settings
from app.database.models import Backtest, Hypothesis, Report


def _pf(v):
    return "∞" if v is None else f"{v:.2f}"


def _verdict(bt: Backtest) -> str:
    if bt.rejected:
        return "REJECTED → Strategy Graveyard"
    return "RETAINED (passes robustness gates)"


def build_markdown(h: Hypothesis, bt: Backtest) -> str:
    reasons = bt.reject_reasons or []
    reasons_md = "\n".join(f"- {r}" for r in reasons) if reasons else "- None — all robustness gates passed."

    return f"""# Research Report — {h.name}

> ⚠️ {DISCLAIMER}

**Asset:** {h.asset}  |  **Timeframe:** {h.timeframe}  |  **Direction:** {h.direction}
**Verdict:** {_verdict(bt)}

## Observation
While studying {h.asset} on the {h.timeframe} timeframe, we isolated a recurring
market condition worth interrogating. Markets rarely repeat exactly, but
structural patterns (trend context, momentum, volatility regime) tend to recur,
and each is a chance to falsify a naive intuition.

## Hypothesis
{h.logic}

Formally, a **{h.direction}** position is opened when every entry condition below
holds, then closed after a fixed holding period:

```json
{h.conditions}
```

## Evidence
The rule was evaluated bar-by-bar across the stored candle history with the
following risk assumptions: fixed position size ({settings.position_size}× equity,
no leverage), a {settings.fee_pct*100:.1f}% fee per side, non-overlapping trades,
and no real execution. This produced **{bt.num_trades}** independent trades.

## Result
| Metric | Value |
|---|---|
| Total return | {bt.total_return*100:.2f}% |
| Win rate | {bt.win_rate*100:.1f}% |
| Profit factor | {_pf(bt.profit_factor)} |
| Max drawdown | {bt.max_drawdown*100:.1f}% |
| Sharpe (trade-level) | {bt.sharpe:.2f} |
| Trades | {bt.num_trades} |
| Avg win | {bt.avg_win*100:.2f}% |
| Avg loss | {bt.avg_loss*100:.2f}% |

## Weaknesses
- Fixed holding period ignores adaptive exits (stops / targets), so it may exit
  strong trends early or hold losers too long.
- No slippage or order-book impact is modelled; fills assume the close price.
- Sharpe is computed at the trade level, not annualised, so cross-strategy
  comparison is only valid within this engine.
- Sample may be regime-dependent; a single history is not out-of-sample proof.

## Counterarguments
- A profitable result could be curve-fit to this particular window; the same
  edge might vanish out-of-sample or after transaction costs rise.
- Even a "retained" strategy with a decent Sharpe may fail live due to latency,
  liquidity, and behavioural slippage the backtest cannot see.
- Robustness gate failures below are reasons to distrust the result:
{reasons_md}

## Next experiment
- Re-run on a held-out time window (walk-forward) to check stability.
- Add an ATR-based stop and target and compare against the fixed-hold exit.
- Sweep the holding period and entry thresholds to map the edge's sensitivity.
- Test the mirror condition on the opposite asset to check for a common driver.

---
*Generated automatically by the Market Intelligence Engine research pipeline.*
"""


def generate_report(db: Session, h: Hypothesis, bt: Backtest) -> Report:
    md = build_markdown(h, bt)
    title = f"{h.name} — {_verdict(bt)}"
    rep = Report(
        backtest_id=bt.id,
        hypothesis_id=h.id,
        title=title,
        markdown=md,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return rep
