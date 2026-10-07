# Observed historical walk-forward v1

Authorized scope: continue the remaining Phase 5 implementation. New profile
HISTORICAL_OBSERVED_WINDOW_BLOCK, synthetic integration only in this package.
No existing pilot, legacy method, matrix, schema or artifact is changed. No real
root creation, real data evaluation, holdout, promotion, PR publication or activation.
Separate reviewed PR required before release. Dedicated design review accepted
this profile subject to the requirements below.

Architecture: app.strategy_research.evaluation.observed_walkforward owns historical
window orchestration/selection. It invokes the existing versioned controlled-session
numeric execution under app.paper.session.observed_engine_v1. app.research owns
registration, window commitments, observation verification and the aggregate trial
receipt. This is a new historical profile with its own method/results, not a change
to the legacy close-to-close WF pipeline. No engine/store migration or new implicit
storage. Caller owns existing empty absolute root and v2 registry/anchor. A failure
preserves the counted reservation and partial artifacts; no retry/reconstruction.

One reservation per complete adaptive schedule before any training or OOS source
read. Config param_hash binds the selector source, initial state, candidate space
and selection rule, not parameters not yet selected. Method additionally binds
strategy source/state, complete dataset, exact ordered windows, execution/risk
profile, capital, policies and implementation sources. Every selected parameter
object must be an exact preregistered candidate. Freeze parameters and training/
purge receipts before writing OPEN_OOS and before calling the OOS reader.

Initial profile is continuous bars only, zero settlement/signal/submission delay,
no warmup, one-bar forward training labels. Label endpoints must be available by
train end <= OOS start. Labels are calculated only using verified training rows;
never read the first OOS close to finish the last training label. Selector receives
retained feature rows and matching labels only. Prefix isolation is not an OS/network
sandbox; callbacks are trusted application code. Reset/deepcopy initial selector
and strategy state for each window; no hidden state carries between windows.

Windows are explicit ordered nonoverlapping OOS ranges within one TRAIN/VAL zone,
with complete source coverage, minimum training rows and minimum OOS rows. A fresh
portfolio/capital is used per window. FIRST_CLOSE baseline drops each window's first
close as a return observation; no synthetic seam return. Frequency identity includes
WINDOW_BLOCK, exact test ranges, baseline, capital reset, step and no forced terminal
liquidation. Preserve return block indexes/timestamps. Never interpret flattened
returns or their product as a continuous traded portfolio. Existing continuous
result frequency hashes cannot mix with this profile.

All required windows must finish COMPLETED with full expected equity endpoints.
A risk/data/signal stop or invalid return intervals invalidates the whole run; all
observed returns remain in evidence, the trial remains counted, and later windows
are not executed. It is not legitimate to aggregate only successful windows as VALID.
Exceptions leave RESERVED without manufactured completion. Poor complete returns
remain eligible observations. DSR is separate and never selects a candidate.

Per-window actual observations/native journal are independently verified; full six
timestamps and membership feed actual registry TIMING_VALIDATION. Outcome v2 hashes
one receipt binding RUN, all ordered window manifests, selections, purges, observations,
timing receipts, return blocks and resulting returns. Read-only verification uses
committed registry replay and rederives complete schedule/purge/timing/
returns from stored evidence without source reads, selector calls or N increments.
It must reject missing, swapped, mutated or reordered evidence. No cached statistics
are relabeled current. Replay validates committed selection provenance and candidate
membership; it does not rerun or independently prove the selector decision. Both
completion and replay compare derived lifecycle/result to the actual committed
ledger outcome, not just a successful append or replay flag. Full historical pipeline and prospective acceptance remain
separate until their own reviewed integration exists.
