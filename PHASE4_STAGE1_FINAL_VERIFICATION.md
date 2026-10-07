# Phase 4 — Stage 1 (Controlled Paper Trading): Final Verification (post test-cleanup)

**Type:** Verification + test-only cleanup. **No production code changed** (all `app/paper/session/`
module hashes are byte-identical to the technically-approved state — see §0). No new features. Stage 2
not started. Not committed/pushed — awaiting your check.

**Verdict:** **APPROVE for phase freeze.**

---

## 0. What changed in this pass

Only the Phase-4 **test file** was edited — six multi-outcome ("passes either way") assertions were
tightened to pin the deterministic outcome. Proof that no production code moved:

```
app/paper/session/__init__.py   a25b55d4…   (unchanged)
app/paper/session/sanitizer.py  f4fad3c0…   (unchanged)
app/paper/session/profiles.py   2d53b8e3…   (unchanged)
app/paper/session/manifest.py   1af26b54…   (unchanged)
app/paper/session/lifecycle.py  45e7e7bb…   (unchanged)
app/paper/session/risk.py       a522df15…   (unchanged)
app/paper/session/broker.py     3f00d60a…   (unchanged)
app/paper/session/engine.py     138cbd3b…   (unchanged)
app/paper/session/ingest.py     9f48ef48…   (unchanged)
app/paper/session/journal.py    8f1326b9…   (unchanged)

tests/test_paper_session_phase4.py  ce0f798f7fff8bffe67a7df0cef2fbe9d1df4ffa94065a5c874dfcb52d2ba61f  (edited)
aggregate (all session .py + test file)  ebc2724fc28b0b5fbc559fbb468e2ccd61deca24548154219ec1a637bae72ef0
```

### The six tightened assertions

Each "deterministic" claim was proven by running the exact scenario repeatedly and observing a single
stable outcome before pinning it.

| Test | Before (weak) | After (pinned) | Deterministic evidence |
|---|---|---|---|
| `test_engine_enforces_per_trade_loss` | `status in (RISK_STOPPED, COMPLETED)` | `status == RISK_STOPPED` **and** `breaches == {"max_loss_per_trade"}` | always RISK_STOPPED / `{max_loss_per_trade}` |
| `test_empty_frame_is_not_completed` | `status in (INVALIDATED, DATA_STOPPED, "NOT_AVAILABLE")` + loose reason | `status == INVALIDATED`, `mismatched_field == "empty_dataset"`, `"empty_dataset" in reason`, `journal_path is None`, **`os.listdir(tmp)==[]`** (no journal written) | always INVALIDATED at preflight, empty dir |
| `test_pilot_btc_deterministic_replay_with_trades` | `status in (COMPLETED, RISK_STOPPED)` + conditional branch | `status == RISK_STOPPED`, `"max_daily_loss" in breaches` | fixed `iloc[300:900]` slice always RISK_STOPs on max_daily_loss |
| `test_risk_stop_when_already_flat_records_no_exit_order` | `status in (COMPLETED, RISK_STOPPED)` | `status == COMPLETED`, no breaches | flat book → equity can't move → always COMPLETED |
| `test_engine_enforces_drawdown_and_session_loss` | `"max_drawdown" in rules or "max_session_loss" in rules` | `"max_drawdown" in rules and "max_session_loss" in rules` | crash always breaches both |
| `test_oversized_long_clamped_to_leverage` | `o["status"] in ("partial", "filled")` | `o["status"] == "partial"` | 5x request clamped to 1.0x cap → always partial |

A misleading inline comment on the already-flat test (which wrongly said "stays RISK_STOPPED") was
corrected to describe the real COMPLETED behavior; the historical test name was left unchanged.

**No multi-outcome lifecycle/status assertions remain** in the file. A full re-grep of the tightened
file finds zero `status in (...)` disjunctions. The only two `or` usages left are **not** weak
either/or outcomes:
- line 318 `assert all(o["status"] != "filled" or o["side"] == "sell" …)` — a logical *implication*
  (if filled ⇒ sell), a universally-quantified correctness property.
- line 707 `assert not (res.get("absolute_risk") or {}).get("breaches")` — a `None`-guard idiom
  (`x or {}`), not an assertion disjunction.

---

## 1. Re-run results (after the cleanup)

```
export PYTHONPATH=/root/.local/share/uv/tools/pytest/lib/python3.11/site-packages

$ python -m pytest tests/test_paper_session_phase4.py
132 passed in 4.31s

$ python -m pytest
438 passed in 52.03s
```

The six tightened tests (and their parametrized cases) all pass.

### H-025 replay, FROM BAR 0, twice

```
$ python /tmp/replay_h025_bar0.py
BARS (from bar 0): 3111
lifecycle_status : RISK_STOPPED  (breach: max_daily_loss, observed 0.05312 > limit 0.05)
final_equity     : 103380.07000387376      net_pnl : +3380.07

journal.jsonl byte-identical : True
run1 sha256                  : cc1675462807037f1807052167febcdccb2c84e7ae858de4e85a2178d9e784ba
run2 sha256                  : cc1675462807037f1807052167febcdccb2c84e7ae858de4e85a2178d9e784ba
final_event_hash (both runs) : dce0571d6c631b850c4e39a2c9691a1ee461093e5eebdcd8c76f6df3ef6fa5e5
journal verifies (run1/run2) : True True
LIVE_EXECUTION_ENABLED       : False
```

Determinism is unchanged (expected — only tests were edited): the two independent replays produce a
byte-identical, self-verifying journal and identical `final_event_hash`.

---

## 2. Safety guards — how they were verified (correct description)

The 12 safety guards were verified by **guard-removal mutation testing**: for each guard, the guarding
line is disabled in a throwaway copy of the source under `/tmp/mut`, the binding test is re-run and
must **fail**, and then the source is restored and the test must **pass** again (fail-before /
pass-after). This demonstrates each test is non-vacuous — it actually holds the guard.

**This is NOT an execution against the pushed baseline commit `e64b4cc`.** It is a source-mutation
technique run against the working files listed in §0. All 12 guards are bound (11 directly; the
negative-target sanitizer guard is bound by `test_sanitizer_classifies_every_case[-1.0-…]` once run
against the correct sanitizer-layer test rather than the broker-layer selector). The full 12-row guard
matrix from the prior report is unchanged, since no production code was modified in this pass.

---

## 3. Version / commit / push status

- **Baseline `e64b4cc`** (the Phase 3 + Phase 4 Stage 1 snapshot secured earlier) **is already pushed**
  to `origin/feature/shadow-forward-v1`.
- **This pass's test-cleanup changes are uncommitted.** In this reprovisioned container the tracked
  `HEAD` is `53fa30d` and the Phase-4 files (plus this report) appear as untracked/modified working
  files; they were verified directly by content hash (§0). After your review and approval, the current
  Stage 1 test-cleanup **must be committed and pushed** to `origin/feature/shadow-forward-v1` (run the
  push from your Mac terminal, since this container's token lacks write scope). I did **not** commit or
  push per your instruction.

---

## 4. Remaining limitations

1. **RESOLVED — the weak per-trade / empty / pilot / already-flat / drawdown / oversized assertions**
   from the prior report are now pinned to their deterministic outcomes (§0).
2. **Tamper-evidence ≠ cryptographic signature.** The journal is an append-only hash chain
   (`parent_hash`/`event_hash`): it detects post-hoc edits and reproduces deterministically, but it is
   not signed and does not by itself prove authorship. Documented as such.
3. **Short trading unimplemented by design.** Stage 1 is long-only-locked at three layers (manifest
   reject, `shorting_allowed=False`, broker `raise`); short-margin/leverage accounting is a separate
   future phase.
4. **Market-hours granularity is daily** for the pilot instruments.
5. **`pytest` sourced from the local uv toolchain** (the container's package index returned 403); the
   app code and system interpreter were not modified, so the results are valid.

---

## 5. Recommendation

**APPROVE — ready to freeze Phase 4 Stage 1.**

- 132 / 132 Phase-4 tests pass; 438 / 438 full suite passes, after tightening.
- No production code changed; every session module byte-identical to the technically-approved state.
- All previously-weak multi-outcome assertions are now pinned to their verified deterministic outcomes;
  remaining disjunctions are genuine, lawful, and documented.
- Replay determinism and fail-closed behavior are intact; all 12 safety guards remain bound (verified
  by guard-removal mutation testing, not by a run against `e64b4cc`).

Next action after your check: commit the current test-cleanup and push to
`origin/feature/shadow-forward-v1`. I will not do so until you confirm.
