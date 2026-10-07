# CONTRACT_BINDING v1

Internal helper for the future Phase 5 registry request path. No production call
site is added by this package; no reservation, counter, database or trust claim.

`check_contract_binding(contract_record, *, contract_hash, research_program_id,
experiment_id)` returns exactly `check_id: CONTRACT_BINDING`, `status: PASS|FAIL`,
and `reason_code` (null on success).

Inputs are built-in JSON-compatible values. Reject custom mapping/scalar classes,
non-string keys, tuples, cycles, non-finite numbers and non-serializable values.
All three external arguments must be lowercase `[0-9a-f]{64}` strings. Invalid
arguments return CONTRACT_BINDING_INVALID defensively; the future REQUEST_SCHEMA
check owns their public request-level errors.

After structural safety checks, require, in order:

1. dict with schema_version `wp5_0a1.contract.v4`;
2. contract_status `VALID`;
3. SHA-256 of canonical UTF-8 JSON body excluding only top-level contract_hash
   equals both embedded and supplied hashes;
4. well-formed research_program_id matching the supplied program;
5. well-formed experiment_id matching the supplied experiment;
6. timing_ledger_schema_version `wp5_0.timing_ledger_row.v1`.

Canonical JSON uses sort_keys=True, separators=(',', ':'), ensure_ascii=False,
allow_nan=False. List ordering and all other contract fields remain significant.
Malformed contract/program/experiment, serialization or hash errors yield
CONTRACT_BINDING_INVALID. A well-formed different program yields
WRONG_RESEARCH_PROGRAM_ID; a different experiment yields CONTRACT_BINDING_INVALID;
an unsupported/missing timing schema yields TIMING_SCHEMA_UNSUPPORTED.
First failure wins. Input is never mutated. Frozen WP5-0A1 statuses inside the
contract, including NOT_APPLICABLE, remain unchanged.

Sources: WP5-0A2 R2 K-1/K-7; R3 M-4 and section 12; final negative-test plan r05–r07.
The local implementation contract MIE-PHASE5-CONTRACT-BINDING-001 specifies this
helper API and defensive JSON boundary. These are new internal interface choices,
not a replacement for the frozen public registry envelope.

Synthetic tests obtain valid contracts through the public
validate_experiment_contract entry point. They do not import frozen test fixtures
or access real research artifacts. A self-consistent contract hash does not prove
producer identity, truthful data or experiment validity. Integration of this
helper into the real request path is a separate task.
