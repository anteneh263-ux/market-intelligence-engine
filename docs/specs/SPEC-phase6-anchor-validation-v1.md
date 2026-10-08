# WP6-2D — signed anchor and head validation v1

Owner app.research under ADR-MIE-004. Pure local verification only: no signer,
private key, key generation, network/service access, root creation or pilot
activation in production code. Synthetic tests alone generate test signing keys.
No registry migration or prior evidence changes. This package does not grant
runtime admission or declare a prospective run VERIFIED.

Use Ed25519 via pinned optional dependency `cryptography==50.0.2` (extra
phase6-anchor). No custom signature arithmetic. Existing environment stays intact;
validation environment uses an isolated temporary installation with pip report.
[API reference](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

## Exact records and signed bytes

WP0 canonical encoding C: UTF8 JSON sorted keys, no whitespace/newline, separators
comma/colon, no ASCII escaping, integers true signed64 nonnegative, ASCII strings,
unknown fields and floats forbidden. Public key is 32 bytes as 64 lowercase hex;
signature is 64 bytes as 128 lowercase hex. Sign/verify C(record excluding signature)
exactly. Schema version in signed content separates head from receipt signatures.

Existing receipt schema stays `p6.anchor_receipt.v1`: schema_version,run_id,
anchor_seq,subject_kind,subject_hash,previous_anchor_hash,retained_utc_ms,
service_identity,signature. Sequence1=RESERVATION,2=GENESIS,3+=CHECKPOINT.
Hash predecessor is SHA256 of C(entire signed receipt), zero at genesis.
Retained times nondecreasing. No receipt may precede or outlive trust validity.

Trust object exact: schema_version=p6.anchor_trust.v1,service_identity,endpoint_id,
algorithm=Ed25519,public_key_hex,not_before_utc_ms,not_after_utc_ms,
max_head_age_ms,max_clock_skew_ms. Token fields use WP0 token grammar. Validity
inclusive and not_before<not_after. max_head_age>0; skew>=0. H(trust) must equal
expected_trust_hash supplied from the approved plan. Key rotation needs a new trust
commitment/plan, not an inferred replacement key. Endpoint transport binding and
approval remain upstream, not proven by this object or a passing signature.

Signed head exact: schema_version=p6.anchor_head.v1,run_id,anchor_seq,anchor_hash,
service_identity,nonce,observed_utc_ms,retention_until_ms,signature.
nonce is 32 bytes as lowercase hex. Service signs its current committed head after
receiving a read-only challenge. retention_until_ms is the service's assertion that
ALL receipts in the prefix remain independently retained until that exclusive time.
The service must refuse this assertion if any required retention cannot be proven.
This field is not a validator inspection of a storage bucket or retention policy.

## Pure API

verify_anchors(request) accepts only a Python dict with exact fields:
trust,expected_trust_hash,expected_run_id,expected_subjects,receipts,head,challenge,
now_utc_ms. No JSON parser/transport in this package. Snapshot plain primitives first, with
maximum300,000visited values, nesting8, dict20keys, list10,000items, ASCII strings256characters and keys
128characters. Reject custom objects/nonfinite/floats/oversized structures without
calling user-defined deepcopy hooks. All schema/signature/check/output operations
use the same detached snapshot; never reread mutable caller input after verification. expected_subjects is a nonempty
list (<=10,000) of exact {subject_kind,subject_hash}; receipts same length, in order.
Each expected subject hash is unique; a repeated checkpoint cannot masquerade as
another anchor. Subject contents, reservation/governance identity and authority
must be independently resolved upstream. No performance/statistical assertions.

challenge is null iff head is null; otherwise exact nonce,issued_utc_ms.
The future coordinator must create a fresh unpredictable nonce, remember issuance
using a trusted clock, use it once, authenticate the service transport and fetch
its head. This pure function cannot establish that the caller actually did so.
Its supplied now and issuance values are constraints, not authentic clock evidence.

Validate all schemas before trust/identity, then signatures, sequence and timing.
Bind every receipt to expected run/service and expected subject at that index.
Check receipts contiguous and hash-linked, trust validity, retained_utc<=now+skew.
now must fall inside trust validity even for offline inspection. For an online
supplied head: same run/service, sequence=len(receipts), hash=H(last receipt),
nonce=challenge.nonce, and verify signature with the pinned trust key.
Require issued<=now, issued>=trust.not_before, now-issued<=max_head_age,
observed>=issued-skew, observed<=now+skew, now-observed<=max_head_age,
observed>=last.retained_utc, trust.not_before<=observed<=trust.not_after,
retention_until>now and retention_until>observed. A stale nonce/head, truncated
local prefix against newer signed head, expired retention or wrong key refuses.

Response exact: schema_version=p6.anchor_validation.v1,
cryptographic_status VALID/INVALID, reason_code null or closed refusal,
head_status MATCHED/OFFLINE/NOT_VALIDATED, anchor_count (0 on failure),
last_anchor_hash (null on failure), runtime_accepted=false,
external_service_verification=UPSTREAM_REQUIRED.
Offline can validate signatures/links but never certify current completeness.
MATCHED means supplied signed head matches supplied challenge and local chain; it
is not proof of external service deployment, durable retention or live head fetch.
No output is technical_verification=VERIFIED. No I/O or input mutation.

Refusal precedence: SCHEMA_INVALID, TRUST_MISMATCH, IDENTITY_MISMATCH,
DEPENDENCY_UNAVAILABLE, SIGNATURE_INVALID, SEQUENCE_INVALID, CLOCK_INVALID,
RETENTION_EXPIRED. Malformed key bytes/signature verify failure SIGNATURE_INVALID;
missing/unsupported backend DEPENDENCY_UNAVAILABLE. Unexpected programming/system
errors propagate rather than turning into a positive result. Algorithms other than
Ed25519 are unsupported schema; no fallback, HMAC/local-signing substitute or key
from the receipt. Lifecycle/approval decisions stay with the future coordinator.

Tests: RFC8032 verification vector; deterministic synthetic signed prefix/head;
forged/wrong key/signature/unsigned edits; prefix substitution/truncation/reorder;
wrong context/subject/service; duplicate checkpoint; boundary clocks and expired
retention; replayed nonce; offline status; missing dependency fail closed;
mutation-free/no network or file calls. Full prospective verification still needs
a reviewed deployed service and actual authenticated read-only head query.

Test reference: [RFC8032 section7.1](https://www.rfc-editor.org/rfc/rfc8032.txt).
Install the optional verifier dependency with `pip install -e '.[phase6-anchor]'`
in an approved development environment. Base imports remain usable without it;
valid-shaped anchor requests fail DEPENDENCY_UNAVAILABLE if the backend is absent.
The signed test module skips explicitly when the optional package is unavailable;
acceptance tests must run with the pinned backend and report actual executions.
