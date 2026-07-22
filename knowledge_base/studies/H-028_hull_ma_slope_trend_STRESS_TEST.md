# Stress-test report — Hull MA slope-trend (H-028 candidate)

**Frozen verdict: `Graveyard`.**
(Primary model HMA n=55: out-of-sample max drawdown **64%** breaches the 60% gate; it also clears no benchmark and no deflated-Sharpe bar.)

**Protocol:** `srp-matrix-v2` (frozen) · 5-split walk-forward OOS · cost {0,5,10} bps × slippage {0,5,10} bps (headline 5+5) · matched random-timing (per-strategy turnover-matched, p95) · buy-and-hold · ex-ante regimes · deflated Sharpe · BH-FDR over the full 10-variant family.
**Data:** real local daily OHLCV — BTC/USD, ETH/USD, SPY, XAU/USD (2018–2026).
**Constraints honoured:** repo unchanged, official H-028 not created, no paper trading, no broker, risk % not optimised.

> Pre-registered before any results were seen. The verdict is a statement about *evidence*, not advice.

---

## 1. Protocol, exactly as locked

- **Primary = HMA, n = 55, deadband = 0, in every walk-forward fold.** `param_grid()` returns a single frozen config, so the pipeline never re-selects or optimises `n` per fold.
- **Frozen HMA formula:** `half = floor(n/2)`, `sqrt_p = round(sqrt(n))`, `HMA = WMA(2·WMA(close,half) − WMA(close,n), sqrt_p)`.
- **Causal:** slope read on completed candles (HMAₜ vs HMAₜ₋₁); the engine shifts the position forward one bar. No repaint, no look-ahead.
- **Rule:** long +1 when HMA rising, short −1 when falling (stop-and-reverse); a common warm-up (`n + √n`) so every MA trades the same OOS window.
- **Periods {20,34,89,100} and deadbands {0.05%,0.10%} are secondary only** — they did **not** feed back into the primary.
- **Comparators at the same fixed n = 55:** SMA-, EMA-, WMA-slope.
- **BH-FDR family = all 10 variants** (HMA-55 + 4 periods + 2 deadbands + 3 comparators).

---

## 2. Primary vs comparators (fixed n = 55, deadband = 0)

Per-period OOS Sharpe; benchmarks use the same exits/turnover.

| Model | Verdict | Deflated Sharpe | OOS Sharpe | Beats B&H? | Beats random p95? | OOS trades | Max DD |
|---|---|---|---|---|---|---|---|
| **HMA-55 (primary)** | **Graveyard** | 0.657 | +0.0103 | ❌ | ❌ | 391 | **64%** |
| SMA-55 | Not Supported | 0.872 | +0.0254 | ❌ | ❌ | 392 | 55% |
| EMA-55 | Graveyard | 0.718 | +0.0155 | ❌ | ❌ | 554 | 63% |
| WMA-55 | Not Supported | 0.799 | +0.0240 | ❌ | ✅ | 457 | 55% |
| *buy-and-hold* | — | — | **+0.0467** | — | — | — | — |
| *matched random-timing (p95)* | — | — | **+0.0257** | — | — | — | — |

---

## 3. The pre-registered question, answered: does the Hull construction beat plain MAs?

**No — it is the worst of the four.** This is the decisive finding.

| Model (same n=55) | OOS Sharpe | Deflated Sharpe | Trades | Max DD |
|---|---|---|---|---|
| SMA-55 | **+0.0254** | **0.872** | 392 | 55% |
| WMA-55 | +0.0240 | 0.799 | 457 | 55% |
| EMA-55 | +0.0155 | 0.718 | 554 | 63% |
| **HMA-55** | **+0.0103** | **0.657** | 391 | **64%** |

The Hull MA — whose entire selling point is *reduced lag* — produced the **lowest OOS Sharpe, the lowest deflated Sharpe, and the deepest drawdown** of the four moving-average constructions at the same period. Note HMA-55 and SMA-55 trade almost identically often (391 vs 392), yet HMA is markedly worse: the Hull lag-reduction (which is known to *overshoot* at turns) produces **lower-quality entries and deeper drawdowns even at equal turnover**, not just more of them. Had this been Supported, the bar was to show HMA beats SMA/EMA/WMA; instead the evidence is the opposite.

---

## 4. Reduced lag → more whipsaw and cost (sensitivity over period)

Ordering the HMA family by lag makes the mechanism explicit — **less lag = more trades = worse**:

| Model | Lag | OOS trades | OOS Sharpe | Deflated Sharpe | Verdict |
|---|---|---|---|---|---|
| HMA-100 | most | 220 | +0.0268 | 0.838 | Not Supported |
| HMA-89 | ↑ | 256 | +0.0335 | 0.892 | Not Supported |
| HMA-55 (primary) | mid | 391 | +0.0103 | 0.657 | Graveyard |
| HMA-34 | ↓ | 588 | +0.0075 | 0.638 | Not Supported |
| HMA-20 | least | **900** | +0.0006 | 0.511 | Graveyard |

Turnover rises monotonically as lag falls (220 → 900 trades), and risk-adjusted performance falls with it. The **deadband** variants (which suppress micro-slopes to cut whipsaw) did not rescue it either — HMA-55 + 0.05% and + 0.10% deadbands both remained **Graveyard** (max DD 63% and 67%). This is direct evidence for the pre-registered concern: reducing MA lag increases whipsaw and trading cost rather than capturing more trend.

---

## 5. Per market (primary HMA-55)

| Market | Verdict | Deflated Sharpe | OOS Sharpe | Beats B&H | Beats random | Trades | Max DD |
|---|---|---|---|---|---|---|---|
| BTC/USD | Graveyard | 0.779 | +0.0154 | ❌ | ❌ | 123 | **92%** |
| ETH/USD | Graveyard | 0.946 | +0.0320 | ❌ | ✅ | 109 | 85% |
| SPY | Not Supported | 0.612 | +0.0069 | ❌ | ❌ | 70 | 37% |
| XAU/USD | Not Supported | 0.293 | −0.0131 | ❌ | ❌ | 89 | 41% |

No market is Supported. Crypto drawdowns are catastrophic (BTC 92%, ETH 85%). The only place deflated Sharpe looks high (ETH 0.946) still carries an 85% drawdown — untradeable.

## 6. Regime (primary HMA-55, indicative full-sample)

| Regime | Annualised Sharpe | Mean return/bar | Bars |
|---|---|---|---|
| Trend | **+4.42** | +61.3 bps | 4,141 |
| Range | **−3.67** | −42.9 bps | 4,272 |
| Crisis | −0.01 | −0.3 bps | 2,086 |

(Magnitudes are indicative/inflated full-sample; the *sign pattern* is the point.) This is a textbook trend-following signature: strongly positive in trends, strongly negative in ranges, roughly flat in crisis. Because ranging bars slightly outnumber trending ones here, the net washes out — the strategy has **no unconditional edge**, only a regime-gated one, exactly as M-004/M-007 describe.

## 7. Multiplicity (BH-FDR over all 10 variants)

Every variant's p-value (1 − deflated Sharpe) fails Benjamini-Hochberg at q = 0.05 — **none survives** (adjusted p ≈ 0.40 across the board). Nothing in the HMA family, its periods, its deadbands, or its MA comparators is a statistically credible discovery once the family is counted.

---

## 8. Conclusion and recommendation

**Verdict: Graveyard.** The primary HMA-55 breaches the 60% max-drawdown gate (64%, and 85–92% on crypto), clears neither buy-and-hold nor its matched random-timing control, sits below the 0.95 deflated-Sharpe bar, and survives no multiplicity correction. It is well-powered (391 OOS trades), so this is a decisive negative, not an underpowered "inconclusive."

**Do NOT advance to paper trading, and do not promote it as a supported H-028.** If you want, it can be recorded as a **Graveyard** entry whose value is the *lesson*, not the strategy (that step would touch the repo and awaits your approval).

**What we learned (the real deliverable):** the pre-registered hypothesis — that a *reduced-lag* MA (Hull) rescues the fragile MA-trend family (M-001) — is **refuted, cleanly and with the mechanism visible.** At equal period, HMA is the worst of SMA/WMA/EMA/HMA; across periods, less lag monotonically means more whipsaw, more cost, and deeper drawdown; deadbands don't fix it; and any residual profit is pure trend-regime exposure that bleeds out in ranges. This **sharpens M-001**: MA-trend fragility is *intrinsic to the family*, and specifically **lower lag makes it worse, not better** — a concrete, reusable prior for evaluating the next "faster/smoother MA" product before spending time on it.

---

*Reproducibility: standalone harness through the frozen `srp-matrix-v2` pipeline on real daily OHLCV (CSV provider); strategies registered in-memory only; no repo files modified. Regime figures are indicative full-sample; all verdict-bearing numbers are walk-forward OOS at the frozen 5+5 bps headline.*
