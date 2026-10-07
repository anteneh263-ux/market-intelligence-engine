# Market Intelligence Engine Project Contract

This contract applies to the entire repository. Follow it together with
[`ADR-MIE-001`](docs/adr/ADR-MIE-001-authoritative-engines-and-evidence-model.md).
If a requested action conflicts with this contract, stop and obtain explicit
authorisation before changing the repository or scientific evidence.

## Purpose and authority boundaries

- MIE is a research platform for reproducible strategy evaluation. It is not a
  proven profitable system, a live-money system, or authority to trade or
  allocate capital.
- Historical research authority is `app.research_core` together with
  `app.strategy_research`.
- The target prospective simulated-execution authority is `app.paper.session`.
  It is currently a deterministic replay engine and is not yet operationally
  authoritative for continuous prospective execution.
- Phase 5 under `app.research` is the target experiment-identity and
  trial-governance authority. Its complete registration, reservation, timing,
  dataset-binding, outcome, and recovery lifecycle is not yet implemented and
  production-verified.
- Existing `PaperEngine`, `ForwardEngine`, the V1 backtester, and their
  artifacts retain their current engine, schema, stage, and evidence semantics
  until a deterministic cutoff is separately approved and implemented.

## Evidence and claim discipline

- Report **planned**, **implemented**, **unit-tested**, **integration-tested**,
  and **production-verified** separately. Also distinguish tests collected,
  executed, passed, failed, skipped, and blocked.
- Trace every material claim through:

  ```text
  documentation → production implementation → actual call site → persisted result
  ```

- A helper, import, test fixture, documentation statement, or passing isolated
  test does not prove that a control is active in the production path.
- A positive backtest, passing test, implemented component, or persisted report
  does not prove profitability or prospective validity.
- Identify the authoritative storage owner and physical evidence location before
  writing runtime or scientific evidence. Do not let constructors or defaults
  silently create stores in the source checkout.

## Scientific versioning and stage separation

- Never silently modify an existing evaluation-matrix version or historical
  artifact. Preserve original provenance, engine, schema, identity, and hashes.
- Methodology changes require a new version, separate results, dedicated review,
  and a separate pull request. Never present an in-place scientific change as a
  behavior-preserving fix.
- Keep these stages explicit and separate:
  - historical evaluation;
  - historical walk-forward evaluation;
  - legacy paper simulation;
  - controlled sessions;
  - forward experiments;
  - Phase 5 identity, registry, and trial evidence.
- Do not transfer status, thresholds, results, or authority between stages
  without a validated, reviewable contract.

## Review and change controls

- Statistics, execution timing, manifests, dataset canonicalization, identity
  contracts, registries, ledgers, receipts, and migrations require dedicated
  review appropriate to their scientific and integrity risks.
- Preserve existing user changes and untracked or ignored artifacts. Before
  working, record the repository, branch, commit, worktree state, and scope.
  Do not reset, clean, stash, overwrite, or absorb unrelated work.
- Destructive cleanup, artifact repair, migration, evidence-root creation,
  legacy cutoff enforcement, and removal require separate explicit
  authorisation.
- Never invent, repair, reconstruct, backdate, silently replace, or relabel
  scientific evidence. Contradictions must be documented without rewriting
  history.
- A new durable engine or store requires an explicit architecture decision that
  defines ownership, authority, migration, equivalence, and failure handling.

## H-025 contract

- H-025 was intended for a prospective test from **1 August 2026 through
  31 July 2027**.
- The authoritative frozen artifact has not been located.
- Never claim that H-025 was verified as frozen, activated, or started unless
  the original authoritative manifest, hashes, identity bindings, event and
  evidence ledgers, receipts, storage authority, and full evidence chain are
  located and independently validated.
- Do not modify, reconstruct, backdate, migrate, relabel, replay as a substitute,
  or generate replacement H-025 manifests, observations, journals, ledgers,
  receipts, registrations, status transitions, or other artifacts.
- Stop work if an action could affect H-025 or an unidentified artifact that
  might belong to H-025. Obtain the controlling artifact and explicit scope
  before proceeding.

## Default stop conditions

Stop before writing or running state-changing workflows when storage authority
is unresolved, evidence identity conflicts, a methodology change lacks a new
version, a requested repair would manufacture history, or the action may affect
frozen or reportedly frozen work. Report the unresolved boundary instead of
creating substitute evidence.
