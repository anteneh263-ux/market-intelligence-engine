# Meta-Learning Report — Corpus Patterns

**strategies tested:** 26

> ⚠️ Research only. Grounded in recorded Knowledge-Base results; no advice.

## Verdict distribution

- Graveyard: 20
- Inconclusive: 1
- Not Supported: 5

## Behaviour by strategy class

| class | n | mean DSR | mean generalization | mean OOS Sharpe (ann) | verdicts |
|---|---|---|---|---|---|
| bar-pattern | 4 | 0.491 | 0.218 | -0.032 | Graveyard:4 |
| breakout | 4 | 0.408 | 0.084 | -0.012 | Graveyard:3, Not Supported:1 |
| composite | 1 | 0.654 | 0.396 | 0.200 | Graveyard:1 |
| mean-reversion-bands | 3 | 0.496 | 0.259 | 0.061 | Graveyard:3 |
| mean-reversion-oscillator | 3 | 0.277 | 0.167 | -0.263 | Graveyard:2, Not Supported:1 |
| price-action-momentum | 4 | 0.265 | 0.090 | -0.678 | Graveyard:3, Not Supported:1 |
| trend-crossover | 3 | 0.490 | 0.253 | 0.162 | Graveyard:2, Not Supported:1 |
| trend-following | 4 | 0.670 | 0.478 | 0.356 | Graveyard:2, Not Supported:1, Inconclusive:1 |

## Top generalizers (by generalization score)

- H-026 (trend-following): generalization=1.000, DSR=0.962 → Inconclusive
- H-025 (trend-following): generalization=0.914, DSR=0.972 → Not Supported
- H-009 (mean-reversion-bands): generalization=0.648, DSR=0.689 → Graveyard
- H-008 (bar-pattern): generalization=0.600, DSR=0.567 → Graveyard
- H-027 (mean-reversion-oscillator): generalization=0.500, DSR=0.555 → Not Supported
- H-013 (trend-crossover): generalization=0.438, DSR=0.526 → Graveyard
- H-023 (composite): generalization=0.396, DSR=0.654 → Graveyard
- H-011 (price-action-momentum): generalization=0.362, DSR=0.633 → Not Supported

## Meta-hypotheses (fragility claims)

| meta | tested | support | contradict | members |
|---|---|---|---|---|
| M-001 | 7 | 6 | 0 | H-010, H-012, H-013, H-015, H-022, H-025, H-026 |
| M-002 | 4 | 4 | 0 | H-002, H-006, H-007, H-011 |
| M-003 | 6 | 6 | 0 | H-003, H-004, H-009, H-020, H-021, H-027 |
| M-004 | 4 | 4 | 0 | H-005, H-018, H-019, H-024 |
| M-005 | 4 | 4 | 0 | H-008, H-014, H-016, H-017 |
| M-006 | 1 | 1 | 0 | H-023 |

## Fragility summary

- fraction fragile (Graveyard/Not Supported): 0.962
- mean OOS trades among fragile: 866.000
- mean OOS max drawdown among fragile: 0.657

_regime-dependence is tracked in CROSS_ASSET_GENERALIZATION.md; per-regime fields will fold into results as the corpus grows._
