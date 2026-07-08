# Cross-Asset Generalization Study — Four Reference Strategies, Five Markets

**date:** 2026-07-08 · **engine:** `app.strategy_research` · **matrix:** `srp-matrix-v1` (`a740f3acbf6572d6`)
**assets:** AAPL (equity), BTC/USD (crypto), ETH/USD (crypto), SPY (US index), XAU/USD (gold, GC=F)
**data:** real daily OHLCV 2018-01 → 2026-07 (Yahoo); equities/gold ≈2,138 bars, crypto ≈3,111 bars
**strategies:** H-010 MACD, H-020 RSI, H-005 ChannelBreakOut, H-002 BarUpDn

> ⚠️ Research only. No execution, no signals, no advice. Every number is a statement about *evidence*.

Every asset passed through the **identical** frozen pipeline: same 5-split walk-forward, same
{0,5,10} bps cost ladder, same {0,5,10} bps slippage, same buy-and-hold + matched-random-timing
benchmarks, same deflated-Sharpe / BH-FDR / bootstrap statistics, same robustness gates. Nothing was
tuned per asset. Data is reproducible from `backend/cross_asset_results.json`.

---

## 1. Per-asset out-of-sample Sharpe (annualised, headline 5+5 bps)

| Strategy | AAPL | BTC/USD | ETH/USD | SPY | XAU/USD | assets positive | generalizes? |
|---|---|---|---|---|---|---|---|
| **H-005 ChannelBreakOut** | +0.67 | +0.41 | +0.45 | +0.01 | +0.01 | **5/5** | direction yes, magnitude weak |
| **H-010 MACD** | +0.27 | +0.17 | +0.30 | −0.39 | −0.86 | 3/5 | partial (trendy assets only) |
| **H-020 RSI** | −0.57 | −0.47 | +0.07 | −0.42 | −1.18 | 1/5 | no |
| **H-002 BarUpDn** | −1.60 | −0.97 | −0.88 | −2.14 | −2.25 | 0/5 | no (confirmed null) |

## 2. Per-regime out-of-sample Sharpe (annualised, pooled across all five assets)

Regimes are labelled **ex-ante from price only** (never from outcome), identically for every asset.

| Strategy | trend | range | crisis | regime signature |
|---|---|---|---|---|
| **H-010 MACD** | **+1.79** | −1.37 | −0.35 | momentum: wins in trends, bleeds in ranges |
| **H-005 ChannelBreakOut** | **+3.88** | −3.16 | +0.02 | momentum (extreme): huge in trends, ruinous in ranges |
| **H-020 RSI** | −1.03 | **+0.38** | −0.13 | mean-reversion: the *mirror* — wins in ranges, run over in trends |
| **H-002 BarUpDn** | +0.02 | −1.45 | −2.29 | none: negative everywhere |

## 3. Verdicts (multi-asset pooled) and a methodological caveat

Under the frozen pipeline the pooled multi-asset verdict for all four is **Graveyard** — the pooled
out-of-sample equity breaches the 60% max-drawdown gate. **Important honesty note:** the "pooled"
curve concatenates five heterogeneous assets' returns; its drawdown is *not* an economic portfolio
drawdown, so the pooled Graveyard is dominated by that concatenation artifact. The **per-asset
results in §1–§2 are the primary generalization evidence**, and they are unambiguous: even H-005,
the best generalizer, posts only a weak positive Sharpe, a deflated Sharpe of 0.72 (< the 0.95 bar),
and crypto per-asset drawdowns that Graveyard it individually. **No strategy is Supported on any
single asset.** The cross-asset conclusion is therefore robust to the aggregation choice: all four
are fragile. (Change-control candidate for a future version: replace concatenation-pooling with an
equal-weight portfolio or per-asset-then-aggregate verdict; not altered here, to honour
pre-registration.)

---

## 4. The four questions, answered

**Which strategies generalize across markets?** Only **H-005 ChannelBreakOut** is directionally
positive on all five assets — a real, if weak, cross-market signal. It is the strongest of the four
(highest deflated Sharpe, 0.72), consistent with the trend-following literature (Moskowitz–Ooi–Pedersen,
Hurst–Ooi–Pedersen, Szakmary). But it still fails the evidential bar: low magnitude, doesn't beat
buy-and-hold/random-timing, and its drawdowns are severe. **Directional generalization ≠ a tradable,
robust edge.**

**Which are asset-specific?** **H-010 MACD** works on the *trendy* assets (AAPL, BTC, ETH) and fails
on the mean-reverting/choppy ones (SPY, gold) — it is conditionally asset-specific, keyed to how
trending the asset is. **H-020 RSI** is weakly asset-specific (marginally positive only on ETH) and
essentially has no cross-market edge.

**Which are regime-dependent?** **All of them, strongly, and in the exact directions theory
predicts.** The momentum rules (MACD, ChannelBreakOut) are massively positive in trends and massively
negative in ranges; the mean-reversion rule (RSI) is the precise mirror image (positive in ranges,
negative in trends). H-002 is negative in every regime. Regime dependence is the single largest
effect in the whole study — far larger than any cross-asset difference.

**What patterns emerge across asset classes?** The dominant axis is **momentum vs mean-reversion ×
market regime**, not asset class per se. A strategy's asset ranking is really a proxy for *how
trending that asset was* over 2018–2026 (crypto most trending → best for momentum rules; gold/SPY
choppier → worst). Two clean regularities:
- **Trend-followers (MACD, breakout) and the mean-reverter (RSI) have opposite, near-symmetric
  regime signatures.** Neither is unconditionally robust; each has a conditional (regime-gated) edge.
- **The naive price-action rule (H-002) is a confirmed null across all five markets and all three
  regimes** — exactly the calibration anchor it was designed to be. The platform did not manufacture
  a single false positive.

Note a refinement to prior expectation: the breakout edge concentrated in **trends**, *not*
specifically in **crises** (crisis Sharpe ≈ 0). On these five assets the "crisis-alpha" story for
breakouts did not appear; the edge is trend-conditional.

---

## 5. Meta-hypothesis updates (evidence-driven)

| meta | statement | update from this study |
|---|---|---|
| **M-001** lagging trend/crossover low OOS robustness | **Supported** — H-010 fragile overall (3/5, DSR 0.01), but *refined*: the weakness is regime-gated (strong +1.79 in trends), not uniform. |
| **M-002** price-action momentum doesn't survive costs/turnover | **Strongly supported** — H-002 negative on 5/5 assets and in every regime. |
| **M-003** mean-reversion oscillator/band rules are regime-dependent | **Directly confirmed** — H-020 posts +0.38 range vs −1.03 trend; textbook regime dependence. |
| **M-004** breakout/volatility edge concentrates in high-vol episodes | **Refined / partly contradicted** — H-005's edge concentrates in **trends** (+3.88), not crises (≈0). Reword M-004 to "concentrates in **trending** regimes." |
| **M-007 (NEW)** momentum and mean-reversion rules have opposite, near-symmetric regime signatures; neither is unconditionally robust, but each carries a conditional regime-gated edge | **Seeded & supported** by H-010/H-005 (trend-positive) vs H-020 (range-positive). To be tested as more strategies enter the corpus. |

---

## 6. Recommendation on scaling to the remaining 19

The methodology is behaving exactly as a sound scientific instrument should: it produces honest
negatives, reproduces literature-predicted regime signatures, and its calibration null (H-002) is
dead on arrival everywhere. **The templates and pipeline are scientifically sound and ready to
scale** — with one queued, pre-registered improvement (portfolio-based rather than concatenation
pooling for the multi-asset drawdown gate) to be applied as a versioned change *before* the next
batch, not retrofitted to these results. Recommended next step: run the same five-asset pipeline
across H-003…H-024, then let M-001…M-007 be decided by the full corpus.

---
*The accumulated knowledge — which rules are momentum, which are reversion, and which regime each
needs — not any individual strategy, is the asset.*
