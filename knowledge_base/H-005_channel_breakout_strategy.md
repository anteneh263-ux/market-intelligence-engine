# Strategy Dossier — H-005: ChannelBreakOutStrategy

**strategy_id:** H-005 · **source:** TradingView built-in ("ChannelBreakOutStrategy") · **date:** 2026-07-08
**class:** breakout · **status:** STEPs 1–4 complete; STEP 5 **pre-registered, pending real data**; verdict **Not Yet Tested**
**engine:** `app.strategy_research` · **frozen matrix:** `srp-matrix-v1` (`a740f3acbf6572d6`)

> ⚠️ Research only. No execution, no signals, no advice. A verdict is a statement about *evidence*.

---

## STEP 1 — Strategy Analysis

Canonical TradingView built-in logic — a Donchian-style channel breakout (engine spec:
`strategies/implementations.py::ChannelBreakOutStrategy`):

- **Indicators:** `upBound = highest(high, length)`, `downBound = lowest(low, length)` over the
  **prior** window (current bar excluded to avoid look-ahead).
- **Entry (long):** close breaks **above** the prior N-bar high.
- **Entry (short):** close breaks **below** the prior N-bar low.
- **Exit:** implicit; long/short, stop-and-reverse.
- **Filters:** none in the base rule.
- **Parameters (canonical default):** `length=5`.
- **Risk management / position sizing:** none; fixed full-notional. (The original built-in uses stop
  orders at the channel edge; the engine uses a close-based breakout, documented as a modelling
  choice for a close-only backtest.)
- **Implicit assumptions (the real targets of testing):** (a) breakouts precede **persistent**
  moves (time-series momentum), not immediate reversals; (b) enough trending/high-vol episodes
  exist to pay for the whipsaw in ranges; (c) fills at close with modest cost; (d) `length=5` is not
  data-snooped.
- **Script-integrity check:** channel uses a **shifted** prior window ⇒ **no look-ahead**; engine
  shifts positions forward one bar (verified).

## STEP 2 — Falsifiable Hypothesis

**H-005.** *The N-bar channel breakout produces positive risk-adjusted returns net of realistic
costs, and this edge (a) survives walk-forward out-of-sample, (b) generalizes across assets, and
(c) is not explained by the number of channel lengths tried.*

**Kill criteria (pre-committed):** **Not Supported** if OOS **deflated Sharpe < 0.95**, **or** the
edge appears in a minority of assets/conditions, **or** it fails to beat **buy-and-hold** and
**matched random-timing (95th pct)** on risk-adjusted terms. **Graveyard** if OOS max drawdown
exceeds the 60% gate. *Directional prior (STEP 3): expect any edge to be concentrated in a few
high-volatility/crisis episodes and to be turnover-sensitive.*

## STEP 3 — Research Review (evidence, not truth)

This class is the best-supported of the four — breakout systems harvest documented time-series
momentum — but the support is heavily qualified by data-snooping and cost work.

- **Moskowitz, Ooi & Pedersen (2012)** — *supports (mechanism):* persistent 1–12 month time-series
  momentum across 58 futures — the trend breakouts exploit.
- **Hurst, Ooi & Pedersen (2017)** — *supports:* a simple trend strategy earned positive
  risk-adjusted returns every decade 1880–2016, **strongest in crises** (crisis alpha).
- **Szakmary, Shen & Sharma (2010)** — *supports (OOS, net):* channel and dual-MA rules were
  significantly profitable out-of-sample across 28 commodity futures, net of costs.
- **Brock, Lakonishok & LeBaron (1992)** — *supports (in-sample):* trading-range-breakout rules had
  predictive power on the DJIA, before costs.
- **Swart (2016)** — *mixed:* Turtle Donchian-channel replication is parameter/optimization-dependent.
- **Sullivan, Timmermann & White (1999)** — *contradicts:* correcting for ~7,846 rules searched,
  the best rules' superiority weakens sharply and fails to persist OOS.
- **Bajgrowicz & Scaillet (2012)** — *contradicts (costs+FDR):* after false-discovery control and
  realistic costs, no rules show persistent profitability.

**Expected failure modes (predicted before testing):** false breakouts / **whipsaw** in ranges;
edge **concentrated in a few high-vol/crisis episodes**; **cost/turnover** sensitivity;
**parameter sensitivity** of channel length N + data-snooping; **alpha decay** post-1990s.

## STEP 4 — Experiment Design (pre-registered; frozen before any run)

Executed through the **identical** `srp-matrix-v1` pipeline (hash `a740f3acbf6572d6`):

- **Data:** real OHLCV via swappable provider; multiple assets incl. some not used to select params
  — and ideally **futures/high-vol assets**, where the literature says breakout edges live.
- **Walk-forward:** 5-split expanding-window; params re-selected in-sample, evaluated OOS, stitched.
- **Parameter grid (data-snooping budget):** `length∈{5,10,20}`.
- **Costs:** {0,5,10} bps/side. **Slippage:** {0,5,10} bps. Headline = 5+5.
- **Benchmarks:** buy-and-hold **and** matched random-timing (25 seeds; beat 95th pct).
- **Regimes:** trend/range/crisis labelled ex-ante — the **crisis** column is decisive here (the
  crisis-alpha claim is directly testable).
- **Primary metric (frozen):** OOS **deflated Sharpe** ≥ 0.95. **Secondary:** maxDD, profit factor,
  turnover, regime-conditional performance, parameter-stability, bootstrap Sharpe CI.
- **Multiplicity:** BH-FDR (q=0.05) across the 23-strategy universe.

**Registered; not alterable after seeing results.**

## STEP 5 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `5cfdb1ca19fb4e0d` · matrix `a740f3acbf6572d6` (`srp-matrix-v1`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | 0.359 |
| Deflated Sharpe (prob. edge is real) | 0.721 |
| Sharpe 95% CI (per-period) | [-0.001, 0.038] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 89.1% |
| OOS trades / bars | 396 / 10112 |
| Robustness / Stability / Generalization | 0.000 / 0.524 / 0.501 |
| Confidence | 0.271 |

**Mechanical verdict:** **Graveyard** — cause of death: catastrophic drawdown out-of-sample

Reasons: OOS max drawdown 89.1% exceeds gate 60%
## STEP 5 · srp-matrix-v2 — Scientific Evaluation

**Run:** provider `csv` (is_real=True) · request `887de68a996d9095` · matrix `4c2aa61eccfd276c` (`srp-matrix-v2`) · assets: AAPL, BTC/USD, ETH/USD, SPY, XAU/USD

| Metric (out-of-sample, headline cost+slippage) | Value |
|---|---|
| OOS Sharpe (annualised) | -0.084 |
| Deflated Sharpe (prob. edge is real) | 0.292 |
| Sharpe 95% CI (per-period) | [-0.042, 0.031] |
| Beats buy-and-hold | False |
| Beats matched random-timing (p95) | False |
| Max drawdown (OOS) | 53.9% |
| OOS trades / bars | 357 / 6000 |
| Robustness / Stability / Generalization | 0.000 / 0.428 / 0.000 |
| Confidence | 0.073 |

**Mechanical verdict:** **Not Supported**

Reasons: failed: deflated Sharpe clears bar; failed: OOS Sharpe positive; failed: beats buy-and-hold (risk-adjusted); failed: beats matched random-timing; failed: robust across majority of conditions


## STEP 6 — Knowledge-Base Record

| Field | Value |
|---|---|
| robustness_score | pending |
| stability_score | pending |
| generalization_score | pending |
| risk_adjusted | pending (deflated Sharpe / maxDD / profit factor / #trades) |
| failure_modes | *predicted:* whipsaw in ranges; crisis-concentration; cost/turnover sensitivity; length sensitivity |
| confidence | pending |
| verdict | **Not Yet Tested** |
| similar_strategies | H-018 (Price Channel), H-019 (ADX Breakout), H-024 (Volty Expan) |
| literature | Moskowitz–Ooi–Pedersen 2012, Hurst–Ooi–Pedersen 2017, Szakmary et al. 2010, BLR 1992 (support); STW 1999, Bajgrowicz–Scaillet 2012 (contradict); Swart 2016 (mixed) |
| provenance_hash | assigned at STEP 5 run |

## STEP 7 — Contribution to Learning

Bears on **M-004** (*breakout/volatility rules concentrate their edge in a few high-volatility
episodes*). The regime-conditional (crisis) OOS result is the key evidence; H-005 votes alongside
H-018, H-019, H-024. If breakout is the one class that survives, that itself is a finding about
*which* market structure rewards which rule.

**Cross-asset generalization (5 markets, 2026-07-08)** — per-asset OOS Sharpe (ann., 5+5 bps):
AAPL **+0.67**, BTC **+0.41**, ETH **+0.45**, SPY **+0.01**, XAU **+0.01** → **5/5 positive** (the
only strategy directionally positive on every market; deflated Sharpe **0.72**, highest of the four,
still < the 0.95 bar). Per-regime (pooled): trend **+3.88**, range **−3.16**, crisis **+0.02**.
**Refines M-004:** the edge concentrates in **trends**, *not* crises (crisis ≈ 0) — the crisis-alpha
story did not appear on these assets. Generalizes in *direction* but not to a robust, tradable edge.
See `CROSS_ASSET_GENERALIZATION.md`.

---

**Sources:**
- Moskowitz, Ooi & Pedersen (2012), *Time Series Momentum*, J. Financial Economics 104(2) — https://www.sciencedirect.com/science/article/pii/S0304405X11002613
- Hurst, Ooi & Pedersen (2017), *A Century of Evidence on Trend-Following Investing*, JPM/AQR — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026
- Szakmary, Shen & Sharma (2010), *Trend-following trading strategies in commodity futures: A re-examination*, JBF 34(2) — https://www.sciencedirect.com/science/article/abs/pii/S037842660900199X
- Brock, Lakonishok & LeBaron (1992), *Simple Technical Trading Rules…*, J. Finance 47(5) — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1992.tb04681.x
- Swart (2016), *Testing a price breakout strategy using Donchian Channels*, MCom thesis, UCT — https://open.uct.ac.za/handle/11427/21754
- Sullivan, Timmermann & White (1999), *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap*, J. Finance 54(5) — https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00163
- Bajgrowicz & Scaillet (2012), *Technical Trading Revisited…*, J. Financial Economics 106(3) — https://www.sciencedirect.com/science/article/abs/pii/S0304405X1200116X
