# Discovery Report — Generated Hypotheses

**policy:** discovery-v1 (`627414bcef51`) · **budget:** {'generated': 5, 'tested': 5} (generated / tested) · **survivors:** 0

> ⚠️ Research only. Auto-generated hypotheses, recorded separately from the pre-registered TradingView universe. Grounded, frozen, and auditable.

## Survival by round (is the generator improving?)

| round | generated | tested | survived | survival rate | best DSR |
|---|---|---|---|---|---|
| 1 | 5 | 5 | 0 | 0.00 | 0.645 |

## Top generated hypotheses (by deflated Sharpe)

| id | recipe | verdict | DSR | priority | why proposed |
|---|---|---|---|---|---|
| G-0002 | `FILTER(donchian|adx_gate)` | Graveyard | 0.645 | 0.414 | family 'breakout' (survival 0.00, meanDSR 0.41), turnover 'low' (survival 0.50); +regime-gate → priority 0.414 |
| G-0005 | `FILTER(supertrend|price_ma)` | Graveyard | 0.529 | 0.373 | family 'trend' (survival 0.00, meanDSR 0.44), turnover 'low' (survival 0.50) → priority 0.373 |
| G-0004 | `VOTE(donchian+supertrend)` | Graveyard | 0.509 | 0.403 | family 'trend' (survival 0.00, meanDSR 0.44), turnover 'low' (survival 0.50); +multi-block → priority 0.403 |
| G-0001 | `FILTER(supertrend|adx_gate)` | Graveyard | 0.473 | 0.423 | family 'trend' (survival 0.00, meanDSR 0.44), turnover 'low' (survival 0.50); +regime-gate → priority 0.423 |
| G-0003 | `FILTER(atr_expand|adx_gate)` | Graveyard | 0.357 | 0.408 | family 'volatility' (survival 0.50, meanDSR 0.05), turnover 'high' (survival 0.00); +regime-gate → priority 0.408 |

_Every hypothesis traces to its recipe hash and the `discovery-v1` policy that proposed it; see `discovery/ledger.json` and `discovery/results/`._