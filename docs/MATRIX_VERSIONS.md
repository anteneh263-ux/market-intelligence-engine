# Frozen-Matrix Version Log

The evaluation matrix is content-addressed. Each version is immutable and hashed; results are
tagged with the version that produced them. **Older versions are never edited or re-run over.**

| version | hash | multi-asset pooling | status | used for |
|---|---|---|---|---|
| **srp-matrix-v1** | `a740f3acbf6572d6` | **concatenation** — stitch all assets' OOS returns into one series | frozen / historical | first four reference strategies (H-002/005/010/020), v1 results kept as history |
| **srp-matrix-v2** | `4c2aa61eccfd276c` | **per-asset-then-aggregate** — metrics per asset, then equal-weight; drawdown gate uses the **mean per-asset OOS drawdown** | current | calibration re-run of the four; all future strategies (H-003…H-024) |

**Only difference v1 → v2:** the multi-asset headline aggregation. Everything else — walk-forward
splits, cost/slippage ladder, benchmarks, statistics, robustness gates, regime labelling — is
byte-identical (the `version` string is the only changed field, so v1's hash is provably unchanged;
`pooling` is a derived property, not a hashed field).

**Why the change:** in v1 the multi-asset "pooled" curve concatenated heterogeneous assets, so its
max drawdown was an artifact (a strategy could be Graveyarded by the *concatenation*, not by real
per-asset risk). v2 removes that artifact by aggregating per-asset.

---

## v1 → v2 calibration (four reference strategies, 5 assets)

| strategy | v1 verdict | v1 maxDD (concat) | v2 verdict | v2 maxDD (mean per-asset) | change |
|---|---|---|---|---|---|
| **H-010 MACD** | Graveyard | 97.4% | **Not Supported** | 55.7% | artifact removed → honest "Not Supported" |
| **H-005 ChannelBreakOut** | Graveyard | ~85% | **Not Supported** | 53.9% | artifact removed → honest "Not Supported" |
| **H-020 RSI** | Graveyard | 100% | **Graveyard** | 66.2% | still Graveyard, now for a *real* per-asset DD |
| **H-002 BarUpDn** | Graveyard | ~80% | **Graveyard** | 84.3% | still Graveyard — the null genuinely blows up |

**Interpretation.** v2 reclassified the two momentum rules (MACD, breakout) from *artifact-Graveyard*
to the more accurate **Not Supported** — they have no robust edge, but they are not catastrophic once
drawdown is measured per asset. RSI and the BarUpDn null remain **Graveyard** for genuine reasons
(their per-asset drawdowns really do exceed the 60% gate). **No strategy became Supported** — the
scientific conclusion (all four fragile) is unchanged; only the *reason* for two of them is now
correct. This is exactly what a methodology fix should do: change wrong labels, not manufacture edges.

Per-asset and per-regime generalization findings (unchanged, method-independent) live in
`CROSS_ASSET_GENERALIZATION.md`. Those numbers came from per-asset walk-forward and are identical
under v1 and v2 — the version only affects how the *headline verdict* aggregates them.

---

## Provenance

- v1 results archived at `knowledge_base/results/H-0xx.srp-matrix-v1.json`.
- v2 results at `knowledge_base/results/H-0xx.srp-matrix-v2.json`; `H-0xx.json` points to the latest.
- Each dossier keeps its **v1 `## STEP 5`** section as history and adds a
  **`## STEP 5 · srp-matrix-v2`** section — v1 is never overwritten.
