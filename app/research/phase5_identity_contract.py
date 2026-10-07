"""WP5-0A1 -- Phase 5 experiment identity and static timing contract validator.

Governing specification order (latest controls):
    1. PHASE5_MASTER_CONTROL_MATRIX_R3_FOR_CHATGPT.md
    2. PHASE5_MASTER_CONTROL_MATRIX_R3A_FOR_CHATGPT.md
    3. PHASE5_MASTER_CONTROL_MATRIX_R3B_FOR_CHATGPT.md
    4. PHASE5_WP5_0A1_CONTROL_MATRIX_R2_FOR_CHATGPT.md
    5. PHASE5_WP5_0A1_CONTROL_MATRIX_R2A_FOR_CHATGPT.md
    6. PHASE5_WP5_0A1_CONTROL_MATRIX_R2B_FOR_CHATGPT.md   <-- controlling

Frozen public surface:

    validate_experiment_contract(request: dict) -> dict

The function is pure and stateless: no filesystem access, no network access, no
clock read, no randomness, no floating-point arithmetic, no dataset / calendar /
manifest loading, no trial reservation, no Trial Counter, no holdout access and
no live execution.  Standard library only.
"""

import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["validate_experiment_contract"]


# ---------------------------------------------------------------------------
# Frozen schema versions (R2B section 0)
# ---------------------------------------------------------------------------

REQUEST_SCHEMA_VERSION = "wp5_0a1.request.v4"
CONTRACT_SCHEMA_VERSION = "wp5_0a1.contract.v4"
RESEARCH_SCOPE_SCHEMA_VERSION = "wp5_0a1.research_scope.v1"
REGISTRATION_SCHEMA_VERSION = "wp5_0a1.research_program_registration.v1"
DATASET_IDENTITY_SCHEMA_VERSION = "wp5_0a1.dataset_identity.v3"
STATIC_TIMING_POLICY_SCHEMA_VERSION = "wp5_0a1.static_timing_policy.v3"
BOUNDARIES_SCHEMA_VERSION = "wp5_0a1.boundaries.v1"
EXPERIMENT_CONFIGURATION_SCHEMA_VERSION = "wp5_0a1.experiment_configuration.v1"
PREREGISTRATION_SCHEMA_VERSION = "wp5_0a1.preregistration.v1"
TIMING_LEDGER_SCHEMA_VERSION = "wp5_0.timing_ledger_row.v1"

# Explicitly superseded versions -- recognised and refused (R2B section C.7, D-2).
SUPERSEDED_SCHEMA_VERSIONS = (
    "wp5_0a1.request.v3",
    "wp5_0a1.request.v2",
    "wp5_0a1.request.v1",
    "wp5_0a1.contract.v3",
    "wp5_0a1.contract.v2",
    "wp5_0a1.contract.v1",
    "wp5_0a1.static_timing_policy.v2",
    "wp5_0a1.static_timing_policy.v1",
    "wp5_0a1.dataset_identity.v2",
    "wp5_0a1.dataset_identity.v1",
)


# ---------------------------------------------------------------------------
# Frozen closed reason-code vocabulary -- exactly 43 (R2B section C.2)
# ---------------------------------------------------------------------------

REASON_CODES: Tuple[str, ...] = (
    "SCHEMA_VERSION_UNSUPPORTED",
    "UNKNOWN_FIELD_PRESENT",
    "MISSING_FIELD",
    "TRIAL_ID_OUT_OF_SCOPE",
    "COUNTER_FIELD_OUT_OF_SCOPE",
    "OUT_OF_SCOPE_FIELD",
    "TYPE_INVALID",
    "BOOL_NOT_ACCEPTED_AS_INT",
    "FLOAT_NOT_PERMITTED",
    "NULL_NOT_PERMITTED",
    "STRING_NOT_NFC",
    "RESEARCH_SCOPE_INVALID",
    "ADMIN_METADATA_IN_IDENTITY_PREIMAGE",
    "EXPERIMENT_CONFIGURATION_INVALID",
    "REGISTRATION_INVALID",
    "PREREGISTRATION_INVALID",
    "IDENTITY_PREIMAGE_MISMATCH",
    "IDENTITY_SELF_REFERENCE",
    "DATASET_HASH_INVALID",
    "DATASET_INTERVAL_ORDER_INVALID",
    "DATASET_GRID_INVALID",
    "DATASET_ROWS_INCONSISTENT",
    "DATASET_ROWS_EXCEED_GRID",
    "DATASET_END_INVALID",
    "COVERAGE_MODE_UNKNOWN",
    "COVERAGE_CONTRACT_INCOMPLETE",
    "COVERAGE_MODE_FIELD_MISMATCH",
    "UTC_MS_INVALID",
    "UTC_MS_OUT_OF_RANGE",
    "STATIC_TIMING_POLICY_INVALID",
    "STATIC_TIMING_RULE_UNKNOWN",
    "STATIC_TIMING_COMBINATION_INVALID",
    "SAME_BAR_CLOSE_EXECUTION_FORBIDDEN",
    "TIMING_MONOTONICITY_VIOLATION",
    "FILL_PRECEDES_ORDER_ELIGIBILITY",
    "BOUNDARY_REVERSED",
    "BOUNDARY_EMPTY",
    "BOUNDARY_OVERLAP",
    "BOUNDARY_ORDER_INVALID",
    "BOUNDARY_NOT_ALIGNED",
    "BOUNDARY_OUT_OF_DATASET",
    "TIMING_LEDGER_SCHEMA_INVALID",
    "CONTRACT_HASH_MISMATCH",
)


# ---------------------------------------------------------------------------
# Frozen check order -- exactly fourteen (R2 section 11.1; unchanged by R2A/R2B)
# ---------------------------------------------------------------------------

CHECK_IDS: Tuple[str, ...] = (
    "INPUT_ENVELOPE",
    "TYPE_DISCIPLINE",
    "RESEARCH_SCOPE",
    "DATASET_IDENTITY",
    "DATASET_COVERAGE",
    "STATIC_TIMING_FIELDS",
    "STATIC_TIMING_COMBINATION",
    "BOUNDARY_STRUCTURE",
    "BOUNDARY_ALIGNMENT",
    "EXPERIMENT_CONFIGURATION",
    "REGISTRATION_METADATA",
    "IDENTITY_PREIMAGE",
    "TIMING_LEDGER_SCHEMA",
    "CONTRACT_HASH",
)

CHECK_STATUSES: Tuple[str, ...] = ("PASS", "FAIL", "NOT_EVALUATED", "NOT_APPLICABLE")

# Index of BOUNDARY_ALIGNMENT -- the only step at which NOT_APPLICABLE may appear.
_ALIGNMENT_STEP_INDEX = CHECK_IDS.index("BOUNDARY_ALIGNMENT")


# ---------------------------------------------------------------------------
# Frozen execution invariants (R2B section A.3) -- order-bearing list
# ---------------------------------------------------------------------------

EXECUTION_INVARIANTS: Tuple[str, ...] = (
    "I1. order_eligibility_offset_ms >= signal_decision_offset_ms.",
    "I2. actual_fill_open_utc_ms >= order_eligibility_utc_ms.",
    "I3. the actual fill bar index is strictly greater than the signal bar index.",
    "I4. no fill may be backdated to a bar that had already opened when the order became eligible.",
    "I5. fill_bar_offset_lower_bound == 1 is a LOWER BOUND ONLY. It is not a claim that an accepted policy"
    " fills exactly one bar after the signal bar; the actual fill bar is whichever observed valid bar opens"
    " first at or after order eligibility, and may be later.",
)


# ---------------------------------------------------------------------------
# Frozen limitations text (R2B section C.5) -- order-bearing, status-independent
# ---------------------------------------------------------------------------

LIMITATIONS: Tuple[str, ...] = (
    "WP5-0A1 attests schema validity, identity derivation, boundary structure and static timing policy"
    " structure only.",
    "research_program_id is derived from the statistical scope only; program label, owner and registration"
    " timestamps are administrative metadata and never affect it. The Trial Counter is keyed by"
    " research_program_id and is therefore never reset by re-registration.",
    "Scope identity is byte identity, not semantic identity. Two scope records that canonicalize to identical"
    " bytes yield the same research_program_id; two records describing the same hypothesis under different"
    " wording or serialization yield different research_program_ids, and WP5-0A1 cannot detect that they are"
    " equivalent.",
    "Detecting near-duplicate or renamed research scopes requires registry comparison and human review in"
    " WP5-0A2. A new Trial Counter must never be granted merely because descriptive wording or opaque identity"
    " hashes changed.",
    "experiment_id is derived from the configuration only; registration timestamps and actors never affect it.",
    "It does NOT open, read or verify the dataset, the market calendar, or the coverage manifest."
    " dataset_hash, calendar_hash and coverage_manifest_hash are caller-asserted opaque identities.",
    "Uniform-grid arithmetic -- interval modulo checks, exact and maximum row counts, and boundary edge"
    " alignment -- is applied ONLY under CONTINUOUS_24_7. Under SESSION_CALENDAR and SPARSE_DECLARED it is not"
    " applied and not claimed.",
    "Under SESSION_CALENDAR and SPARSE_DECLARED, WP5-0A1 verifies the identity binding only. Session"
    " membership, holidays, half-days, DST transitions, missing observations, boundary-edge membership and"
    " row-count plausibility require the calendar-aware / coverage-aware runtime validator in the next work"
    " package.",
    "dataset_end_exclusive_ms is supplied by the dataset builder and is bound into dataset_identity_hash and"
    " experiment_id. Under CONTINUOUS_24_7 it is verified against the already-verified uniform grid. Under"
    " SESSION_CALENDAR and SPARSE_DECLARED it is a caller assertion that WP5-0A1 has bound but NOT verified"
    " against a market calendar; verifying it against the actual close of the last session bar is calendar and"
    " manifest validation in the next work package.",
    "The end of a non-continuous dataset is NEVER inferred from timeframe_ms or bar_duration_ms. A nominal bar"
    " label does not prove when the last observed bar closed.",
    "Execution is contracted against order eligibility: the earliest permissible fill is the open of the FIRST"
    " observed valid bar at or after order eligibility, where order eligibility is the signal decision instant"
    " plus the declared order submission latency. A fill on the signal bar is forbidden, and a fill may never"
    " be assigned to a bar open that occurred before order eligibility.",
    "fill_bar_offset_lower_bound = 1 is a lower bound only. It does not assert that an accepted policy fills"
    " one bar after the signal bar; settlement lag, signal delay or submission latency may place the first"
    " eligible bar open later.",
    "Millisecond submission and fill offsets are NOMINAL and are derived only under CONTINUOUS_24_7. A nominal"
    " grid timestamp is not an actual tradable session open. Under SESSION_CALENDAR and SPARSE_DECLARED both"
    " the actual fill bar and the actual UTC fill timestamp are unresolved here.",
    "The five execution invariants emitted in execution_invariants are a contract on the coverage-aware"
    " runtime validator. WP5-0A1 verifies only their static and nominal forms; it reads no data and verifies"
    " no absolute timestamp.",
    "It does NOT establish runtime no-look-ahead compliance; static offsets are a policy, not evidence."
    " Runtime compliance is established only by validating actual timing-ledger rows (WP5-0A2).",
    "A permitted timing combination is an internally consistent policy, not evidence that the described"
    " execution is achievable.",
    "It does NOT count trials, reserve trials, or define trial_id.",
    "It does NOT evaluate performance, statistics, robustness, out-of-sample success, production readiness or"
    " profitability.",
    "A VALID contract is a precondition for later Phase 5 work, never an approval of a strategy.",
)


# ---------------------------------------------------------------------------
# Frozen closed enumerations
# ---------------------------------------------------------------------------

COVERAGE_MODES: Tuple[str, ...] = ("CONTINUOUS_24_7", "SESSION_CALENDAR", "SPARSE_DECLARED")

FEATURE_AVAILABILITY_RULES: Tuple[str, ...] = ("AT_BAR_CLOSE", "AT_BAR_CLOSE_PLUS_SETTLEMENT")

_FIRST_AT_ELIG = "FIRST_OBSERVED_VALID_BAR_OPEN_AT_OR_AFTER_ORDER_ELIGIBILITY"

ORDER_SUBMISSION_RULES: Tuple[str, ...] = ("AT_ORDER_ELIGIBILITY", _FIRST_AT_ELIG)

FILL_RULES: Tuple[str, ...] = (_FIRST_AT_ELIG,)

FORBIDDEN_FILL_TOKEN = "SAME_BAR_CLOSE"

COVERAGE_VERIFICATION_STATUSES: Tuple[str, ...] = (
    "GRID_ARITHMETIC_VERIFIED",
    "IDENTITY_BOUND_ONLY_MEMBERSHIP_DEFERRED",
)

ALIGNMENT_VERIFICATION_STATUSES: Tuple[str, ...] = (
    "GRID_ALIGNMENT_VERIFIED",
    "ALIGNMENT_DEFERRED_TO_COVERAGE_VALIDATOR",
)

DATASET_END_VERIFICATION_STATUSES: Tuple[str, ...] = (
    "GRID_DERIVED_END_VERIFIED",
    "CALLER_ASSERTED_END_UNVERIFIED",
)

FILL_RESOLUTION_STATUSES: Tuple[str, ...] = (
    "NOMINAL_GRID_RESOLVED",
    "DEFERRED_TO_COVERAGE_VALIDATOR",
)


# ---------------------------------------------------------------------------
# Frozen structural input limit (Hardening R2 finding 1)
# ---------------------------------------------------------------------------

# The single frozen structural bound on an incoming request.  Semantics, frozen:
#
#   * Depth is defined on container nodes only -- mappings and lists.  Scalar
#     leaves (str, int, bool, float, None, and any other non-container object)
#     do not increase depth.
#   * The root request is depth 0.
#   * A container nested directly inside a container of depth d has depth d + 1.
#   * A container depth of at most MAX_INPUT_NESTING_DEPTH is permitted.
#   * A container depth greater than MAX_INPUT_NESTING_DEPTH is INVALID.
#   * A reference cycle at any depth is INVALID.  A cycle means an object whose
#     identity is already on the *active ancestor path*; an object merely
#     referenced from two separate, already-completed branches is not a cycle
#     and is not rejected.
#   * Both failures produce reason_code TYPE_INVALID at check INPUT_ENVELOPE.
#   * The validator never raises RecursionError.
MAX_INPUT_NESTING_DEPTH = 64

# The frozen bound on total traversal work (Hardening R3).  MAX_INPUT_NESTING_DEPTH
# bounds how *deep* a request may be; it does not bound how *much* of it there is.
# A shallow acyclic directed acyclic graph that references the same child object
# repeatedly at every level stays well inside the depth bound while expanding into
# exponentially many node occurrences across the preflight, the forbidden-key scan,
# the non-string-key scan, the type walk and canonical JSON construction.  That is a
# resource-exhaustion path, and an unbounded resource path is not fail-closed.  This
# second bound closes it.  Semantics, frozen:
#
#   * The root request counts as one visit.
#   * Every mapping value occurrence counts as one visit.
#   * Every list element occurrence counts as one visit.
#   * A repeated reference counts once for each occurrence.  Identity is not
#     deduplicated: the same object reached twice costs two visits.
#   * Mapping keys are not visits.  Keys remain governed solely by the existing
#     string-key checks.
#   * Exactly MAX_INPUT_NODE_VISITS visits is permitted.
#   * More than MAX_INPUT_NODE_VISITS visits is INVALID.
#   * The failure produces reason_code TYPE_INVALID at check INPUT_ENVELOPE, the
#     same result as the depth and cycle failures.
#   * The validator never raises MemoryError or RecursionError.
#   * The request is never mutated.
#
# The budget is enforced by the iterative structural preflight, before any recursive
# traversal and before any canonical JSON construction.  It is enforced *ahead* of
# the work, not after it: a node's immediate child count is tested against the
# remaining budget before those children are materialised or pushed, so the stack
# never grows past the frozen bound even for a single enormous container.
MAX_INPUT_NODE_VISITS = 100000


# ---------------------------------------------------------------------------
# Frozen input schema (R2 section 5, amended by R2A / R2B)
# ---------------------------------------------------------------------------

TOP_LEVEL_KEYS: Tuple[str, ...] = (
    "schema_version",
    "research_scope",
    "research_program_registration",
    "dataset_identity",
    "static_timing_policy",
    "boundaries",
    "experiment_configuration",
    "preregistration",
    "expected_identities",
    "timing_ledger_schema_version",
)

_RESEARCH_SCOPE_KEYS = (
    "schema_version",
    "hypothesis_family_hash",
    "strategy_family_hash",
    "asset_universe_hash",
    "target_definition_hash",
    "evaluation_objective_hash",
)

_REGISTRATION_INPUT_KEYS = ("schema_version", "program_label", "owner", "created_utc_ms")

_DATASET_IDENTITY_REQUIRED_KEYS = (
    "schema_version",
    "dataset_hash",
    "timeframe_ms",
    "first_open_ms",
    "last_open_ms",
    "dataset_end_exclusive_ms",
    "rows",
    "coverage_mode",
)

_DATASET_IDENTITY_MODE_KEYS = ("calendar_id", "calendar_hash", "coverage_manifest_hash")

# Fields whose declared type is a string and whose datatype is therefore settled
# at step 2 (TYPE_DISCIPLINE), before any mode-conditional coverage test.
# Hardening R1 ruling 3.
_DECLARED_STRING_FIELDS: Tuple[Tuple[str, str], ...] = (("dataset_identity", "calendar_id"),)

_STATIC_TIMING_POLICY_KEYS = (
    "schema_version",
    "bar_duration_ms",
    "settlement_lag_ms",
    "feature_availability_rule",
    "signal_delay_ms",
    "order_submission_latency_ms",
    "order_submission_rule",
    "fill_rule",
)

_BOUNDARIES_KEYS = ("schema_version", "train", "val", "holdout")
_INTERVAL_KEYS = ("start_ms", "end_ms")
_INTERVAL_NAMES = ("train", "val", "holdout")

_EXPERIMENT_CONFIGURATION_INPUT_KEYS = ("schema_version", "strategy_id", "param_hash", "method_hash")

_PREREGISTRATION_INPUT_KEYS = (
    "schema_version",
    "registered_utc_ms",
    "registered_by",
    "registration_reference",
)

_EXPECTED_IDENTITY_KEYS = ("research_program_id", "experiment_id")


# ---------------------------------------------------------------------------
# Frozen out-of-scope key vocabularies (R2 section 7)
# ---------------------------------------------------------------------------

TRIAL_KEYS: Tuple[str, ...] = ("trial_id", "trial_id_record")

COUNTER_KEYS: Tuple[str, ...] = (
    "attempt_fingerprint",
    "attempt_fingerprint_record",
    "reservation_sequence",
    "reservation_utc_ms",
    "idempotency_key",
    "verification_replay_id",
    "N",
    "N_total",
    "trial_outcome",
    "timing_ledger_rows",
)

_COUNTER_KEY_PREFIXES: Tuple[str, ...] = ("ledger_",)

OUT_OF_SCOPE_KEYS: Tuple[str, ...] = (
    "psr",
    "dsr",
    "sr_values",
    "var_sr_trials",
    "holdout_access_state",
)

_OUT_OF_SCOPE_KEY_PREFIXES: Tuple[str, ...] = ("mc_", "cpcv_", "rag_")

ADMIN_KEYS: Tuple[str, ...] = (
    "program_label",
    "owner",
    "created_utc_ms",
    "registered_utc_ms",
    "registered_by",
    "registration_reference",
)

_ADMIN_KEY_SUFFIXES: Tuple[str, ...] = ("_label", "_actor", "_note")

_IDENTITY_PREIMAGE_ROOTS: Tuple[str, ...] = ("research_scope", "experiment_configuration")

_DERIVED_RECORD_HASH_KEYS: Tuple[str, ...] = (
    "registration_record_hash",
    "preregistration_record_hash",
)


# ---------------------------------------------------------------------------
# Frozen patterns and numeric bounds
# ---------------------------------------------------------------------------

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_PROGRAM_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,127}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
_STRATEGY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
_REGISTRATION_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/#-]{1,127}$")
_CALENDAR_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")

UTC_MS_MIN = 946684800000
UTC_MS_MAX = 4102444800000

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_PLACEHOLDER_HASHES = ("0" * 64, "f" * 64, _EMPTY_SHA256)


class _Halt(Exception):
    """Internal control-flow signal carrying a single frozen reason code."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _fail(reason_code: str) -> "_Halt":
    return _Halt(reason_code)


# ---------------------------------------------------------------------------
# Canonicalisation and digests (re-implemented locally; no Phase 4 import)
# ---------------------------------------------------------------------------


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canonical_bytes(obj)).hexdigest()


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX64_RE.match(value))


def _is_bound_hash(value: Any) -> bool:
    return _is_hex64(value) and value not in _PLACEHOLDER_HASHES


def _nominal_first_grid_open_at_or_after(x: int, bar_duration_ms: int) -> int:
    """Integer ceiling to the uniform grid.  CONTINUOUS_24_7 ONLY (R2B section A.7)."""
    return ((x + bar_duration_ms - 1) // bar_duration_ms) * bar_duration_ms


def _require_keys(node: Dict[str, Any], required: Tuple[str, ...], optional: Tuple[str, ...] = ()) -> None:
    # A non-string mapping key is not representable in canonical JSON at all, so
    # the mapping cannot be interpreted as a record.  It is refused before any
    # field-level test, and never by sorting mixed key types (which would raise).
    for key in node.keys():
        if not isinstance(key, str):
            raise _fail("TYPE_INVALID")
    for key in required:
        if key not in node:
            raise _fail("MISSING_FIELD")
    allowed = set(required) | set(optional)
    for key in sorted(node.keys()):
        if key not in allowed:
            raise _fail("UNKNOWN_FIELD_PRESENT")


def _require_dict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail("TYPE_INVALID")
    return value


def _absolute_utc_ms(value: Any) -> int:
    """Five-step UTC-ms validation for absolute timestamps (R2 section 8)."""
    if isinstance(value, bool):
        raise _fail("BOOL_NOT_ACCEPTED_AS_INT")
    if not isinstance(value, int):
        raise _fail("TYPE_INVALID")
    if value < UTC_MS_MIN or value > UTC_MS_MAX:
        raise _fail("UTC_MS_OUT_OF_RANGE")
    return value


# ---------------------------------------------------------------------------
# Step 1 -- INPUT_ENVELOPE
# ---------------------------------------------------------------------------

_SCAN_PRIORITY: Tuple[str, ...] = (
    "TRIAL_ID_OUT_OF_SCOPE",
    "COUNTER_FIELD_OUT_OF_SCOPE",
    "OUT_OF_SCOPE_FIELD",
    "ADMIN_METADATA_IN_IDENTITY_PREIMAGE",
    "IDENTITY_SELF_REFERENCE",
)


def _classify_key(key: str, path: Tuple[Any, ...]) -> Optional[str]:
    if key in TRIAL_KEYS:
        return "TRIAL_ID_OUT_OF_SCOPE"
    if key in COUNTER_KEYS or any(key.startswith(p) for p in _COUNTER_KEY_PREFIXES):
        return "COUNTER_FIELD_OUT_OF_SCOPE"
    if key in OUT_OF_SCOPE_KEYS or any(key.startswith(p) for p in _OUT_OF_SCOPE_KEY_PREFIXES):
        return "OUT_OF_SCOPE_FIELD"
    inside_identity_preimage = len(path) >= 1 and path[0] in _IDENTITY_PREIMAGE_ROOTS
    if inside_identity_preimage:
        if key in ADMIN_KEYS or any(key.endswith(s) for s in _ADMIN_KEY_SUFFIXES):
            return "ADMIN_METADATA_IN_IDENTITY_PREIMAGE"
        if key in _DERIVED_RECORD_HASH_KEYS:
            return "IDENTITY_SELF_REFERENCE"
    if key == "contract_hash" and len(path) >= 1:
        return "IDENTITY_SELF_REFERENCE"
    return None


def _structural_preflight(request: Any) -> None:
    """Bound the request structurally before any recursive traversal can recurse.

    Hardening R2 finding 1.  ``_scan_forbidden_keys``, ``_reject_non_string_keys``
    and the step 2 type walk are all recursive.  Run on a cyclic or arbitrarily
    deep object they exhaust the interpreter stack and raise ``RecursionError``,
    which would break the frozen guarantee that this validator is total and
    fail-closed.  This preflight runs first and refuses both shapes explicitly.

    The preflight itself is iterative -- an explicit stack, never recursion --
    so it cannot raise ``RecursionError`` on the very inputs it exists to refuse.

    Cycles are detected by object identity on the *active ancestor path* only.
    An exit marker is pushed beneath each node's children, so a node leaves the
    active set once its whole subtree is complete.  A shared object reachable
    through two separate completed branches is therefore traversed twice and
    accepted; only a genuine back-reference to an ancestor is refused.

    Hardening R3 adds the second frozen bound, ``MAX_INPUT_NODE_VISITS``.  Depth
    alone does not bound total work: a shallow acyclic DAG that reuses the same
    child object at every level expands exponentially while never exceeding depth
    64 and never containing a cycle.  Every occurrence is counted -- shared objects
    are deliberately *not* deduplicated, because the downstream recursive walks and
    canonical JSON construction will themselves re-traverse each occurrence, so the
    occurrence count is the honest measure of the work being authorised.

    The budget is charged *before* the corresponding work is scheduled.  A node's
    immediate child count is compared against the remaining budget before those
    children are materialised or pushed, so neither the visit counter nor the
    explicit stack can grow past the frozen bound -- not even for one container
    holding an enormous number of elements.

    Raises ``_Halt('TYPE_INVALID')`` for a cycle, for a container deeper than
    ``MAX_INPUT_NESTING_DEPTH``, or for a request whose node-visit count exceeds
    ``MAX_INPUT_NODE_VISITS``.  Returns ``None`` otherwise.  Does not mutate the
    request.
    """
    # Rule 1: the root request counts as one visit.  A scalar root is one visit and
    # has no children, so it is trivially inside both bounds.
    if not isinstance(request, (dict, list)):
        return

    visits = 1

    active: Dict[int, bool] = {}
    # Stack entries are (node, depth, is_exit_marker).
    stack: List[Tuple[Any, int, bool]] = [(request, 0, False)]

    while stack:
        node, depth, is_exit_marker = stack.pop()

        if is_exit_marker:
            active.pop(id(node), None)
            continue

        if id(node) in active:
            raise _fail("TYPE_INVALID")
        if depth > MAX_INPUT_NESTING_DEPTH:
            raise _fail("TYPE_INVALID")

        # Charge this node's children against the budget *before* touching them.
        # ``len`` is O(1) on both dict and list, so the immediate child count is
        # known without materialising anything.  Every occurrence is charged --
        # scalars included -- and the remaining budget is expressed as a
        # subtraction so no oversized quantity is ever constructed.
        child_count = len(node)
        if child_count > MAX_INPUT_NODE_VISITS - visits:
            raise _fail("TYPE_INVALID")
        visits += child_count

        active[id(node)] = True
        stack.append((node, depth, True))

        if isinstance(node, dict):
            children: List[Any] = list(node.values())
        else:
            children = list(node)
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1, False))


def _scan_forbidden_keys(request: Any) -> Optional[str]:
    found: Dict[str, bool] = {}

    def walk(node: Any, path: Tuple[Any, ...]) -> None:
        if isinstance(node, dict):
            for key in sorted(k for k in node.keys() if isinstance(k, str)):
                classification = _classify_key(key, path)
                if classification is not None:
                    found[classification] = True
                walk(node[key], path + (key,))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, path + (index,))

    walk(request, ())
    for classification in _SCAN_PRIORITY:
        if classification in found:
            return classification
    return None


def _reject_non_string_keys(node: Any) -> None:
    """Refuse any mapping carrying a non-string key, at any depth.

    Hardening R1 ruling 4 (interpretation I-7, approved): such a mapping is not
    representable in canonical JSON at all, so it cannot be interpreted as a
    record.  It is refused at step 1 (``INPUT_ENVELOPE``) with ``TYPE_INVALID``,
    before ``MISSING_FIELD`` and ``UNKNOWN_FIELD_PRESENT``, and never by sorting
    mixed key types (which would raise ``TypeError``).
    """
    if isinstance(node, dict):
        for key in node.keys():
            if not isinstance(key, str):
                raise _fail("TYPE_INVALID")
        for key in sorted(node.keys()):
            _reject_non_string_keys(node[key])
    elif isinstance(node, list):
        for item in node:
            _reject_non_string_keys(item)


def _check_input_envelope(request: Any, ctx: Dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise _fail("TYPE_INVALID")

    # Hardening R2 finding 1: the structural bound must be established before any
    # recursive traversal below can recurse.  After this call every later walk is
    # guaranteed bounded and acyclic.
    _structural_preflight(request)

    classification = _scan_forbidden_keys(request)
    if classification is not None:
        raise _fail(classification)

    _reject_non_string_keys(request)

    if "schema_version" not in request:
        raise _fail("MISSING_FIELD")
    if request["schema_version"] != REQUEST_SCHEMA_VERSION:
        raise _fail("SCHEMA_VERSION_UNSUPPORTED")

    _require_keys(request, TOP_LEVEL_KEYS)

    expected_versions = (
        ("research_scope", RESEARCH_SCOPE_SCHEMA_VERSION),
        ("research_program_registration", REGISTRATION_SCHEMA_VERSION),
        ("dataset_identity", DATASET_IDENTITY_SCHEMA_VERSION),
        ("static_timing_policy", STATIC_TIMING_POLICY_SCHEMA_VERSION),
        ("boundaries", BOUNDARIES_SCHEMA_VERSION),
        ("experiment_configuration", EXPERIMENT_CONFIGURATION_SCHEMA_VERSION),
        ("preregistration", PREREGISTRATION_SCHEMA_VERSION),
    )
    for name, expected in expected_versions:
        node = _require_dict(request[name])
        if "schema_version" not in node:
            raise _fail("MISSING_FIELD")
        if node["schema_version"] != expected:
            raise _fail("SCHEMA_VERSION_UNSUPPORTED")

    _require_dict(request["expected_identities"])
    ctx["request"] = request


# ---------------------------------------------------------------------------
# Step 2 -- TYPE_DISCIPLINE
# ---------------------------------------------------------------------------


def _check_type_discipline(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    def walk(node: Any) -> None:
        if node is None:
            raise _fail("NULL_NOT_PERMITTED")
        if isinstance(node, bool):
            raise _fail("BOOL_NOT_ACCEPTED_AS_INT")
        if isinstance(node, float):
            raise _fail("FLOAT_NOT_PERMITTED")
        if isinstance(node, int):
            return
        if isinstance(node, str):
            for character in node:
                code_point = ord(character)
                if code_point <= 0x1F or code_point == 0x7F:
                    raise _fail("TYPE_INVALID")
            if unicodedata.normalize("NFC", node) != node:
                raise _fail("STRING_NOT_NFC")
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            for key in node.keys():
                if not isinstance(key, str):
                    raise _fail("TYPE_INVALID")
            for key in sorted(node.keys()):
                walk(key)
                walk(node[key])
            return
        raise _fail("TYPE_INVALID")

    walk(request)

    # Hardening R1 ruling 3: a declared-string field carrying a wrong datatype is
    # a type-discipline defect (TYPE_INVALID at step 2), never a coverage defect
    # at step 5.  ``None``, ``bool`` and ``float`` keep their own frozen codes,
    # which ``walk`` has already raised above.
    for block, field in _DECLARED_STRING_FIELDS:
        node = request.get(block)
        if isinstance(node, dict) and field in node and not isinstance(node[field], str):
            raise _fail("TYPE_INVALID")


# ---------------------------------------------------------------------------
# Step 3 -- RESEARCH_SCOPE
# ---------------------------------------------------------------------------


def _check_research_scope(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    scope = _require_dict(request["research_scope"])
    _require_keys(scope, _RESEARCH_SCOPE_KEYS)
    for key in _RESEARCH_SCOPE_KEYS[1:]:
        if not _is_bound_hash(scope[key]):
            raise _fail("RESEARCH_SCOPE_INVALID")
    ctx["research_scope_record"] = dict(scope)
    ctx["research_program_id"] = _digest(ctx["research_scope_record"])
    ctx["counter_key"] = ctx["research_program_id"]


# ---------------------------------------------------------------------------
# Step 4 -- DATASET_IDENTITY (structural only)
# ---------------------------------------------------------------------------


def _check_dataset_identity(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    dataset = _require_dict(request["dataset_identity"])
    _require_keys(dataset, _DATASET_IDENTITY_REQUIRED_KEYS, _DATASET_IDENTITY_MODE_KEYS)

    if not _is_bound_hash(dataset["dataset_hash"]):
        raise _fail("DATASET_HASH_INVALID")

    rows = dataset["rows"]
    if isinstance(rows, bool):
        raise _fail("BOOL_NOT_ACCEPTED_AS_INT")
    if not isinstance(rows, int) or rows < 1:
        raise _fail("TYPE_INVALID")

    timeframe_ms = dataset["timeframe_ms"]
    if isinstance(timeframe_ms, bool):
        raise _fail("BOOL_NOT_ACCEPTED_AS_INT")
    if not isinstance(timeframe_ms, int):
        raise _fail("TYPE_INVALID")
    if timeframe_ms <= 0:
        raise _fail("UTC_MS_INVALID")

    first_open_ms = _absolute_utc_ms(dataset["first_open_ms"])
    last_open_ms = _absolute_utc_ms(dataset["last_open_ms"])
    dataset_end_exclusive_ms = _absolute_utc_ms(dataset["dataset_end_exclusive_ms"])

    if last_open_ms < first_open_ms:
        raise _fail("DATASET_INTERVAL_ORDER_INVALID")
    if dataset_end_exclusive_ms <= last_open_ms:
        raise _fail("DATASET_END_INVALID")

    ctx["dataset_identity_record"] = dict(dataset)
    ctx["timeframe_ms"] = timeframe_ms
    ctx["rows"] = rows
    ctx["first_open_ms"] = first_open_ms
    ctx["last_open_ms"] = last_open_ms
    ctx["dataset_end_exclusive_ms"] = dataset_end_exclusive_ms


# ---------------------------------------------------------------------------
# Step 5 -- DATASET_COVERAGE (closed union + CONTINUOUS_24_7-only arithmetic)
# ---------------------------------------------------------------------------

_MODE_REQUIRED = {
    "CONTINUOUS_24_7": (),
    "SESSION_CALENDAR": ("calendar_id", "calendar_hash"),
    "SPARSE_DECLARED": ("coverage_manifest_hash",),
}


def _check_dataset_coverage(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    dataset = ctx["dataset_identity_record"]
    coverage_mode = dataset["coverage_mode"]
    if coverage_mode not in COVERAGE_MODES:
        raise _fail("COVERAGE_MODE_UNKNOWN")

    required = _MODE_REQUIRED[coverage_mode]
    for key in required:
        if key not in dataset:
            raise _fail("COVERAGE_CONTRACT_INCOMPLETE")
    for key in _DATASET_IDENTITY_MODE_KEYS:
        if key in dataset and key not in required:
            raise _fail("COVERAGE_MODE_FIELD_MISMATCH")

    if coverage_mode == "SESSION_CALENDAR":
        # Hardening R1 ruling 3: the datatype was settled at step 2.  A present
        # string that fails the frozen calendar_id syntax is a mode-field
        # mismatch, NOT an incomplete coverage contract (which is reserved for a
        # mode-required field that is absent).
        if not _CALENDAR_ID_RE.match(dataset["calendar_id"]):
            raise _fail("COVERAGE_MODE_FIELD_MISMATCH")
        if not _is_bound_hash(dataset["calendar_hash"]):
            raise _fail("DATASET_HASH_INVALID")
    elif coverage_mode == "SPARSE_DECLARED":
        if not _is_bound_hash(dataset["coverage_manifest_hash"]):
            raise _fail("DATASET_HASH_INVALID")

    timeframe_ms = ctx["timeframe_ms"]
    first_open_ms = ctx["first_open_ms"]
    last_open_ms = ctx["last_open_ms"]
    dataset_end_exclusive_ms = ctx["dataset_end_exclusive_ms"]
    rows = ctx["rows"]

    if coverage_mode == "CONTINUOUS_24_7":
        span = last_open_ms - first_open_ms
        if span % timeframe_ms != 0:
            raise _fail("DATASET_GRID_INVALID")
        slots = span // timeframe_ms + 1
        if rows > slots:
            raise _fail("DATASET_ROWS_EXCEED_GRID")
        if rows != slots:
            raise _fail("DATASET_ROWS_INCONSISTENT")
        if dataset_end_exclusive_ms != last_open_ms + timeframe_ms:
            raise _fail("DATASET_END_INVALID")
        ctx["coverage_verification_status"] = "GRID_ARITHMETIC_VERIFIED"
        ctx["dataset_end_verification_status"] = "GRID_DERIVED_END_VERIFIED"
    else:
        ctx["coverage_verification_status"] = "IDENTITY_BOUND_ONLY_MEMBERSHIP_DEFERRED"
        ctx["dataset_end_verification_status"] = "CALLER_ASSERTED_END_UNVERIFIED"

    ctx["coverage_mode"] = coverage_mode
    ctx["dataset_identity_hash"] = _digest(ctx["dataset_identity_record"])


# ---------------------------------------------------------------------------
# Step 6 -- STATIC_TIMING_FIELDS
# ---------------------------------------------------------------------------


def _check_static_timing_fields(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    policy = _require_dict(request["static_timing_policy"])

    for key in sorted(k for k in policy.keys() if isinstance(k, str)):
        if key.endswith("_utc_ms"):
            raise _fail("STATIC_TIMING_POLICY_INVALID")

    _require_keys(policy, _STATIC_TIMING_POLICY_KEYS)

    fill_rule = policy["fill_rule"]
    if fill_rule != FORBIDDEN_FILL_TOKEN and fill_rule not in FILL_RULES:
        raise _fail("STATIC_TIMING_RULE_UNKNOWN")
    if policy["feature_availability_rule"] not in FEATURE_AVAILABILITY_RULES:
        raise _fail("STATIC_TIMING_RULE_UNKNOWN")
    if policy["order_submission_rule"] not in ORDER_SUBMISSION_RULES:
        raise _fail("STATIC_TIMING_RULE_UNKNOWN")

    bar_duration_ms = policy["bar_duration_ms"]
    if isinstance(bar_duration_ms, bool):
        raise _fail("BOOL_NOT_ACCEPTED_AS_INT")
    if not isinstance(bar_duration_ms, int):
        raise _fail("TYPE_INVALID")
    if bar_duration_ms <= 0:
        raise _fail("UTC_MS_INVALID")
    if bar_duration_ms != ctx["timeframe_ms"]:
        raise _fail("STATIC_TIMING_POLICY_INVALID")

    for key in ("settlement_lag_ms", "signal_delay_ms"):
        value = policy[key]
        if isinstance(value, bool):
            raise _fail("BOOL_NOT_ACCEPTED_AS_INT")
        if not isinstance(value, int):
            raise _fail("TYPE_INVALID")
        if value < 0 or value > bar_duration_ms:
            raise _fail("UTC_MS_INVALID")

    latency = policy["order_submission_latency_ms"]
    if isinstance(latency, bool):
        raise _fail("BOOL_NOT_ACCEPTED_AS_INT")
    if not isinstance(latency, int):
        raise _fail("TYPE_INVALID")
    if latency < 0 or latency > bar_duration_ms:
        raise _fail("STATIC_TIMING_POLICY_INVALID")

    ctx["static_timing_policy_record"] = dict(policy)
    ctx["bar_duration_ms"] = bar_duration_ms
    ctx["settlement_lag_ms"] = policy["settlement_lag_ms"]
    ctx["signal_delay_ms"] = policy["signal_delay_ms"]
    ctx["order_submission_latency_ms"] = latency
    ctx["feature_availability_rule"] = policy["feature_availability_rule"]
    ctx["order_submission_rule"] = policy["order_submission_rule"]
    ctx["fill_rule"] = fill_rule


# ---------------------------------------------------------------------------
# Step 7 -- STATIC_TIMING_COMBINATION
# ---------------------------------------------------------------------------


def _check_static_timing_combination(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    bar_duration_ms = ctx["bar_duration_ms"]
    settlement_lag_ms = ctx["settlement_lag_ms"]
    signal_delay_ms = ctx["signal_delay_ms"]
    latency = ctx["order_submission_latency_ms"]
    feature_rule = ctx["feature_availability_rule"]
    submission_rule = ctx["order_submission_rule"]
    fill_rule = ctx["fill_rule"]

    # Rule F -- unconditional, before any offset arithmetic (R2B section A.5).
    if fill_rule == FORBIDDEN_FILL_TOKEN:
        raise _fail("SAME_BAR_CLOSE_EXECUTION_FORBIDDEN")

    # Rule A / Rule B.
    if feature_rule == "AT_BAR_CLOSE" and settlement_lag_ms != 0:
        raise _fail("STATIC_TIMING_COMBINATION_INVALID")
    if feature_rule == "AT_BAR_CLOSE_PLUS_SETTLEMENT" and settlement_lag_ms == 0:
        raise _fail("STATIC_TIMING_COMBINATION_INVALID")

    if feature_rule == "AT_BAR_CLOSE":
        bar_available_offset_ms = bar_duration_ms
    else:
        bar_available_offset_ms = bar_duration_ms + settlement_lag_ms
    signal_decision_offset_ms = bar_available_offset_ms + signal_delay_ms
    order_eligibility_offset_ms = signal_decision_offset_ms + latency

    # Rule H -- invariant I1 chain, computable in every coverage mode.
    if not (
        bar_duration_ms
        <= bar_available_offset_ms
        <= signal_decision_offset_ms
        <= order_eligibility_offset_ms
    ):
        raise _fail("TIMING_MONOTONICITY_VIOLATION")

    nominal_order_submission_offset_ms: Optional[int] = None
    nominal_earliest_fill_offset_ms: Optional[int] = None
    nominal_earliest_fill_bar_offset: Optional[int] = None
    nominal_submission_bar_offset: Optional[int] = None
    nominal_fill_simultaneity: Optional[str] = None

    if ctx["coverage_mode"] == "CONTINUOUS_24_7":
        fill_resolution_status = "NOMINAL_GRID_RESOLVED"
        if submission_rule == "AT_ORDER_ELIGIBILITY":
            nominal_order_submission_offset_ms = order_eligibility_offset_ms
        else:
            nominal_order_submission_offset_ms = _nominal_first_grid_open_at_or_after(
                order_eligibility_offset_ms, bar_duration_ms
            )
        nominal_earliest_fill_offset_ms = _nominal_first_grid_open_at_or_after(
            order_eligibility_offset_ms, bar_duration_ms
        )

        # Nominal I2 -- has its own code so backdating is never masked.
        if nominal_earliest_fill_offset_ms < order_eligibility_offset_ms:
            raise _fail("FILL_PRECEDES_ORDER_ELIGIBILITY")

        nominal_earliest_fill_bar_offset = nominal_earliest_fill_offset_ms // bar_duration_ms
        nominal_submission_bar_offset = nominal_order_submission_offset_ms // bar_duration_ms

        if not (
            order_eligibility_offset_ms
            <= nominal_order_submission_offset_ms
            <= nominal_earliest_fill_offset_ms
        ):
            raise _fail("TIMING_MONOTONICITY_VIOLATION")

        # Rule G -- nominal I3.
        if nominal_earliest_fill_bar_offset < 1:
            raise _fail("TIMING_MONOTONICITY_VIOLATION")

        nominal_fill_simultaneity = (
            "SIMULTANEOUS"
            if nominal_earliest_fill_offset_ms == nominal_order_submission_offset_ms
            else "STRICTLY_AFTER"
        )
    else:
        fill_resolution_status = "DEFERRED_TO_COVERAGE_VALIDATOR"

    ctx["derived_timing_offsets"] = {
        "signal_bar_offset": 0,
        "fill_bar_offset_lower_bound": 1,
        "fill_target": _FIRST_AT_ELIG,
        "submission_target": submission_rule,
        "bar_available_offset_ms": bar_available_offset_ms,
        "signal_decision_offset_ms": signal_decision_offset_ms,
        "order_eligibility_offset_ms": order_eligibility_offset_ms,
        "fill_resolution_status": fill_resolution_status,
        "nominal_order_submission_offset_ms": nominal_order_submission_offset_ms,
        "nominal_earliest_fill_offset_ms": nominal_earliest_fill_offset_ms,
        "nominal_earliest_fill_bar_offset": nominal_earliest_fill_bar_offset,
        "nominal_submission_bar_offset": nominal_submission_bar_offset,
        "nominal_fill_simultaneity": nominal_fill_simultaneity,
    }
    ctx["static_timing_policy_hash"] = _digest(ctx["static_timing_policy_record"])


# ---------------------------------------------------------------------------
# Step 8 -- BOUNDARY_STRUCTURE (truth-table rows 5-9, 14)
# ---------------------------------------------------------------------------


def _check_boundary_structure(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    boundaries = _require_dict(request["boundaries"])
    _require_keys(boundaries, _BOUNDARIES_KEYS)

    intervals: Dict[str, Dict[str, int]] = {}
    for name in _INTERVAL_NAMES:
        interval = _require_dict(boundaries[name])
        _require_keys(interval, _INTERVAL_KEYS)
        start_ms = _absolute_utc_ms(interval["start_ms"])
        end_ms = _absolute_utc_ms(interval["end_ms"])
        intervals[name] = {"start_ms": start_ms, "end_ms": end_ms}

    for name in _INTERVAL_NAMES:
        if intervals[name]["end_ms"] < intervals[name]["start_ms"]:
            raise _fail("BOUNDARY_REVERSED")
    for name in _INTERVAL_NAMES:
        if intervals[name]["end_ms"] == intervals[name]["start_ms"]:
            raise _fail("BOUNDARY_EMPTY")

    train, val, holdout = intervals["train"], intervals["val"], intervals["holdout"]

    if (
        val["start_ms"] < train["start_ms"]
        or holdout["start_ms"] < val["start_ms"]
        or holdout["end_ms"] < val["end_ms"]
    ):
        raise _fail("BOUNDARY_ORDER_INVALID")

    if train["end_ms"] > val["start_ms"] or val["end_ms"] > holdout["start_ms"]:
        raise _fail("BOUNDARY_OVERLAP")

    ctx["boundaries_record"] = {
        "schema_version": BOUNDARIES_SCHEMA_VERSION,
        "train": dict(train),
        "val": dict(val),
        "holdout": dict(holdout),
    }
    ctx["boundaries_hash"] = _digest(ctx["boundaries_record"])
    ctx["gap_train_val_ms"] = val["start_ms"] - train["end_ms"]
    ctx["gap_val_holdout_ms"] = holdout["start_ms"] - val["end_ms"]
    ctx["intervals"] = intervals


# ---------------------------------------------------------------------------
# Step 9 -- BOUNDARY_ALIGNMENT (rows 12a/12b) and containment (rows 13a/13b)
# ---------------------------------------------------------------------------


def _check_boundary_alignment(request: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    """Return True when the step is PASS, False when it is NOT_APPLICABLE."""
    intervals = ctx["intervals"]
    first_open_ms = ctx["first_open_ms"]
    timeframe_ms = ctx["timeframe_ms"]
    continuous = ctx["coverage_mode"] == "CONTINUOUS_24_7"

    if continuous:
        ctx["alignment_verification_status"] = "GRID_ALIGNMENT_VERIFIED"
        for name in _INTERVAL_NAMES:
            for edge in _INTERVAL_KEYS:
                if (intervals[name][edge] - first_open_ms) % timeframe_ms != 0:
                    raise _fail("BOUNDARY_NOT_ALIGNED")
    else:
        ctx["alignment_verification_status"] = "ALIGNMENT_DEFERRED_TO_COVERAGE_VALIDATOR"

    # Row 13a is evaluated before row 13b (R2B section B.4).
    if intervals["train"]["start_ms"] < first_open_ms:
        raise _fail("BOUNDARY_OUT_OF_DATASET")
    if intervals["holdout"]["end_ms"] > ctx["dataset_end_exclusive_ms"]:
        raise _fail("BOUNDARY_OUT_OF_DATASET")

    return continuous


# ---------------------------------------------------------------------------
# Step 10 -- EXPERIMENT_CONFIGURATION
# ---------------------------------------------------------------------------


def _check_experiment_configuration(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    configuration = _require_dict(request["experiment_configuration"])
    _require_keys(configuration, _EXPERIMENT_CONFIGURATION_INPUT_KEYS)

    strategy_id = configuration["strategy_id"]
    if not isinstance(strategy_id, str) or not _STRATEGY_ID_RE.match(strategy_id):
        raise _fail("EXPERIMENT_CONFIGURATION_INVALID")
    for key in ("param_hash", "method_hash"):
        if not _is_bound_hash(configuration[key]):
            raise _fail("EXPERIMENT_CONFIGURATION_INVALID")

    ctx["strategy_id"] = strategy_id
    ctx["param_hash"] = configuration["param_hash"]
    ctx["method_hash"] = configuration["method_hash"]


# ---------------------------------------------------------------------------
# Step 11 -- REGISTRATION_METADATA
# ---------------------------------------------------------------------------


def _check_registration_metadata(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    registration = _require_dict(request["research_program_registration"])
    _require_keys(registration, _REGISTRATION_INPUT_KEYS)

    program_label = registration["program_label"]
    if not isinstance(program_label, str) or not _PROGRAM_LABEL_RE.match(program_label):
        raise _fail("REGISTRATION_INVALID")
    owner = registration["owner"]
    if not isinstance(owner, str) or not _OWNER_RE.match(owner):
        raise _fail("REGISTRATION_INVALID")
    created_utc_ms = _absolute_utc_ms(registration["created_utc_ms"])

    preregistration = _require_dict(request["preregistration"])
    _require_keys(preregistration, _PREREGISTRATION_INPUT_KEYS)

    registered_by = preregistration["registered_by"]
    if not isinstance(registered_by, str) or not _OWNER_RE.match(registered_by):
        raise _fail("PREREGISTRATION_INVALID")
    registration_reference = preregistration["registration_reference"]
    if not isinstance(registration_reference, str) or not _REGISTRATION_REFERENCE_RE.match(
        registration_reference
    ):
        raise _fail("PREREGISTRATION_INVALID")
    registered_utc_ms = _absolute_utc_ms(preregistration["registered_utc_ms"])

    ctx["program_label"] = program_label
    ctx["owner"] = owner
    ctx["created_utc_ms"] = created_utc_ms
    ctx["registered_by"] = registered_by
    ctx["registration_reference"] = registration_reference
    ctx["registered_utc_ms"] = registered_utc_ms


# ---------------------------------------------------------------------------
# Step 12 -- IDENTITY_PREIMAGE
# ---------------------------------------------------------------------------


def _check_identity_preimage(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    experiment_configuration_record = {
        "schema_version": EXPERIMENT_CONFIGURATION_SCHEMA_VERSION,
        "research_program_id": ctx["research_program_id"],
        "dataset_identity_hash": ctx["dataset_identity_hash"],
        "boundaries_hash": ctx["boundaries_hash"],
        "static_timing_policy_hash": ctx["static_timing_policy_hash"],
        "strategy_id": ctx["strategy_id"],
        "param_hash": ctx["param_hash"],
        "method_hash": ctx["method_hash"],
    }
    experiment_id = _digest(experiment_configuration_record)

    registration_record = {
        "schema_version": REGISTRATION_SCHEMA_VERSION,
        "research_program_id": ctx["research_program_id"],
        "program_label": ctx["program_label"],
        "owner": ctx["owner"],
        "created_utc_ms": ctx["created_utc_ms"],
    }
    preregistration_record = {
        "schema_version": PREREGISTRATION_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "registered_utc_ms": ctx["registered_utc_ms"],
        "registered_by": ctx["registered_by"],
        "registration_reference": ctx["registration_reference"],
    }

    ctx["experiment_configuration_record"] = experiment_configuration_record
    ctx["experiment_id"] = experiment_id
    ctx["experiment_configuration_hash"] = experiment_id
    ctx["registration_record"] = registration_record
    ctx["registration_record_hash"] = _digest(registration_record)
    ctx["preregistration_record"] = preregistration_record
    ctx["preregistration_record_hash"] = _digest(preregistration_record)

    expected = _require_dict(request["expected_identities"])
    _require_keys(expected, (), _EXPECTED_IDENTITY_KEYS)
    if "research_program_id" in expected and expected["research_program_id"] != ctx["research_program_id"]:
        raise _fail("IDENTITY_PREIMAGE_MISMATCH")
    if "experiment_id" in expected and expected["experiment_id"] != experiment_id:
        raise _fail("IDENTITY_PREIMAGE_MISMATCH")


# ---------------------------------------------------------------------------
# Step 13 -- TIMING_LEDGER_SCHEMA
# ---------------------------------------------------------------------------


def _check_timing_ledger_schema(request: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    if request["timing_ledger_schema_version"] != TIMING_LEDGER_SCHEMA_VERSION:
        raise _fail("TIMING_LEDGER_SCHEMA_INVALID")
    ctx["timing_ledger_schema_version"] = TIMING_LEDGER_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Output body construction
# ---------------------------------------------------------------------------


def _checks_block(statuses: List[str]) -> List[Dict[str, str]]:
    return [
        {"check_id": check_id, "status": status} for check_id, status in zip(CHECK_IDS, statuses)
    ]


def _build_body(ctx: Dict[str, Any], statuses: List[str], reason_code: Optional[str]) -> Dict[str, Any]:
    """Primary body builder (R2 section 15 step 1)."""
    valid = reason_code is None
    body: Dict[str, Any] = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contract_status": "VALID" if valid else "INVALID",
        "reason_code": reason_code,
        "research_program_id": ctx["research_program_id"] if valid else None,
        "experiment_id": ctx["experiment_id"] if valid else None,
        "counter_key": ctx["counter_key"] if valid else None,
    }
    if valid:
        dataset_identity = dict(ctx["dataset_identity_record"])
        dataset_identity["dataset_identity_hash"] = ctx["dataset_identity_hash"]
        dataset_identity["coverage_verification_status"] = ctx["coverage_verification_status"]
        dataset_identity["dataset_end_verification_status"] = ctx["dataset_end_verification_status"]

        static_timing_policy = dict(ctx["static_timing_policy_record"])
        static_timing_policy["static_timing_policy_hash"] = ctx["static_timing_policy_hash"]

        boundaries = dict(ctx["boundaries_record"])
        boundaries["boundaries_hash"] = ctx["boundaries_hash"]
        boundaries["gap_train_val_ms"] = ctx["gap_train_val_ms"]
        boundaries["gap_val_holdout_ms"] = ctx["gap_val_holdout_ms"]
        boundaries["alignment_verification_status"] = ctx["alignment_verification_status"]

        experiment_configuration = dict(ctx["experiment_configuration_record"])
        experiment_configuration["experiment_configuration_hash"] = ctx["experiment_configuration_hash"]

        research_program_registration = dict(ctx["registration_record"])
        research_program_registration["registration_record_hash"] = ctx["registration_record_hash"]

        preregistration = dict(ctx["preregistration_record"])
        preregistration["preregistration_record_hash"] = ctx["preregistration_record_hash"]

        body["research_scope"] = dict(ctx["research_scope_record"])
        body["research_program_registration"] = research_program_registration
        body["dataset_identity"] = dataset_identity
        body["static_timing_policy"] = static_timing_policy
        body["derived_timing_offsets"] = dict(ctx["derived_timing_offsets"])
        body["boundaries"] = boundaries
        body["experiment_configuration"] = experiment_configuration
        body["preregistration"] = preregistration
        body["timing_ledger_schema_version"] = ctx["timing_ledger_schema_version"]

    body["checks"] = _checks_block(statuses)
    body["execution_invariants"] = list(EXECUTION_INVARIANTS)
    body["limitations"] = list(LIMITATIONS)
    return body


def _rebuild_body(ctx: Dict[str, Any], statuses: List[str], reason_code: Optional[str]) -> Dict[str, Any]:
    """Independent second builder (R2 section 15 step 3).

    Deliberately written as a separate construction path -- it does not call
    ``_build_body`` and does not copy its result.  Any accidental dependence on
    iteration order, mutation between passes, or impurity in the derivation
    surfaces as ``CONTRACT_HASH_MISMATCH``.
    """
    valid = reason_code is None
    parts: List[Tuple[str, Any]] = []
    parts.append(("schema_version", CONTRACT_SCHEMA_VERSION))
    parts.append(("contract_status", "VALID" if valid else "INVALID"))
    parts.append(("reason_code", reason_code))
    parts.append(("research_program_id", ctx["research_program_id"] if valid else None))
    parts.append(("experiment_id", ctx["experiment_id"] if valid else None))
    parts.append(("counter_key", ctx["counter_key"] if valid else None))

    if valid:
        scope_record: Dict[str, Any] = {}
        for key in sorted(ctx["research_scope_record"]):
            scope_record[key] = ctx["research_scope_record"][key]
        parts.append(("research_scope", scope_record))

        registration: Dict[str, Any] = {}
        for key in sorted(ctx["registration_record"]):
            registration[key] = ctx["registration_record"][key]
        registration["registration_record_hash"] = ctx["registration_record_hash"]
        parts.append(("research_program_registration", registration))

        dataset: Dict[str, Any] = {}
        for key in sorted(ctx["dataset_identity_record"]):
            dataset[key] = ctx["dataset_identity_record"][key]
        dataset["dataset_identity_hash"] = ctx["dataset_identity_hash"]
        dataset["coverage_verification_status"] = ctx["coverage_verification_status"]
        dataset["dataset_end_verification_status"] = ctx["dataset_end_verification_status"]
        parts.append(("dataset_identity", dataset))

        policy: Dict[str, Any] = {}
        for key in sorted(ctx["static_timing_policy_record"]):
            policy[key] = ctx["static_timing_policy_record"][key]
        policy["static_timing_policy_hash"] = ctx["static_timing_policy_hash"]
        parts.append(("static_timing_policy", policy))

        offsets: Dict[str, Any] = {}
        for key in sorted(ctx["derived_timing_offsets"]):
            offsets[key] = ctx["derived_timing_offsets"][key]
        parts.append(("derived_timing_offsets", offsets))

        boundaries: Dict[str, Any] = {
            "schema_version": BOUNDARIES_SCHEMA_VERSION,
        }
        for name in _INTERVAL_NAMES:
            boundaries[name] = {
                "start_ms": ctx["intervals"][name]["start_ms"],
                "end_ms": ctx["intervals"][name]["end_ms"],
            }
        boundaries["boundaries_hash"] = ctx["boundaries_hash"]
        boundaries["gap_train_val_ms"] = ctx["gap_train_val_ms"]
        boundaries["gap_val_holdout_ms"] = ctx["gap_val_holdout_ms"]
        boundaries["alignment_verification_status"] = ctx["alignment_verification_status"]
        parts.append(("boundaries", boundaries))

        configuration: Dict[str, Any] = {}
        for key in sorted(ctx["experiment_configuration_record"]):
            configuration[key] = ctx["experiment_configuration_record"][key]
        configuration["experiment_configuration_hash"] = ctx["experiment_configuration_hash"]
        parts.append(("experiment_configuration", configuration))

        prereg: Dict[str, Any] = {}
        for key in sorted(ctx["preregistration_record"]):
            prereg[key] = ctx["preregistration_record"][key]
        prereg["preregistration_record_hash"] = ctx["preregistration_record_hash"]
        parts.append(("preregistration", prereg))

        parts.append(("timing_ledger_schema_version", ctx["timing_ledger_schema_version"]))

    checks: List[Dict[str, str]] = []
    for index in range(len(CHECK_IDS)):
        checks.append({"check_id": CHECK_IDS[index], "status": statuses[index]})
    parts.append(("checks", checks))
    parts.append(("execution_invariants", [line for line in EXECUTION_INVARIANTS]))
    parts.append(("limitations", [line for line in LIMITATIONS]))

    rebuilt: Dict[str, Any] = {}
    for key, value in parts:
        rebuilt[key] = value
    return rebuilt


# ---------------------------------------------------------------------------
# Frozen public entry point
# ---------------------------------------------------------------------------

_CHECK_FUNCTIONS = (
    _check_input_envelope,
    _check_type_discipline,
    _check_research_scope,
    _check_dataset_identity,
    _check_dataset_coverage,
    _check_static_timing_fields,
    _check_static_timing_combination,
    _check_boundary_structure,
    _check_boundary_alignment,
    _check_experiment_configuration,
    _check_registration_metadata,
    _check_identity_preimage,
    _check_timing_ledger_schema,
)


def validate_experiment_contract(request: dict) -> dict:
    """Validate a WP5-0A1 experiment contract request.

    Pure, stateless, deterministic and fail-closed.  Returns the frozen closed
    output schema ``wp5_0a1.contract.v4``.  Never raises for a malformed
    request: every rejection is reported as an ``INVALID`` contract carrying a
    single frozen ``reason_code`` and a complete fourteen-entry ``checks`` list.

    This includes hostile structural shapes.  A reference cycle, a container
    nested deeper than ``MAX_INPUT_NESTING_DEPTH``, or a request expanding to more
    than ``MAX_INPUT_NODE_VISITS`` node occurrences, is refused with
    ``TYPE_INVALID`` at ``INPUT_ENVELOPE``; ``RecursionError`` and ``MemoryError``
    are never raised.
    """
    ctx: Dict[str, Any] = {}
    statuses: List[str] = ["NOT_EVALUATED"] * len(CHECK_IDS)
    reason_code: Optional[str] = None

    for index, check_function in enumerate(_CHECK_FUNCTIONS):
        try:
            result = check_function(request, ctx)
        except _Halt as halt:
            statuses[index] = "FAIL"
            reason_code = halt.reason_code
            break
        if index == _ALIGNMENT_STEP_INDEX and result is False:
            statuses[index] = "NOT_APPLICABLE"
        else:
            statuses[index] = "PASS"

    if reason_code is None:
        statuses[len(CHECK_IDS) - 1] = "PASS"
        body = _build_body(ctx, statuses, None)
        h1 = _digest(body)
        h2 = _digest(_rebuild_body(ctx, statuses, None))
        if h1 != h2:
            statuses[len(CHECK_IDS) - 1] = "FAIL"
            reason_code = "CONTRACT_HASH_MISMATCH"
            body = _build_body(ctx, statuses, reason_code)
            body["contract_hash"] = _digest(body)
            return body
        body["contract_hash"] = h1
        return body

    body = _build_body(ctx, statuses, reason_code)
    body["contract_hash"] = _digest(body)
    return body
