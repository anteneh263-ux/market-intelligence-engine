# ADR-MIE-001: Authoritative Engines and Evidence Model

**Status:** Accepted — target architecture; implementation not yet authorised

**Decision date:** 2026-08-26

**Revision:** 1.0

**Scope:** Target architecture and domain-authority model

**Repository modifications:** None

---

## 1. Decision statement

> **ADR-MIE-001 is accepted as the target architecture and domain-authority model. Acceptance does not activate the target production workflow, deprecate existing components immediately, validate any existing prospective experiment, or authorise changes to H-025. Each authority becomes operational only after its stated integration, storage, testing, and evidence-verification conditions are satisfied.**

MIE will assign separate authorities to separate research responsibilities:

1. **Historical research authority**

   `app.research_core` together with `app.strategy_research`

2. **Prospective simulated-execution target authority**

   `app.paper.session`

3. **Experiment identity and trial-governance target authority**

   Phase 5 contracts and registry components under `app.research`

4. **Human-readable research corpus and publication layer**

   `knowledge_base/`

The original paper engine, original `forward-v1` engine, V1 backtester, and their storage systems will become legacy compatibility systems only after their replacement conditions and a deterministic cutoff boundary have been approved and satisfied.

No component is considered operationally authoritative merely because its code exists or because this ADR names it as the target authority.

---

## 2. Epistemic and implementation status

This ADR distinguishes the following states:

- **Planned:** Described as part of the target architecture.
- **Implemented:** Production code exists for at least part of the capability.
- **Unit-tested:** Isolated behavior has an executed passing test.
- **Integration-tested:** The real cross-component path has an executed passing test.
- **Production-verified:** The intended active path has produced a validated, persisted artifact under the required controls.

The target architecture contains components at different maturity levels. Acceptance of this ADR does not promote any component from implemented or tested to production-verified.

Every material claim must ultimately be traceable through:

```text
documentation
    → production implementation
    → actual production call site
    → persisted, verifiable result
```

“The function exists” is not equivalent to “the control is active.”

---

## 3. Current-state findings

### 3.1 Current callable surfaces

The current unified CLI exposes:

- Research evaluation through `Orchestrator`
- The original `PaperEngine` through `mie paper`
- The original `ForwardEngine` through `mie forward`
- Research-safety commands through `mie safety`

The HTTP application exposes both:

- the newer Research Core through `/api/engine/*`;
- the legacy SQLAlchemy backtest system through `/api/backtests/*`.

### 3.2 Implemented but not fully integrated target components

The following have implementations, but they have not been verified as one complete authoritative production lifecycle:

- `app.paper.session`
- `app.forward.shadow`
- `app.research.phase5_identity_contract`
- `app.research.phase5_trial_registry`

In particular:

- `app.paper.session` currently operates as a deterministic replay engine.
- It is not yet a continuous forward-running system that safely closes and processes newly arriving market-data segments.
- Its replay entry point receives a session manifest as an in-memory object and writes a journal.
- The current target path does not yet demonstrate that the immutable manifest is first persisted, read back, verified, and bound to execution before journal creation.
- Phase 5 identity validation is implemented as a pure validation function.
- The current Phase 5 registry component initializes or validates the SQLite registry and its anchor document.
- Trial registration, trial reservation, timing-ledger recording, and outcome appending remain target capabilities.
- No verified production call path currently connects Phase 5 identity, the Phase 5 registry, a controlled paper session, and resulting evidence into one governed lifecycle.

Therefore, the target components are not to be described as a complete active Phase 5 prospective-testing platform.

### 3.3 Located artifacts

The inspected checkout did not establish the presence of authoritative runtime artifacts for:

- controlled paper-session manifests;
- controlled session journals;
- shadow-forward manifests or ledgers;
- active forward-test manifests;
- Phase 5 registry databases or anchor documents.

Architecture can be assessed from code, but no active prospective experiment can be validated from code alone.

---

## 4. Authoritative engine decision

### 4.1 Historical research authority

The authoritative target for historical strategy evaluation remains:

```text
app.research_core.Orchestrator
        │
        ▼
app.strategy_research
        │
        ├── providers
        ├── strategies
        ├── walk-forward evaluation
        ├── backtesting
        ├── costs and slippage
        ├── benchmarks
        ├── statistical evaluation
        └── mechanical verdicts
```

Its responsibilities are limited to:

- historical evaluation;
- historical walk-forward out-of-sample analysis;
- benchmark comparison;
- matrix-versioned statistical assessment;
- production of versioned historical research records.

It must not:

- manage prospective experiment lifecycle;
- create or alter prospective evidence;
- act as the Phase 5 trial registry;
- treat historical results as forward evidence;
- infer that a prospective experiment started;
- silently change the methodology of an existing matrix version.

Existing matrix versions must remain unchanged. A methodology correction or scientific change requires:

1. a new matrix version;
2. an explicit provenance boundary;
3. separate results;
4. a separate pull request;
5. code review appropriate to the affected statistical or execution behavior.

### 4.2 Prospective simulated-execution target authority

The selected target engine for new prospective simulated execution is:

```text
app.paper.session
```

It is preferred because its design provides or aims to provide:

- deterministic execution;
- explicit session manifests;
- strategy, parameter, dataset, execution, and risk bindings;
- causal decision and execution timing;
- frozen signal policies;
- fail-closed runtime validation;
- controlled session lifecycle states;
- manifest-bound journals;
- paper-only and live-disabled invariants;
- explicit risk and data-quality handling.

However, `app.paper.session` is currently a **deterministic replay engine**, not an operational continuous forward runner.

Its target authority becomes operational only after all of the following exist:

1. A controlled production entry point.
2. Explicit evidence-root configuration.
3. Phase 5 identity and registry integration.
4. Durable persistence and verification of the session manifest before execution.
5. Closed-segment market-data processing.
6. Cryptographic binding among manifest, dataset segment, journal, reports, lifecycle evidence, and receipts.
7. Full integration tests for the real lifecycle.
8. A verified non-H-025 prospective experiment demonstrating the complete evidence path.
9. Independent review of statistics, execution timing, manifests, registries, and ledgers.

Until those conditions are satisfied, `app.paper.session` is the **accepted target authority**, not the active production authority.

---

## 5. Continuous prospective execution model

A future production entry point must not treat a growing market-data file as one continuously mutable dataset.

Instead, it must process a sequence of closed, immutable segments:

```text
Open market-data intake
        │
        ▼
Deterministic segment-close rule
        │
        ▼
Closed segment with frozen boundaries
        │
        ▼
Dataset identity and segment hash
        │
        ▼
Persisted and verified session manifest
        │
        ▼
Deterministic execution over that segment
        │
        ▼
Manifest-bound journal and reports
        │
        ▼
Verified receipt and lifecycle update
        │
        ▼
Next segment may be opened
```

### 5.1 Segment closure

The future production entry point must define, before execution:

- the segment’s opening boundary;
- the segment’s closing boundary;
- timezone and trading-calendar rules;
- inclusion and exclusion semantics;
- asset universe;
- timeframe;
- expected bars or observations;
- late-arriving-data policy;
- duplicate-data policy;
- missing-data policy;
- revision policy;
- data-provider identity;
- canonical serialization and hashing method.

A segment must be closed before it can become scientific evidence.

### 5.2 No mutation of previous evidence

When new market data arrives:

- it must form a new segment or be handled under an explicitly preregistered correction policy;
- prior segment files must not be extended in place;
- prior dataset hashes must not change;
- prior manifests and journals must not be rewritten;
- previous reports must not be regenerated under changed inputs while retaining their old identity.

A corrected or restated segment must receive:

- a new segment identity;
- a new dataset hash;
- explicit linkage to the superseded segment;
- a reason for supersession;
- preservation of the original segment and its evidence.

### 5.3 Continuous operation

“Continuous” does not mean that evidence is mutable or that the engine remains permanently attached to a growing file.

It means that the system repeatedly performs a controlled sequence:

```text
close → freeze → hash → bind → execute → verify → append outcome
```

Each completed segment must remain independently reproducible.

---

## 6. Prospective dataset identity

### 6.1 Historical replay identity

A fixed historical dataset may be identified by a hash of the entire canonical dataset because its content and coverage are already closed.

### 6.2 Prospective segment identity

A prospective dataset identity must be defined per closed segment.

Each segment identity must include or bind:

- research-program identity;
- experiment identity;
- provider identity;
- asset or instrument identity;
- timeframe;
- segment start boundary;
- segment end boundary;
- first and last included observation;
- observation or bar count;
- coverage and calendar rules;
- canonical schema version;
- canonicalization method;
- data-quality status;
- content hash;
- optional parent or previous-segment identity.

Conceptually:

```text
segment_id = hash(
    experiment_id
    + provider_identity
    + asset_universe
    + timeframe
    + coverage_boundaries
    + schema_version
    + canonical_data_hash
)
```

The exact canonical format and hash algorithm must be frozen in a versioned specification before implementation.

### 6.3 Growing source files

A mutable provider file may be used as an intake source, but it is never itself authoritative scientific evidence.

Before execution, the relevant closed segment must be:

1. extracted;
2. canonicalized;
3. persisted in the evidence root;
4. hashed;
5. bound to the session manifest;
6. verified on readback.

Execution must use the frozen segment or verify that the supplied runtime segment exactly matches it.

---

## 7. Physical authority of the session manifest

### 7.1 Manifest authority

The authoritative session manifest must be a durable artifact in the evidence root.

An in-memory manifest object is not sufficient.

Before execution begins, the target workflow must:

1. Construct the complete session manifest.
2. Canonicalize it using a versioned canonicalization rule.
3. Calculate its manifest hash.
4. Persist it atomically to the assigned session directory.
5. Flush it durably according to the approved storage policy.
6. Read it back from the authoritative path.
7. Recalculate and verify its hash.
8. Verify its references to:
   - experiment identity;
   - trial identity;
   - strategy and parameter hashes;
   - risk-policy hashes;
   - execution-policy hashes;
   - closed dataset-segment identity and hash.
9. Pass the verified persisted representation into execution.
10. Bind the journal genesis record to that manifest hash.

If persistence or verification fails, execution must not begin and no scientific session journal may be created.

### 7.2 Manifest immutability

The session manifest must not contain mutable current state.

Changes in session state must be represented through append-only lifecycle or journal events rather than rewriting the manifest.

Any change to a frozen manifest field creates a new manifest identity and, where scientifically material, a new session or experiment identity.

### 7.3 Partial or failed starts

The target workflow must define deterministic recovery for:

- manifest persisted but journal not started;
- manifest persisted and journal genesis written, but execution interrupted;
- segment closed but trial not successfully reserved;
- trial reserved but session never started;
- session started but final report not produced.

Recovery must preserve the original artifacts and append status or recovery evidence. It must not manufacture a clean history by deleting or replacing partial records.

---

## 8. Phase 5 role and capability boundary

Phase 5 is the target outer governance control plane. It is not another backtest engine or execution simulator.

### 8.1 Verified current scope

The current Phase 5 components establish only part of the target control plane:

- deterministic experiment-contract validation exists;
- SQLite registry and anchor initialization logic exists.

The current registry component must not be described as providing a complete trial lifecycle.

### 8.2 Target Phase 5 capabilities

The following remain target capabilities until implemented, integrated, tested, and production-verified:

- research-program registration;
- experiment registration;
- trial registration;
- trial reservation;
- reservation expiry or abandonment handling;
- timing-ledger recording;
- dataset-segment binding;
- session binding;
- outcome appending;
- trial-count and multiplicity accounting;
- final lifecycle status;
- recovery from interrupted operations;
- immutable or append-only provenance;
- cross-record integrity verification.

### 8.3 Target operation order

The intended governed workflow is:

```text
Validate Phase 5 experiment contract
        │
        ▼
Verify authoritative registry and anchor
        │
        ▼
Register or verify research program
        │
        ▼
Register or verify experiment
        │
        ▼
Close and hash prospective data segment
        │
        ▼
Reserve or register trial
        │
        ▼
Create and persist immutable session manifest
        │
        ▼
Read back and verify manifest
        │
        ▼
Run controlled deterministic session
        │
        ▼
Verify journal and produce bound reports
        │
        ▼
Append trial outcome and lifecycle evidence
        │
        ▼
Issue and externally anchor receipt
```

No session result may be called Phase 5 evidence merely because Phase 5 code exists. The result must be registered and cryptographically bound to the correct program, experiment, trial, dataset segment, timing contract, session manifest, and journal.

---

## 9. Forward testing and controlled sessions

MIE will not maintain a second independent authoritative forward-execution simulator.

Instead:

- a **controlled paper session** is the execution unit;
- a **closed dataset segment** is the prospective data unit;
- a **forward test** is the scientific experiment governing and evaluating a sequence of controlled sessions and closed segments.

Conceptually:

```text
Forward experiment
   ├── Phase 5 experiment identity
   ├── frozen experiment definition
   ├── preregistered thresholds
   ├── lifecycle ledger
   ├── closed prospective segments
   │   └── controlled paper sessions
   │       ├── persisted session manifest
   │       ├── segment hash
   │       ├── journal
   │       └── reports
   ├── checkpoint receipts
   └── final forward evaluation
```

The forward layer may:

- admit or reject an experiment;
- manage experiment-level status;
- aggregate verified session evidence;
- evaluate preregistered checkpoints;
- apply preregistered consistency and promotion rules;
- produce final experiment conclusions.

It must not independently reimplement:

- signal-generation timing;
- position sizing;
- fees and slippage;
- order simulation;
- risk stops;
- portfolio accounting.

---

## 10. Shadow-Forward role

`app.forward.shadow` will be retained as a target shared policy and evidence-governance library. It must not become another independent execution engine or uncontrolled evidence store.

Its appropriate responsibilities include:

- experiment admission status;
- expected ranges;
- regime policy;
- consistency checks;
- checkpoint receipts;
- lifecycle-transition rules;
- manifest verification;
- ledger verification.

### 10.1 Experiment lifecycle

The experiment-governance layer owns statuses such as:

- `INVALID`
- `REJECT`
- `SHADOW`
- `PROMOTE`
- `LIVE_READY`
- `INVALIDATED`

This answers:

> What research-governance status does the overall prospective experiment have?

### 10.2 Session lifecycle

The controlled session layer owns statuses such as:

- `CREATED`
- `VALIDATED`
- `RUNNING`
- `PAUSED`
- `DATA_STOPPED`
- `RISK_STOPPED`
- `SIGNAL_STOPPED`
- `INVALIDATED`
- `COMPLETED`

This answers:

> What happened during one controlled execution session?

A session reaching `COMPLETED` does not automatically promote the experiment or demonstrate scientific support.

---

## 11. `LIVE_READY` semantics

`LIVE_READY` is strictly a **research-governance status**.

It means only that an experiment has satisfied the preregistered research conditions associated with that status.

It does not mean:

- approved for real-money trading;
- safe to trade;
- profitable;
- production-deployable;
- broker-connected;
- authorised for capital allocation;
- authorised to place real orders.

MIE currently has no live-execution authority.

No MIE component may translate `LIVE_READY` into live execution. Any future real-money system would require:

1. a separate architecture decision;
2. separate risk and compliance controls;
3. separate credentials and broker boundaries;
4. explicit human authorisation;
5. independent operational testing;
6. a new security and capital-governance model.

Before implementation, the project should evaluate renaming `LIVE_READY` to a less ambiguous status, such as `RESEARCH_VALIDATED`.

Renaming must not rewrite historical evidence. If adopted, it requires a versioned schema and an explicit compatibility mapping.

Until then, documentation and interfaces must display the full meaning:

> `LIVE_READY` — research-governance status only; no live-execution authority.

---

## 12. Cryptographic evidence binding

In this architecture, “immutable” means **cryptographically bound and verifiable**, not merely “should not be edited.”

### 12.1 Required evidence graph

The target evidence graph must bind:

```text
Research-program identity
        │
        ▼
Experiment contract and identity
        │
        ▼
Trial registration or reservation
        │
        ▼
Closed dataset-segment identity and hash
        │
        ▼
Persisted session manifest and hash
        │
        ▼
Journal genesis and hash chain
        │
        ▼
Session-final record and journal head
        │
        ▼
Derived report hashes
        │
        ▼
Experiment lifecycle event
        │
        ▼
Checkpoint receipt
        │
        ▼
Independent anchor or registry record
```

### 12.2 Binding requirements

At minimum:

- The experiment record must reference the experiment-contract hash.
- The trial record must reference the experiment identity.
- The trial or session record must reference the closed dataset-segment identity.
- The session manifest must include the dataset hash and relevant policy hashes.
- The journal genesis must reference the session-manifest hash.
- Every journal event must be hash-chained.
- The session-final record must be included in the journal chain.
- Reports must include:
  - report schema version;
  - session-manifest hash;
  - journal head hash;
  - journal event count;
  - dataset-segment hash;
  - report content hash.
- Experiment lifecycle events must reference the verified evidence they evaluate.
- Checkpoint receipts must bind:
  - experiment identity;
  - trial or session identity;
  - manifest hash;
  - journal head;
  - event count;
  - report hashes;
  - lifecycle-ledger head where applicable.
- An independent registry or external anchor must preserve enough information to detect final-event deletion or local evidence-tree replacement.

### 12.3 Verification behavior

Verification must fail closed if:

- any required hash is absent;
- canonicalization versions are unknown;
- a dataset segment differs from its frozen hash;
- a manifest differs from its stored hash;
- a journal chain is broken;
- a journal does not reference the expected manifest;
- a report references the wrong journal head;
- an experiment event references unverified evidence;
- a receipt belongs to another experiment;
- an expected chain head or record count differs;
- required registry bindings cannot be resolved.

Locally hash-chained files are tamper-evident, not absolutely immutable. Independent anchoring is required to detect complete replacement or deletion of a chain’s final portion.

---

## 13. Authoritative storage model

### 13.1 Source repository

The source repository is authoritative for:

- source code;
- tests;
- versioned matrix definitions;
- schema definitions;
- canonicalization specifications;
- documentation;
- architecture decisions;
- compatibility readers.

It is not automatically the runtime evidence store.

Runtime evidence paths must not be inferred from:

- the current working directory;
- the source checkout;
- a default such as `./paper_trading`;
- a constructor that silently creates state.

### 13.2 Explicit evidence root

All new prospective evidence must use an explicitly configured evidence root separate from the source tree.

The exact physical location remains an implementation decision requiring separate authorisation. The logical target structure is:

```text
<EVIDENCE_ROOT>/
├── registry/
│   ├── phase5-registry.sqlite
│   └── registry-metadata/
├── programs/
│   └── <research_program_id>/
│       ├── program-registration.json
│       └── experiments/
│           └── <experiment_id>/
│               ├── experiment-contract.json
│               ├── experiment-manifest.json
│               ├── lifecycle-ledger.jsonl
│               ├── segments/
│               │   └── <segment_id>/
│               │       ├── segment-metadata.json
│               │       ├── canonical-data artifact or binding
│               │       └── segment-receipt.json
│               ├── sessions/
│               │   └── <session_id>/
│               │       ├── session-manifest.json
│               │       ├── journal.jsonl
│               │       └── reports/
│               ├── checkpoints/
│               └── final-evaluation.json
└── receipts/
    └── independently anchored receipts
```

The Phase 5 anchor should be stored or anchored independently where required to detect replacement, rollback, or local evidence loss.

### 13.3 Storage authority by evidence type

| Evidence type | Authoritative record | Derived or non-authoritative view |
|---|---|---|
| Historical matrix evaluation | Immutable per-version machine result with request and matrix hashes | Latest pointer, dossier, index |
| Strategy definition | Versioned source plus strategy and parameter hashes | Narrative dossier |
| Research-program identity | Phase 5 registry record and anchor | Display metadata |
| Experiment identity | Validated Phase 5 contract and registry record | Human-readable description |
| Trial reservation and status | Phase 5 registry or append-only trial record | Trial summary |
| Prospective data | Closed canonical segment with coverage identity and hash | Provider filename or dashboard |
| Session definition | Persisted, verified session manifest | Session summary |
| Executed session facts | Verified append-only session journal | Reports and dashboard |
| Segment performance | Derived report bound to segment, manifest, and journal hashes | Dashboard table |
| Experiment status | Verified experiment lifecycle ledger | CLI or status page |
| Checkpoint state | Receipt bound to ledger and report hashes, independently anchored | Checkpoint summary |
| Final conclusion | Frozen-threshold evaluation over verified evidence | Knowledge Base narrative |
| Literature | Structured citation record with source identifiers | Dossier prose |

### 13.4 Knowledge Base role

`knowledge_base/` remains the human-readable research corpus and publication layer.

It may contain:

- dossiers;
- literature;
- immutable historical result archives;
- indexes;
- learning reports;
- discovery summaries;
- references to prospective evidence;
- derived summaries of verified experiments.

It is not the sole authority for:

- session execution facts;
- Phase 5 trial counts;
- experiment identity;
- current prospective lifecycle state;
- closed dataset-segment identity.

A Knowledge Base summary must link to the authoritative evidence identity and hashes from which it was derived.

---

## 14. Legacy classification

Legacy classification does not take operational effect merely because this ADR is accepted.

Each legacy cutoff requires a separately approved deterministic boundary.

### 14.1 Legacy V1 backtester

Components including:

- `app.backtesting`
- `app.database`
- `/api/backtests/*`
- `market_intelligence.db`

are designated as target **legacy historical/MVP infrastructure**.

After the approved cutoff:

- existing code and data must be preserved according to retention policy;
- results must retain their original engine and schema labels;
- no result may be silently reinterpreted as a Research Core result;
- it must not create new authoritative scientific evaluations;
- compatibility reads remain until consumers are inventoried;
- removal requires a separate retention and deprecation decision.

### 14.2 Original `PaperEngine`

Components including:

- `app.paper.engine`
- `app.paper.cli_paper`
- `paper_trading/config.json`
- `paper_trading/state.json`
- `paper_trading/log/`

are designated as target **legacy paper simulator v1**.

After the approved cutoff:

- it must not create new authoritative experiments;
- existing state and logs must be preserved if found;
- existing results must retain their original engine and schema;
- old evidence must not be replayed or reconstructed to resemble controlled-session evidence;
- future CLI behavior must either route to the controlled session engine or be clearly labelled legacy.

### 14.3 Original `ForwardEngine`

Components including:

- `app.forward.engine`
- `app.forward.store`
- `app.forward.simulate`
- `app.forward.cli_forward`
- `forward-v1` manifests

are designated as target **legacy locked-forward v1**.

After the approved cutoff:

- no new authoritative `forward-v1` experiment may be created;
- existing `forward-v1` artifacts remain under their original semantics;
- readers and integrity verification must be preserved;
- artifacts must not be converted in place;
- any authorised migration must copy, verify, label, and preserve the original;
- missing evidence must never be invented to complete a migration.

### 14.4 Research-safety JSONL trial ledger

The current research-safety JSONL trial ledger remains authoritative only for records genuinely created under its existing protocol.

If Phase 5 becomes operational:

- new trials must use the approved Phase 5 path after the deterministic cutoff;
- the JSONL ledger becomes a frozen legacy ledger;
- existing records must not be rewritten;
- ambiguous historical records must not be assigned invented Phase 5 identities;
- imported records must retain original provenance and explicit migration status.

### 14.5 Shadow-Forward components

`app.forward.shadow` is retained, not deprecated wholesale.

Its reusable governance and verification logic may support the target architecture. Components that duplicate session execution or Phase 5 ownership must be reviewed individually before consolidation.

---

## 15. Deterministic legacy cutoff policy

A legacy cutoff must be defined before legacy write paths are blocked or redirected.

The cutoff must use at least one deterministic boundary, such as:

- an exact release tag;
- an exact commit;
- an exact schema version;
- an exact UTC date and time;
- a combination of release and date.

The final cutoff decision must state:

1. Which legacy components it covers.
2. The exact boundary.
3. Which operations become prohibited after the boundary.
4. Whether read-only verification remains supported.
5. How in-progress legacy experiments are handled.
6. How artifacts created before and after the boundary are identified.
7. Whether the CLI refuses, warns, or routes elsewhere.
8. How rollback is handled without creating mixed-authority evidence.

No cutoff is established by this ADR alone.

Until a separate cutoff decision is accepted:

- existing components are not immediately deprecated;
- no new target-authority claim may be inferred;
- new experiments must not be represented as authoritative where ownership remains unresolved;
- H-025 remains excluded from all cutoff-driven migration or repair.

---

## 16. Preservation rules

The following must be preserved unchanged unless a separate authorised preservation decision explicitly permits otherwise:

- existing evaluation-matrix versions;
- existing per-version strategy results;
- existing dossiers, including contradictory ones;
- existing trial ledgers and summaries;
- any located paper or forward manifests;
- any located journals, observations, receipts, or evaluations;
- Phase 4 verification reports;
- Phase 5 schema and identity definitions;
- Git history and historical tags;
- H-025-related documentation;
- any later-located H-025 artifacts.

Contradictions must be reported through new superseding documentation. Historical records must not be silently edited to create artificial consistency.

---

## 17. H-025 decision

The verified statement remains:

> H-025 was intended for a prospective test from 1 August 2026 through 31 July 2027. The authoritative frozen artifact has not been located.

Therefore:

- H-025 is excluded from migration, cleanup, replay, reconstruction, and cutoff processing.
- No new H-025 manifest may be generated as a substitute.
- No existing session may be relabelled as the intended H-025 prospective test.
- No observations may be backfilled.
- No manifest, hash, journal, ledger, receipt, registration, or lifecycle transition may be reconstructed.
- Historical deterministic replay does not prove that the prospective test started.
- H-025 may enter the target architecture only if the original authoritative artifacts are located and validated.
- If the artifacts are not found, the intended test remains unverified.
- Any future prospective test of related logic requires a new identity and an honest new start date.

This ADR does not authorise any change related to H-025.

---

## 18. Options considered

### Option A: Keep every engine equally authoritative

**Rejected.**

This would preserve:

- duplicate execution implementations;
- conflicting lifecycle semantics;
- multiple competing stores;
- unclear scientific authority;
- increased reconciliation risk.

### Option B: Make the original `ForwardEngine` the sole authority

**Rejected as the target.**

`forward-v1` has useful integrity controls, but the controlled-session design provides a stronger target foundation for explicit runtime binding, risk handling, execution policy, journals, and session evidence.

Existing `ForwardEngine` evidence retains its original status and is not invalidated by this decision.

### Option C: Build an entirely new execution engine

**Rejected.**

The repository already contains the core components needed for the target architecture. A new engine would increase fragmentation and require another equivalence and evidence model.

### Option D: Controlled sessions for execution, Research Core for historical research, and Phase 5 for governance

**Accepted as the target architecture.**

This assigns each component one primary responsibility while preserving existing evidence and compatibility paths.

---

## 19. Consequences

### Benefits

- One historical evaluation authority.
- One target prospective execution authority.
- One target identity and trial-governance authority.
- Forward tests reuse controlled-session evidence rather than duplicating execution.
- Prospective data is frozen per closed segment.
- Session manifests become durable evidence rather than transient objects.
- “Immutable” receives an explicit cryptographic meaning.
- Existing artifacts retain their original provenance.
- Storage ownership becomes explicit.
- `LIVE_READY` cannot be interpreted as trading authorisation.
- Future `AGENTS.md` and Codex rules can refer to one accepted architecture.

### Costs and risks

- The controlled replay engine requires new production orchestration before it can operate continuously.
- Phase 5 requires substantial lifecycle functionality beyond registry initialization.
- The current CLI and API require later restructuring.
- Canonical segment serialization must be specified carefully.
- Cryptographic binding and recovery logic add operational complexity.
- Compatibility readers must remain for legacy artifacts.
- Existing storage must be inventoried before any migration.
- Cross-engine equivalence tests are required before disabling legacy execution.
- External anchoring is needed for stronger tamper evidence.
- Renaming `LIVE_READY` requires schema versioning and compatibility planning.

---

## 20. Operational conditions for each authority

### 20.1 Historical research authority becomes operational when

- its documented production path matches actual call sites;
- matrix versions are frozen and identified;
- persisted results include request and matrix identity;
- statistical controls are traced to active production paths;
- integration tests execute successfully;
- output storage authority is defined.

### 20.2 Prospective session authority becomes operational when

- manifests are persisted and verified before execution;
- market data is closed and hashed per segment;
- continuous segment orchestration is implemented;
- journals and reports are cryptographically bound;
- recovery behavior is specified and tested;
- a controlled production entry point exists;
- a non-H-025 prospective experiment completes the verified path.

### 20.3 Phase 5 authority becomes operational when

- trial registration is implemented;
- reservation semantics are implemented;
- timing records are implemented;
- dataset-segment bindings are implemented;
- outcomes can be appended without rewriting history;
- interruption and recovery behavior is defined;
- registry and anchor integrity are verified;
- integration tests cover the complete workflow.

### 20.4 Forward experiment authority becomes operational when

- it consumes only verified controlled-session evidence;
- it does not duplicate execution behavior;
- experiment lifecycle records are hash-bound;
- checkpoint receipts are independently anchored;
- final evaluation uses preregistered thresholds;
- reports refer to verified session and segment evidence.

---

## 21. Preconditions before implementation or cleanup

No architecture implementation or cleanup is authorised until all applicable items below receive separate approval:

1. The physical evidence-root policy.
2. The canonical closed-segment specification.
3. The session-manifest persistence and verification protocol.
4. The cryptographic evidence-binding specification.
5. The Phase 5 trial-lifecycle specification.
6. Recovery and partial-failure semantics.
7. The controlled-session production-entry-point design.
8. The forward aggregation and experiment-lifecycle design.
9. The external receipt-anchoring policy.
10. An exact legacy cutoff release, commit, date, or equivalent boundary.
11. A read-only inventory of existing artifacts.
12. Classification of every located artifact by engine, schema, stage, and authority.
13. Cross-engine equivalence requirements.
14. Explicit exclusion of H-025 from migration and repair.
15. A concise repository `AGENTS.md` contract.
16. Equivalent Codex instructions or a Codex skill.
17. Separate, reviewable implementation pull requests.

The artifact inventory must not begin until separately authorised.

---

## 22. Required implementation sequence after separate authorisation

If implementation is later authorised, it should proceed through separate pull requests:

1. **ADR and concise project contract**
2. **Read-only artifact inventory tooling**
3. **Explicit evidence-root configuration**
4. **Canonical closed-segment identity specification**
5. **Session-manifest persistence and verification**
6. **Phase 5 trial registration and reservation**
7. **Timing-ledger and dataset-segment binding**
8. **Controlled prospective segment runner**
9. **Journal, report, lifecycle, and receipt hash binding**
10. **Forward experiment aggregation over verified sessions**
11. **Recovery and interrupted-operation handling**
12. **Compatibility readers**
13. **Deterministic legacy cutoff enforcement**
14. **Documentation correction**
15. **Evidence-preserving migration, where separately approved**
16. **Removal only after a separate retention decision**

Statistics, execution timing, manifests, dataset canonicalization, identity contracts, registries, ledgers, receipts, and migrations require dedicated code review.

---

## 23. Decision boundary

Acceptance of this ADR does **not**:

- authorise code changes;
- authorise repository cleanup;
- authorise an artifact inventory;
- create or authorise `AGENTS.md`;
- create or authorise a Codex skill;
- activate `app.paper.session` as a production forward runner;
- claim that Phase 5 has a complete trial lifecycle;
- establish the physical evidence root;
- establish a legacy cutoff;
- deprecate existing components immediately;
- move or migrate evidence;
- prove any prospective experiment is active;
- validate H-025;
- authorise changes to H-025;
- authorise real-money execution;
- treat `LIVE_READY` as permission to trade;
- claim that any target component is production-verified.

It establishes the accepted target architecture and domain-authority model. Operational authority must be earned separately through integration, explicit storage ownership, executed testing, cryptographic evidence verification, and validated persisted artifacts.
