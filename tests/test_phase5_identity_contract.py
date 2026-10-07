"""WP5-0A1 -- negative, boundary and identity tests for the Phase 5 contract validator.

Test catalogue coverage:
    R2  sections 13.1 - 13.8   (retained groups)
    R2A tests E-1..E-4, E-7..E-13, E-15, E-17   (retained / amended per R2B section C.7)
    R2B tests A-1..A-7, B-1..B-7, D-1..D-4

Everything the tests assert about frozen text, canonicalisation and identity is
re-declared INDEPENDENTLY in this file.  The tests never import a constant from
the module under test in order to compare the module against itself.
"""

import ast
import copy
import hashlib
import importlib
import inspect
import json
import unicodedata

import pytest

from app.research.phase5_identity_contract import validate_experiment_contract


MODULE_NAME = "app.research.phase5_identity_contract"


# ===========================================================================
# Independent re-implementations (never imported from the module under test)
# ===========================================================================


def canonical_bytes(obj):
    """Independent canonical-JSON encoder (R3B / R2 section 16)."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(obj):
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def h(tag):
    """Deterministic, non-placeholder, lowercase hex64 opaque identity."""
    return hashlib.sha256(("wp5_0a1_test::" + tag).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Independently retyped frozen vocabularies (R2B section C.2 / C.3)
# ---------------------------------------------------------------------------

EXPECTED_REASON_CODES = [
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
]

EXPECTED_CHECK_IDS = [
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
]

EXPECTED_STATUSES = {"PASS", "FAIL", "NOT_EVALUATED", "NOT_APPLICABLE"}

FIRST_AT_ELIG = "FIRST_OBSERVED_VALID_BAR_OPEN_AT_OR_AFTER_ORDER_ELIGIBILITY"

# Independently retyped frozen execution invariants (R2B section A.3), D-1.
EXPECTED_EXECUTION_INVARIANTS = [
    "I1. order_eligibility_offset_ms >= signal_decision_offset_ms.",
    "I2. actual_fill_open_utc_ms >= order_eligibility_utc_ms.",
    "I3. the actual fill bar index is strictly greater than the signal bar index.",
    "I4. no fill may be backdated to a bar that had already opened when the order became eligible.",
    "I5. fill_bar_offset_lower_bound == 1 is a LOWER BOUND ONLY. It is not a claim that an accepted policy"
    " fills exactly one bar after the signal bar; the actual fill bar is whichever observed valid bar opens"
    " first at or after order eligibility, and may be later.",
]

# Independently retyped frozen limitations (R2B section C.5), D-1.
EXPECTED_LIMITATIONS = [
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
]


# ===========================================================================
# Fixtures -- a minimal VALID request in each coverage mode
# ===========================================================================

DAY_MS = 86400000
FIRST_OPEN = 1577836800000            # 2020-01-01T00:00:00Z
ROWS = 366
LAST_OPEN = FIRST_OPEN + (ROWS - 1) * DAY_MS
DATASET_END = LAST_OPEN + DAY_MS

TRAIN_START = FIRST_OPEN
TRAIN_END = FIRST_OPEN + 200 * DAY_MS
VAL_START = TRAIN_END + DAY_MS
VAL_END = VAL_START + 80 * DAY_MS
HOLDOUT_START = VAL_END + DAY_MS
HOLDOUT_END = DATASET_END


def base_request():
    """A canonical VALID CONTINUOUS_24_7 request."""
    return {
        "schema_version": "wp5_0a1.request.v4",
        "research_scope": {
            "schema_version": "wp5_0a1.research_scope.v1",
            "hypothesis_family_hash": h("hypothesis"),
            "strategy_family_hash": h("strategy_family"),
            "asset_universe_hash": h("asset_universe"),
            "target_definition_hash": h("target_definition"),
            "evaluation_objective_hash": h("evaluation_objective"),
        },
        "research_program_registration": {
            "schema_version": "wp5_0a1.research_program_registration.v1",
            "program_label": "MIE Phase5 Pilot",
            "owner": "anteneh",
            "created_utc_ms": 1700000000000,
        },
        "dataset_identity": {
            "schema_version": "wp5_0a1.dataset_identity.v3",
            "dataset_hash": h("dataset"),
            "timeframe_ms": DAY_MS,
            "first_open_ms": FIRST_OPEN,
            "last_open_ms": LAST_OPEN,
            "dataset_end_exclusive_ms": DATASET_END,
            "rows": ROWS,
            "coverage_mode": "CONTINUOUS_24_7",
        },
        "static_timing_policy": {
            "schema_version": "wp5_0a1.static_timing_policy.v3",
            "bar_duration_ms": DAY_MS,
            "settlement_lag_ms": 0,
            "feature_availability_rule": "AT_BAR_CLOSE",
            "signal_delay_ms": 0,
            "order_submission_latency_ms": 0,
            "order_submission_rule": "AT_ORDER_ELIGIBILITY",
            "fill_rule": FIRST_AT_ELIG,
        },
        "boundaries": {
            "schema_version": "wp5_0a1.boundaries.v1",
            "train": {"start_ms": TRAIN_START, "end_ms": TRAIN_END},
            "val": {"start_ms": VAL_START, "end_ms": VAL_END},
            "holdout": {"start_ms": HOLDOUT_START, "end_ms": HOLDOUT_END},
        },
        "experiment_configuration": {
            "schema_version": "wp5_0a1.experiment_configuration.v1",
            "strategy_id": "H-025",
            "param_hash": h("params"),
            "method_hash": h("method"),
        },
        "preregistration": {
            "schema_version": "wp5_0a1.preregistration.v1",
            "registered_utc_ms": 1700000001000,
            "registered_by": "anteneh",
            "registration_reference": "PRE/2024/0001",
        },
        "expected_identities": {},
        "timing_ledger_schema_version": "wp5_0.timing_ledger_row.v1",
    }


def session_request():
    request = base_request()
    dataset = request["dataset_identity"]
    dataset["coverage_mode"] = "SESSION_CALENDAR"
    dataset["calendar_id"] = "XNYS"
    dataset["calendar_hash"] = h("calendar")
    # Session data is sparse on a nominal daily grid: rows need not equal slots
    # and the declared end need not equal last_open_ms + timeframe_ms.
    dataset["rows"] = 252
    dataset["dataset_end_exclusive_ms"] = LAST_OPEN + 3 * DAY_MS + 1
    request["boundaries"]["holdout"]["end_ms"] = LAST_OPEN + 3 * DAY_MS
    return request


def sparse_request():
    request = base_request()
    dataset = request["dataset_identity"]
    dataset["coverage_mode"] = "SPARSE_DECLARED"
    dataset["coverage_manifest_hash"] = h("manifest")
    dataset["rows"] = 17
    dataset["dataset_end_exclusive_ms"] = LAST_OPEN + 7
    request["boundaries"]["holdout"]["end_ms"] = LAST_OPEN + 7
    return request


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def statuses_of(result):
    return {entry["check_id"]: entry["status"] for entry in result["checks"]}


def status_list(result):
    return [entry["status"] for entry in result["checks"]]


def assert_invalid(result, reason_code, failing_check=None):
    assert result["contract_status"] == "INVALID", result
    assert result["reason_code"] == reason_code, (
        "expected %s, got %s" % (reason_code, result["reason_code"])
    )
    assert result["reason_code"] in EXPECTED_REASON_CODES
    if failing_check is not None:
        assert statuses_of(result)[failing_check] == "FAIL", status_list(result)
    assert_output_shape(result)


def assert_valid(result):
    assert result["contract_status"] == "VALID", result
    assert result["reason_code"] is None
    assert_output_shape(result)


def assert_output_shape(result):
    """Invariants that must hold for EVERY output, VALID or INVALID."""
    assert isinstance(result, dict)
    assert result["schema_version"] == "wp5_0a1.contract.v4"
    assert result["contract_status"] in ("VALID", "INVALID")

    checks = result["checks"]
    assert [entry["check_id"] for entry in checks] == EXPECTED_CHECK_IDS
    assert len(checks) == 14
    for entry in checks:
        assert set(entry.keys()) == {"check_id", "status"}
        assert entry["status"] in EXPECTED_STATUSES

    statuses = status_list(result)

    # At most one FAIL, and everything after it is NOT_EVALUATED.
    fail_indexes = [i for i, s in enumerate(statuses) if s == "FAIL"]
    assert len(fail_indexes) <= 1, statuses
    if fail_indexes:
        cut = fail_indexes[0]
        assert all(s == "NOT_EVALUATED" for s in statuses[cut + 1:]), statuses
        assert all(s in ("PASS", "NOT_APPLICABLE") for s in statuses[:cut]), statuses
        # NOT_APPLICABLE may never appear after a FAIL.
        assert "NOT_APPLICABLE" not in statuses[cut:], statuses
    else:
        assert all(s in ("PASS", "NOT_APPLICABLE") for s in statuses), statuses

    # NOT_APPLICABLE may only ever appear at BOUNDARY_ALIGNMENT.
    for index, status in enumerate(statuses):
        if status == "NOT_APPLICABLE":
            assert EXPECTED_CHECK_IDS[index] == "BOUNDARY_ALIGNMENT", statuses

    # reason_code is null if and only if the contract is VALID.
    if result["contract_status"] == "VALID":
        assert result["reason_code"] is None
        assert statuses.count("FAIL") == 0
    else:
        assert result["reason_code"] in EXPECTED_REASON_CODES
        assert statuses.count("FAIL") == 1

    # contract_hash is always present, lowercase hex64.
    contract_hash = result["contract_hash"]
    assert isinstance(contract_hash, str)
    assert len(contract_hash) == 64
    assert all(c in "0123456789abcdef" for c in contract_hash)

    # Frozen text blocks are emitted regardless of status.
    assert result["execution_invariants"] == EXPECTED_EXECUTION_INVARIANTS
    assert result["limitations"] == EXPECTED_LIMITATIONS


def mutate(path, value, request=None):
    """Return a request with request[path[0]][path[1]]... set to value."""
    request = copy.deepcopy(request if request is not None else base_request())
    node = request
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return request


def drop(path, request=None):
    request = copy.deepcopy(request if request is not None else base_request())
    node = request
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return request


# ===========================================================================
# 13.1 -- Baseline positive cases and determinism
# ===========================================================================


def test_baseline_continuous_request_is_valid():
    result = validate_experiment_contract(base_request())
    assert_valid(result)
    assert status_list(result) == ["PASS"] * 14


def test_baseline_session_calendar_request_is_valid():
    result = validate_experiment_contract(session_request())
    assert_valid(result)
    assert statuses_of(result)["BOUNDARY_ALIGNMENT"] == "NOT_APPLICABLE"


def test_baseline_sparse_declared_request_is_valid():
    result = validate_experiment_contract(sparse_request())
    assert_valid(result)
    assert statuses_of(result)["BOUNDARY_ALIGNMENT"] == "NOT_APPLICABLE"


def test_validator_is_deterministic_across_repeated_calls():
    first = validate_experiment_contract(base_request())
    second = validate_experiment_contract(base_request())
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first["contract_hash"] == second["contract_hash"]


def test_validator_does_not_mutate_its_input():
    request = base_request()
    before = canonical_bytes(request)
    validate_experiment_contract(request)
    assert canonical_bytes(request) == before


def test_identity_chain_is_reproducible_from_the_output_itself():
    """The emitted identities must be recomputable by an independent hasher."""
    result = validate_experiment_contract(base_request())

    scope = {k: v for k, v in result["research_scope"].items()}
    assert sha256_hex(scope) == result["research_program_id"]
    assert result["counter_key"] == result["research_program_id"]

    dataset = dict(result["dataset_identity"])
    for derived in ("dataset_identity_hash", "coverage_verification_status",
                    "dataset_end_verification_status"):
        dataset.pop(derived)
    assert sha256_hex(dataset) == result["dataset_identity"]["dataset_identity_hash"]

    policy = dict(result["static_timing_policy"])
    policy.pop("static_timing_policy_hash")
    assert sha256_hex(policy) == result["static_timing_policy"]["static_timing_policy_hash"]

    boundaries = dict(result["boundaries"])
    for derived in ("boundaries_hash", "gap_train_val_ms", "gap_val_holdout_ms",
                    "alignment_verification_status"):
        boundaries.pop(derived)
    assert sha256_hex(boundaries) == result["boundaries"]["boundaries_hash"]

    configuration = dict(result["experiment_configuration"])
    configuration.pop("experiment_configuration_hash")
    assert sha256_hex(configuration) == result["experiment_id"]


def test_identity_chain_is_acyclic_no_self_reference():
    """No emitted record may contain its own hash in its own preimage."""
    result = validate_experiment_contract(base_request())
    pairs = [
        ("dataset_identity", "dataset_identity_hash"),
        ("static_timing_policy", "static_timing_policy_hash"),
        ("boundaries", "boundaries_hash"),
        ("experiment_configuration", "experiment_configuration_hash"),
        ("research_program_registration", "registration_record_hash"),
        ("preregistration", "preregistration_record_hash"),
    ]
    for block, hash_key in pairs:
        record = dict(result[block])
        own_hash = record[hash_key]
        record.pop(hash_key)
        assert own_hash not in canonical_bytes(record).decode("utf-8")
    assert "contract_hash" not in canonical_bytes(
        {k: v for k, v in result.items() if k != "contract_hash"}
    ).decode("utf-8")


def test_dataset_end_exclusive_ms_is_bound_into_dataset_hash_and_experiment_id():
    """Step 9 of the brief: dataset_end_exclusive_ms must change both hashes."""
    baseline = validate_experiment_contract(sparse_request())
    moved = sparse_request()
    moved["dataset_identity"]["dataset_end_exclusive_ms"] += 1
    changed = validate_experiment_contract(moved)
    assert_valid(changed)
    assert (
        changed["dataset_identity"]["dataset_identity_hash"]
        != baseline["dataset_identity"]["dataset_identity_hash"]
    )
    assert changed["experiment_id"] != baseline["experiment_id"]
    assert changed["contract_hash"] != baseline["contract_hash"]


def test_administrative_metadata_never_affects_identity():
    """F1 identity / metadata separation."""
    baseline = validate_experiment_contract(base_request())
    for path, value in (
        (("research_program_registration", "program_label"), "A Totally Different Label"),
        (("research_program_registration", "owner"), "someone.else"),
        (("research_program_registration", "created_utc_ms"), 1710000000000),
    ):
        result = validate_experiment_contract(mutate(path, value))
        assert_valid(result)
        assert result["research_program_id"] == baseline["research_program_id"]
        assert result["experiment_id"] == baseline["experiment_id"]
        assert result["counter_key"] == baseline["counter_key"]

    for path, value in (
        (("preregistration", "registered_utc_ms"), 1720000000000),
        (("preregistration", "registered_by"), "another.actor"),
        (("preregistration", "registration_reference"), "PRE/2030/9999"),
    ):
        result = validate_experiment_contract(mutate(path, value))
        assert_valid(result)
        assert result["experiment_id"] == baseline["experiment_id"]


def test_scope_change_changes_research_program_id_and_experiment_id():
    baseline = validate_experiment_contract(base_request())
    result = validate_experiment_contract(
        mutate(("research_scope", "hypothesis_family_hash"), h("other-hypothesis"))
    )
    assert_valid(result)
    assert result["research_program_id"] != baseline["research_program_id"]
    assert result["experiment_id"] != baseline["experiment_id"]


def test_expected_identities_matching_passes_and_mismatching_fails():
    baseline = validate_experiment_contract(base_request())

    ok = base_request()
    ok["expected_identities"] = {
        "research_program_id": baseline["research_program_id"],
        "experiment_id": baseline["experiment_id"],
    }
    assert_valid(validate_experiment_contract(ok))

    for key in ("research_program_id", "experiment_id"):
        bad = base_request()
        bad["expected_identities"] = {key: h("wrong")}
        assert_invalid(
            validate_experiment_contract(bad),
            "IDENTITY_PREIMAGE_MISMATCH",
            "IDENTITY_PREIMAGE",
        )


# ===========================================================================
# 16 -- canonicalisation parity: mappings order-free, lists order-bearing (R3B)
# ===========================================================================


def test_mapping_key_order_does_not_change_identity():
    request = base_request()
    reordered = base_request()
    reordered["research_scope"] = dict(
        reversed(list(request["research_scope"].items()))
    )
    reordered["dataset_identity"] = dict(
        reversed(list(request["dataset_identity"].items()))
    )
    reordered["static_timing_policy"] = dict(
        reversed(list(request["static_timing_policy"].items()))
    )
    a = validate_experiment_contract(request)
    b = validate_experiment_contract(reordered)
    assert_valid(b)
    assert a["research_program_id"] == b["research_program_id"]
    assert a["experiment_id"] == b["experiment_id"]
    assert a["contract_hash"] == b["contract_hash"]


def test_canonicalisation_is_order_free_for_mappings_and_order_bearing_for_lists():
    assert canonical_bytes({"a": 1, "b": 2}) == canonical_bytes({"b": 2, "a": 1})
    assert canonical_bytes([1, 2]) != canonical_bytes([2, 1])


def test_canonical_form_rejects_non_finite_floats():
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("nan")})


def test_emitted_lists_are_order_bearing():
    result = validate_experiment_contract(base_request())
    assert result["limitations"] != list(reversed(result["limitations"]))
    assert canonical_bytes(result["limitations"]) != canonical_bytes(
        list(reversed(result["limitations"]))
    )


# ===========================================================================
# 13.2 -- Envelope, unknown fields, out-of-scope fields, self reference
# ===========================================================================


def test_request_is_not_a_mapping():
    for bad in ([], "x", 7, None, (1, 2)):
        assert_invalid(validate_experiment_contract(bad), "TYPE_INVALID", "INPUT_ENVELOPE")


def test_missing_top_level_schema_version():
    assert_invalid(
        validate_experiment_contract(drop(("schema_version",))),
        "MISSING_FIELD",
        "INPUT_ENVELOPE",
    )


def test_unsupported_top_level_schema_version():
    assert_invalid(
        validate_experiment_contract(mutate(("schema_version",), "wp5_0a1.request.v9")),
        "SCHEMA_VERSION_UNSUPPORTED",
        "INPUT_ENVELOPE",
    )


@pytest.mark.parametrize(
    "superseded",
    [
        "wp5_0a1.request.v1",
        "wp5_0a1.request.v2",
        "wp5_0a1.request.v3",
    ],
)
def test_superseded_request_schema_versions_are_refused(superseded):
    """R2B D-2: an explicitly superseded schema version is recognised and refused."""
    assert_invalid(
        validate_experiment_contract(mutate(("schema_version",), superseded)),
        "SCHEMA_VERSION_UNSUPPORTED",
        "INPUT_ENVELOPE",
    )


@pytest.mark.parametrize(
    "block,superseded",
    [
        ("dataset_identity", "wp5_0a1.dataset_identity.v2"),
        ("dataset_identity", "wp5_0a1.dataset_identity.v1"),
        ("static_timing_policy", "wp5_0a1.static_timing_policy.v2"),
        ("static_timing_policy", "wp5_0a1.static_timing_policy.v1"),
    ],
)
def test_superseded_sub_record_schema_versions_are_refused(block, superseded):
    assert_invalid(
        validate_experiment_contract(mutate((block, "schema_version"), superseded)),
        "SCHEMA_VERSION_UNSUPPORTED",
        "INPUT_ENVELOPE",
    )


@pytest.mark.parametrize("block", [
    "research_scope",
    "research_program_registration",
    "dataset_identity",
    "static_timing_policy",
    "boundaries",
    "experiment_configuration",
    "preregistration",
])
def test_sub_record_missing_schema_version(block):
    assert_invalid(
        validate_experiment_contract(drop((block, "schema_version"))),
        "MISSING_FIELD",
        "INPUT_ENVELOPE",
    )


def test_unknown_top_level_field():
    request = base_request()
    request["extra_field"] = "x"
    assert_invalid(
        validate_experiment_contract(request), "UNKNOWN_FIELD_PRESENT", "INPUT_ENVELOPE"
    )


@pytest.mark.parametrize("field", [
    "research_scope",
    "research_program_registration",
    "dataset_identity",
    "static_timing_policy",
    "boundaries",
    "experiment_configuration",
    "preregistration",
    "expected_identities",
    "timing_ledger_schema_version",
])
def test_missing_top_level_field(field):
    assert_invalid(
        validate_experiment_contract(drop((field,))), "MISSING_FIELD", "INPUT_ENVELOPE"
    )


def test_caller_supplied_contract_hash_at_top_level_is_an_unknown_field():
    """Interpretation I-3, reconciling 13.5.6 with 13.2.11."""
    request = base_request()
    request["contract_hash"] = h("forged")
    assert_invalid(
        validate_experiment_contract(request), "UNKNOWN_FIELD_PRESENT", "INPUT_ENVELOPE"
    )


def test_nested_contract_hash_is_identity_self_reference():
    """Interpretation I-3."""
    request = base_request()
    request["research_scope"]["contract_hash"] = h("forged")
    assert_invalid(
        validate_experiment_contract(request), "IDENTITY_SELF_REFERENCE", "INPUT_ENVELOPE"
    )


@pytest.mark.parametrize("derived", ["registration_record_hash", "preregistration_record_hash"])
@pytest.mark.parametrize("root", ["research_scope", "experiment_configuration"])
def test_derived_record_hash_inside_identity_preimage_is_self_reference(root, derived):
    request = base_request()
    request[root][derived] = h("derived")
    assert_invalid(
        validate_experiment_contract(request), "IDENTITY_SELF_REFERENCE", "INPUT_ENVELOPE"
    )


@pytest.mark.parametrize("admin_key", [
    "program_label", "owner", "created_utc_ms",
    "registered_utc_ms", "registered_by", "registration_reference",
    "reviewer_label", "approver_actor", "analyst_note",
])
@pytest.mark.parametrize("root", ["research_scope", "experiment_configuration"])
def test_admin_metadata_inside_identity_preimage_is_refused(root, admin_key):
    request = base_request()
    request[root][admin_key] = "x"
    assert_invalid(
        validate_experiment_contract(request),
        "ADMIN_METADATA_IN_IDENTITY_PREIMAGE",
        "INPUT_ENVELOPE",
    )


@pytest.mark.parametrize("key", ["trial_id", "trial_id_record"])
def test_trial_fields_are_out_of_scope(key):
    request = base_request()
    request[key] = "anything"
    assert_invalid(
        validate_experiment_contract(request), "TRIAL_ID_OUT_OF_SCOPE", "INPUT_ENVELOPE"
    )


def test_trial_field_nested_at_depth_is_still_refused():
    request = base_request()
    request["experiment_configuration"]["trial_id"] = "t-1"
    assert_invalid(
        validate_experiment_contract(request), "TRIAL_ID_OUT_OF_SCOPE", "INPUT_ENVELOPE"
    )


@pytest.mark.parametrize("key", [
    "attempt_fingerprint", "attempt_fingerprint_record", "reservation_sequence",
    "reservation_utc_ms", "idempotency_key", "verification_replay_id",
    "N", "N_total", "trial_outcome", "timing_ledger_rows", "ledger_rows", "ledger_state",
])
def test_counter_fields_are_out_of_scope(key):
    request = base_request()
    request[key] = 1
    assert_invalid(
        validate_experiment_contract(request), "COUNTER_FIELD_OUT_OF_SCOPE", "INPUT_ENVELOPE"
    )


@pytest.mark.parametrize("key", [
    "psr", "dsr", "sr_values", "var_sr_trials", "holdout_access_state",
    "mc_paths", "cpcv_folds", "rag_index",
])
def test_downstream_statistics_fields_are_out_of_scope(key):
    request = base_request()
    request[key] = 1
    assert_invalid(
        validate_experiment_contract(request), "OUT_OF_SCOPE_FIELD", "INPUT_ENVELOPE"
    )


def test_out_of_scope_precedence_trial_beats_counter_beats_statistics():
    request = base_request()
    request["trial_id"] = "t"
    request["idempotency_key"] = "k"
    request["psr"] = 1
    assert_invalid(validate_experiment_contract(request), "TRIAL_ID_OUT_OF_SCOPE")

    request = base_request()
    request["idempotency_key"] = "k"
    request["psr"] = 1
    assert_invalid(validate_experiment_contract(request), "COUNTER_FIELD_OUT_OF_SCOPE")

    request = base_request()
    request["psr"] = 1
    request["research_scope"]["owner"] = "x"
    assert_invalid(validate_experiment_contract(request), "OUT_OF_SCOPE_FIELD")


def test_out_of_scope_scan_precedes_schema_version_check():
    request = base_request()
    request["schema_version"] = "wp5_0a1.request.v3"
    request["trial_id"] = "t"
    assert_invalid(validate_experiment_contract(request), "TRIAL_ID_OUT_OF_SCOPE")


def test_non_string_top_level_mapping_key_is_refused_without_raising():
    request = base_request()
    request[7] = "x"
    result = validate_experiment_contract(request)
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


# ===========================================================================
# 13.3 -- Type discipline
# ===========================================================================


@pytest.mark.parametrize("path", [
    ("dataset_identity", "rows"),
    ("dataset_identity", "timeframe_ms"),
    ("dataset_identity", "first_open_ms"),
    ("static_timing_policy", "settlement_lag_ms"),
    ("static_timing_policy", "signal_delay_ms"),
    ("static_timing_policy", "order_submission_latency_ms"),
    ("boundaries", "train", "start_ms"),
    ("research_program_registration", "created_utc_ms"),
])
def test_bool_is_never_accepted_as_int(path):
    for value in (True, False):
        assert_invalid(
            validate_experiment_contract(mutate(path, value)),
            "BOOL_NOT_ACCEPTED_AS_INT",
            "TYPE_DISCIPLINE",
        )


@pytest.mark.parametrize("path", [
    ("dataset_identity", "rows"),
    ("dataset_identity", "timeframe_ms"),
    ("dataset_identity", "first_open_ms"),
    ("dataset_identity", "dataset_end_exclusive_ms"),
    ("static_timing_policy", "bar_duration_ms"),
    ("static_timing_policy", "settlement_lag_ms"),
    ("boundaries", "holdout", "end_ms"),
])
def test_floats_are_never_permitted(path):
    assert_invalid(
        validate_experiment_contract(mutate(path, 1.0)),
        "FLOAT_NOT_PERMITTED",
        "TYPE_DISCIPLINE",
    )


def test_integer_valued_float_is_still_refused():
    """No silent coercion: 86400000.0 is not 86400000."""
    assert_invalid(
        validate_experiment_contract(mutate(("dataset_identity", "timeframe_ms"), 86400000.0)),
        "FLOAT_NOT_PERMITTED",
        "TYPE_DISCIPLINE",
    )


@pytest.mark.parametrize("path", [
    ("dataset_identity", "dataset_hash"),
    ("dataset_identity", "rows"),
    ("research_scope", "hypothesis_family_hash"),
    ("boundaries", "val", "start_ms"),
    ("timing_ledger_schema_version",),
])
def test_null_is_never_permitted(path):
    assert_invalid(
        validate_experiment_contract(mutate(path, None)),
        "NULL_NOT_PERMITTED",
        "TYPE_DISCIPLINE",
    )


def test_non_nfc_string_is_refused():
    decomposed = "Café Program"          # NFD form of "Café Program"
    assert decomposed != "Café Program"
    assert_invalid(
        validate_experiment_contract(
            mutate(("research_program_registration", "program_label"), decomposed)
        ),
        "STRING_NOT_NFC",
        "TYPE_DISCIPLINE",
    )


def test_nfc_string_is_accepted():
    """A composed (NFC) string clears TYPE_DISCIPLINE.

    ``program_label`` additionally carries an ASCII-only frozen pattern, so a
    non-ASCII label is still refused later at REGISTRATION_METADATA.  The point
    under test here is that it is NEVER refused as STRING_NOT_NFC.
    """
    composed = "Café Program"
    assert unicodedata.normalize("NFC", composed) == composed
    result = validate_experiment_contract(
        mutate(("research_program_registration", "program_label"), composed)
    )
    assert result["reason_code"] != "STRING_NOT_NFC"
    assert statuses_of(result)["TYPE_DISCIPLINE"] == "PASS"
    assert_output_shape(result)

    # An ASCII NFC label satisfies both the NFC gate and the frozen pattern.
    assert_valid(
        validate_experiment_contract(
            mutate(("research_program_registration", "program_label"), "Cafe Program")
        )
    )


@pytest.mark.parametrize("bad", ["a\tb", "a\nb", "a\x00b", "a\x7fb", "a\x1fb"])
def test_control_characters_are_refused(bad):
    assert_invalid(
        validate_experiment_contract(
            mutate(("research_program_registration", "program_label"), bad)
        ),
        "TYPE_INVALID",
        "TYPE_DISCIPLINE",
    )


def test_non_string_nested_mapping_key_is_refused():
    """Hardening R1 ruling 4 (I-7 approved): a non-string mapping key at ANY
    depth is refused at INPUT_ENVELOPE with TYPE_INVALID, and the validator
    returns a well-formed INVALID response rather than raising."""
    request = base_request()
    request["boundaries"]["train"][3] = 1
    assert_invalid(
        validate_experiment_contract(request), "TYPE_INVALID", "INPUT_ENVELOPE"
    )


@pytest.mark.parametrize("path", [
    ("research_scope",),
    ("dataset_identity",),
    ("static_timing_policy",),
    ("boundaries", "holdout"),
    ("experiment_configuration",),
])
def test_non_string_mapping_key_at_every_depth_is_refused_at_input_envelope(path):
    """Ruling 4: the depth of the offending mapping never changes the outcome."""
    request = base_request()
    node = request
    for key in path:
        node = node[key]
    node[7] = "x"
    assert_invalid(
        validate_experiment_contract(request), "TYPE_INVALID", "INPUT_ENVELOPE"
    )


def test_non_string_key_precedes_missing_field_and_unknown_field():
    """Ruling 4: TYPE_INVALID precedes MISSING_FIELD and UNKNOWN_FIELD_PRESENT."""
    request = drop(("research_scope", "asset_universe_hash"))
    request["research_scope"]["surplus"] = h("surplus")
    request["research_scope"][11] = "x"
    assert_invalid(
        validate_experiment_contract(request), "TYPE_INVALID", "INPUT_ENVELOPE"
    )


@pytest.mark.parametrize("bad", [set(), frozenset(), (1, 2), object(), b"bytes", complex(1, 2)])
def test_unsupported_value_types_are_refused(bad):
    assert_invalid(
        validate_experiment_contract(mutate(("experiment_configuration", "param_hash"), bad)),
        "TYPE_INVALID",
        "TYPE_DISCIPLINE",
    )


def test_bool_is_tested_before_int_and_before_float():
    """Precedence: a bool must never be reported as TYPE_INVALID or FLOAT."""
    result = validate_experiment_contract(mutate(("dataset_identity", "rows"), True))
    assert result["reason_code"] == "BOOL_NOT_ACCEPTED_AS_INT"


# ===========================================================================
# 13.4 -- research_scope
# ===========================================================================


@pytest.mark.parametrize("key", [
    "hypothesis_family_hash",
    "strategy_family_hash",
    "asset_universe_hash",
    "target_definition_hash",
    "evaluation_objective_hash",
])
@pytest.mark.parametrize("bad", [
    "0" * 64,
    "f" * 64,
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",   # sha256("") is len 64
    "A" * 64,
    "abc",
    "",
    "g" * 64,
    123,
])
def test_research_scope_rejects_unbound_or_malformed_hashes(key, bad):
    # Hardening R1: exactly one outcome per case.  ``123`` is a well-typed JSON
    # integer, so it survives TYPE_DISCIPLINE and is refused as a scope defect.
    result = validate_experiment_contract(mutate(("research_scope", key), bad))
    assert_invalid(result, "RESEARCH_SCOPE_INVALID", "RESEARCH_SCOPE")


def test_research_scope_rejects_empty_sha256_placeholder():
    empty_sha = hashlib.sha256(b"").hexdigest()
    assert_invalid(
        validate_experiment_contract(
            mutate(("research_scope", "asset_universe_hash"), empty_sha)
        ),
        "RESEARCH_SCOPE_INVALID",
        "RESEARCH_SCOPE",
    )


@pytest.mark.parametrize("key", [
    "hypothesis_family_hash", "strategy_family_hash", "asset_universe_hash",
    "target_definition_hash", "evaluation_objective_hash",
])
def test_research_scope_missing_field(key):
    assert_invalid(
        validate_experiment_contract(drop(("research_scope", key))),
        "MISSING_FIELD",
        "RESEARCH_SCOPE",
    )


def test_research_scope_unknown_field():
    request = base_request()
    request["research_scope"]["extra"] = h("extra")
    assert_invalid(
        validate_experiment_contract(request), "UNKNOWN_FIELD_PRESENT", "RESEARCH_SCOPE"
    )


# ===========================================================================
# 13.4 -- dataset identity and coverage (R2A A.1-A.5, R2B B.1-B.7)
# ===========================================================================


@pytest.mark.parametrize("bad", ["0" * 64, "f" * 64, "ABC" + "d" * 61, "abc", 5])
def test_dataset_hash_must_be_a_bound_hex64(bad):
    # Hardening R1: exactly one outcome per case.
    result = validate_experiment_contract(mutate(("dataset_identity", "dataset_hash"), bad))
    assert_invalid(result, "DATASET_HASH_INVALID", "DATASET_IDENTITY")


@pytest.mark.parametrize("rows", [0, -1, -1000])
def test_rows_must_be_at_least_one(rows):
    assert_invalid(
        validate_experiment_contract(mutate(("dataset_identity", "rows"), rows)),
        "TYPE_INVALID",
        "DATASET_IDENTITY",
    )


@pytest.mark.parametrize("timeframe", [0, -1, -86400000])
def test_timeframe_ms_must_be_strictly_positive(timeframe):
    assert_invalid(
        validate_experiment_contract(mutate(("dataset_identity", "timeframe_ms"), timeframe)),
        "UTC_MS_INVALID",
        "DATASET_IDENTITY",
    )


@pytest.mark.parametrize("path", [
    ("dataset_identity", "first_open_ms"),
    ("dataset_identity", "last_open_ms"),
    ("dataset_identity", "dataset_end_exclusive_ms"),
    ("research_program_registration", "created_utc_ms"),
    ("preregistration", "registered_utc_ms"),
    ("boundaries", "train", "start_ms"),
])
@pytest.mark.parametrize("bad", [0, -1, 946684799999, 4102444800001, 10 ** 18])
def test_absolute_utc_ms_range(path, bad):
    assert_invalid(
        validate_experiment_contract(mutate(path, bad)), "UTC_MS_OUT_OF_RANGE"
    )


def test_utc_ms_range_endpoints_are_inclusive():
    request = base_request()
    request["research_program_registration"]["created_utc_ms"] = 946684800000
    request["preregistration"]["registered_utc_ms"] = 4102444800000
    assert_valid(validate_experiment_contract(request))


def test_last_open_before_first_open():
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "last_open_ms"), FIRST_OPEN - DAY_MS)
        ),
        "DATASET_INTERVAL_ORDER_INVALID",
        "DATASET_IDENTITY",
    )


def test_single_row_dataset_is_permitted():
    request = base_request()
    dataset = request["dataset_identity"]
    dataset["rows"] = 1
    dataset["last_open_ms"] = FIRST_OPEN
    dataset["dataset_end_exclusive_ms"] = FIRST_OPEN + DAY_MS
    request["boundaries"] = {
        "schema_version": "wp5_0a1.boundaries.v1",
        "train": {"start_ms": FIRST_OPEN, "end_ms": FIRST_OPEN + DAY_MS},
        "val": {"start_ms": FIRST_OPEN + DAY_MS, "end_ms": FIRST_OPEN + DAY_MS},
        "holdout": {"start_ms": FIRST_OPEN + DAY_MS, "end_ms": FIRST_OPEN + DAY_MS},
    }
    # val and holdout are empty -> BOUNDARY_EMPTY; the dataset itself is fine.
    assert_invalid(validate_experiment_contract(request), "BOUNDARY_EMPTY", "BOUNDARY_STRUCTURE")


@pytest.mark.parametrize("delta", [0, -1, -DAY_MS])
def test_dataset_end_must_exceed_last_open(delta):
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "dataset_end_exclusive_ms"), LAST_OPEN + delta)
        ),
        "DATASET_END_INVALID",
        "DATASET_IDENTITY",
    )


@pytest.mark.parametrize("mode", ["CONTINUOUS", "continuous_24_7", "UNKNOWN", "", "24_7", 5])
def test_coverage_mode_union_is_closed(mode):
    # Hardening R1: exactly one outcome per case.
    result = validate_experiment_contract(mutate(("dataset_identity", "coverage_mode"), mode))
    assert_invalid(result, "COVERAGE_MODE_UNKNOWN", "DATASET_COVERAGE")


def test_session_calendar_requires_calendar_id_and_calendar_hash():
    for missing in ("calendar_id", "calendar_hash"):
        assert_invalid(
            validate_experiment_contract(drop(("dataset_identity", missing), session_request())),
            "COVERAGE_CONTRACT_INCOMPLETE",
            "DATASET_COVERAGE",
        )


def test_sparse_declared_requires_coverage_manifest_hash():
    assert_invalid(
        validate_experiment_contract(
            drop(("dataset_identity", "coverage_manifest_hash"), sparse_request())
        ),
        "COVERAGE_CONTRACT_INCOMPLETE",
        "DATASET_COVERAGE",
    )


@pytest.mark.parametrize("field,value", [
    ("calendar_id", "XNYS"),
    ("calendar_hash", None),
    ("coverage_manifest_hash", None),
])
def test_continuous_mode_forbids_mode_conditional_fields(field, value):
    request = base_request()
    request["dataset_identity"][field] = "XNYS" if value is None and field == "calendar_id" else h(field)
    assert_invalid(
        validate_experiment_contract(request),
        "COVERAGE_MODE_FIELD_MISMATCH",
        "DATASET_COVERAGE",
    )


def test_session_calendar_forbids_coverage_manifest_hash():
    request = session_request()
    request["dataset_identity"]["coverage_manifest_hash"] = h("manifest")
    assert_invalid(
        validate_experiment_contract(request),
        "COVERAGE_MODE_FIELD_MISMATCH",
        "DATASET_COVERAGE",
    )


def test_sparse_declared_forbids_calendar_fields():
    for field, value in (("calendar_id", "XNYS"), ("calendar_hash", h("calendar"))):
        request = sparse_request()
        request["dataset_identity"][field] = value
        assert_invalid(
            validate_experiment_contract(request),
            "COVERAGE_MODE_FIELD_MISMATCH",
            "DATASET_COVERAGE",
        )


@pytest.mark.parametrize("bad", ["0" * 64, "f" * 64, "abc"])
def test_calendar_hash_must_be_bound(bad):
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "calendar_hash"), bad, session_request())
        ),
        "DATASET_HASH_INVALID",
        "DATASET_COVERAGE",
    )


@pytest.mark.parametrize("bad", ["0" * 64, "f" * 64, "abc"])
def test_coverage_manifest_hash_must_be_bound(bad):
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "coverage_manifest_hash"), bad, sparse_request())
        ),
        "DATASET_HASH_INVALID",
        "DATASET_COVERAGE",
    )


@pytest.mark.parametrize("bad", ["xnys", "", "1ABC", "X" * 33, "XN-YS"])
def test_present_calendar_id_failing_frozen_syntax_is_a_mode_field_mismatch(bad):
    """Hardening R1 ruling 3, limb 3: a calendar_id that is PRESENT and a string
    but fails the frozen syntax is COVERAGE_MODE_FIELD_MISMATCH.  It is NOT
    COVERAGE_CONTRACT_INCOMPLETE -- that code is reserved for absence.
    This supersedes interpretation I-4."""
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "calendar_id"), bad, session_request())
        ),
        "COVERAGE_MODE_FIELD_MISMATCH",
        "DATASET_COVERAGE",
    )


def test_missing_calendar_id_is_an_incomplete_coverage_contract():
    """Ruling 3, limb 1: absence, and only absence, is the incomplete case."""
    assert_invalid(
        validate_experiment_contract(drop(("dataset_identity", "calendar_id"), session_request())),
        "COVERAGE_CONTRACT_INCOMPLETE",
        "DATASET_COVERAGE",
    )


@pytest.mark.parametrize("bad", [5, 0, -1, ["XNYS"], {"id": "XNYS"}])
def test_wrong_calendar_id_datatype_is_a_type_discipline_defect(bad):
    """Ruling 3, limb 2: a wrong datatype is settled at the earlier
    type-discipline check, never at DATASET_COVERAGE."""
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "calendar_id"), bad, session_request())
        ),
        "TYPE_INVALID",
        "TYPE_DISCIPLINE",
    )


@pytest.mark.parametrize("bad,expected", [
    (None, "NULL_NOT_PERMITTED"),
    (True, "BOOL_NOT_ACCEPTED_AS_INT"),
])
def test_null_and_bool_calendar_id_keep_their_own_frozen_codes(bad, expected):
    """Ruling 3 limb 2 is subordinate to the more specific frozen codes for null
    and bool, which are themselves TYPE_DISCIPLINE defects at the same step."""
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "calendar_id"), bad, session_request())
        ),
        expected,
        "TYPE_DISCIPLINE",
    )


def test_calendar_id_datatype_precedes_calendar_id_syntax():
    """The three limbs of ruling 3 are mutually exclusive and totally ordered:
    datatype (step 2) before syntax (step 5); absence (step 5) is disjoint."""
    result = validate_experiment_contract(
        mutate(("dataset_identity", "calendar_id"), 7, session_request())
    )
    assert result["reason_code"] == "TYPE_INVALID"
    assert result["reason_code"] != "COVERAGE_MODE_FIELD_MISMATCH"
    assert statuses_of(result)["DATASET_COVERAGE"] == "NOT_EVALUATED"


def test_continuous_grid_modulo_violation():
    assert_invalid(
        validate_experiment_contract(
            mutate(("dataset_identity", "last_open_ms"), LAST_OPEN + 1)
        ),
        "DATASET_GRID_INVALID",
        "DATASET_COVERAGE",
    )


def test_continuous_rows_exceeding_grid_slots():
    assert_invalid(
        validate_experiment_contract(mutate(("dataset_identity", "rows"), ROWS + 1)),
        "DATASET_ROWS_EXCEED_GRID",
        "DATASET_COVERAGE",
    )


def test_continuous_rows_below_grid_slots():
    assert_invalid(
        validate_experiment_contract(mutate(("dataset_identity", "rows"), ROWS - 1)),
        "DATASET_ROWS_INCONSISTENT",
        "DATASET_COVERAGE",
    )


def test_rows_exceed_grid_is_reported_before_rows_inconsistent():
    """Both predicates hold when rows > slots; the exceed code must win."""
    result = validate_experiment_contract(mutate(("dataset_identity", "rows"), ROWS + 1000))
    assert result["reason_code"] == "DATASET_ROWS_EXCEED_GRID"


def test_continuous_dataset_end_must_equal_last_open_plus_timeframe():
    for delta in (1, -1, DAY_MS, 2 * DAY_MS):
        assert_invalid(
            validate_experiment_contract(
                mutate(("dataset_identity", "dataset_end_exclusive_ms"), DATASET_END + delta)
            ),
            "DATASET_END_INVALID",
        )


def test_non_continuous_dataset_end_is_not_compared_to_the_grid():
    """R2B B.3: session and sparse ends are structural only, never grid-derived."""
    for factory in (session_request, sparse_request):
        request = factory()
        request["dataset_identity"]["dataset_end_exclusive_ms"] = LAST_OPEN + 1
        request["boundaries"]["holdout"]["end_ms"] = LAST_OPEN + 1
        result = validate_experiment_contract(request)
        assert_valid(result)
        assert (
            result["dataset_identity"]["dataset_end_verification_status"]
            == "CALLER_ASSERTED_END_UNVERIFIED"
        )


def test_dataset_end_verification_status_per_mode():
    continuous = validate_experiment_contract(base_request())
    assert (
        continuous["dataset_identity"]["dataset_end_verification_status"]
        == "GRID_DERIVED_END_VERIFIED"
    )
    for factory in (session_request, sparse_request):
        result = validate_experiment_contract(factory())
        assert (
            result["dataset_identity"]["dataset_end_verification_status"]
            == "CALLER_ASSERTED_END_UNVERIFIED"
        )


def test_coverage_verification_status_per_mode():
    assert (
        validate_experiment_contract(base_request())["dataset_identity"][
            "coverage_verification_status"
        ]
        == "GRID_ARITHMETIC_VERIFIED"
    )
    for factory in (session_request, sparse_request):
        assert (
            validate_experiment_contract(factory())["dataset_identity"][
                "coverage_verification_status"
            ]
            == "IDENTITY_BOUND_ONLY_MEMBERSHIP_DEFERRED"
        )


def test_grid_arithmetic_is_never_applied_to_non_continuous_modes():
    """Rows far below nominal slots must be accepted outside CONTINUOUS_24_7."""
    for factory in (session_request, sparse_request):
        request = factory()
        request["dataset_identity"]["rows"] = 1
        assert_valid(validate_experiment_contract(request))


def test_non_continuous_never_reports_membership_or_alignment_as_verified():
    for factory in (session_request, sparse_request):
        result = validate_experiment_contract(factory())
        assert (
            result["boundaries"]["alignment_verification_status"]
            == "ALIGNMENT_DEFERRED_TO_COVERAGE_VALIDATOR"
        )
        assert result["derived_timing_offsets"]["fill_resolution_status"] == (
            "DEFERRED_TO_COVERAGE_VALIDATOR"
        )
        assert statuses_of(result)["BOUNDARY_ALIGNMENT"] == "NOT_APPLICABLE"
        text = canonical_bytes(result).decode("utf-8")
        assert "GRID_ALIGNMENT_VERIFIED" not in text
        assert "GRID_ARITHMETIC_VERIFIED" not in text
        assert "GRID_DERIVED_END_VERIFIED" not in text
        assert "NOMINAL_GRID_RESOLVED" not in text


# ===========================================================================
# 13.5 -- static timing fields, combination table and SAME_BAR_CLOSE
# ===========================================================================


def timing(**overrides):
    """A request whose static_timing_policy is overridden."""
    request = base_request()
    request["static_timing_policy"].update(overrides)
    return request


@pytest.mark.parametrize("factory", [base_request, session_request, sparse_request])
def test_same_bar_close_is_always_forbidden(factory):
    """R2B A.6 rows 5-6 and brief step 11 -- forbidden in every coverage mode."""
    request = factory()
    request["static_timing_policy"]["fill_rule"] = "SAME_BAR_CLOSE"
    assert_invalid(
        validate_experiment_contract(request),
        "SAME_BAR_CLOSE_EXECUTION_FORBIDDEN",
        "STATIC_TIMING_COMBINATION",
    )


def test_same_bar_close_wins_over_every_other_timing_defect():
    """Rule F is unconditional and precedes rules A/B and all offset arithmetic."""
    request = timing(
        fill_rule="SAME_BAR_CLOSE",
        feature_availability_rule="AT_BAR_CLOSE",
        settlement_lag_ms=1,                     # would be STATIC_TIMING_COMBINATION_INVALID
    )
    assert_invalid(
        validate_experiment_contract(request), "SAME_BAR_CLOSE_EXECUTION_FORBIDDEN"
    )


def test_same_bar_close_token_never_appears_as_an_accepted_value():
    result = validate_experiment_contract(base_request())
    assert "SAME_BAR_CLOSE" not in canonical_bytes(
        result["static_timing_policy"]
    ).decode("utf-8")
    assert "SAME_BAR_CLOSE" not in canonical_bytes(
        result["derived_timing_offsets"]
    ).decode("utf-8")


@pytest.mark.parametrize("value", ["NEXT_BAR_OPEN", "AT_SIGNAL", "", "same_bar_close", "VWAP"])
def test_unknown_fill_rule(value):
    assert_invalid(
        validate_experiment_contract(timing(fill_rule=value)),
        "STATIC_TIMING_RULE_UNKNOWN",
        "STATIC_TIMING_FIELDS",
    )


@pytest.mark.parametrize("value", ["AT_BAR_OPEN", "", "at_bar_close", "IMMEDIATE"])
def test_unknown_feature_availability_rule(value):
    assert_invalid(
        validate_experiment_contract(timing(feature_availability_rule=value)),
        "STATIC_TIMING_RULE_UNKNOWN",
        "STATIC_TIMING_FIELDS",
    )


@pytest.mark.parametrize("value", ["AT_SIGNAL_BAR_CLOSE", "", "IMMEDIATE"])
def test_unknown_order_submission_rule(value):
    assert_invalid(
        validate_experiment_contract(timing(order_submission_rule=value)),
        "STATIC_TIMING_RULE_UNKNOWN",
        "STATIC_TIMING_FIELDS",
    )


@pytest.mark.parametrize("field", [
    "bar_duration_ms", "settlement_lag_ms", "feature_availability_rule",
    "signal_delay_ms", "order_submission_latency_ms", "order_submission_rule", "fill_rule",
])
def test_missing_static_timing_field(field):
    assert_invalid(
        validate_experiment_contract(drop(("static_timing_policy", field))),
        "MISSING_FIELD",
        "STATIC_TIMING_FIELDS",
    )


def test_absolute_timestamp_in_static_timing_policy_is_refused():
    """R2B A.4: the policy is offset-only; absolute instants are forbidden."""
    for key in ("anchor_utc_ms", "session_open_utc_ms"):
        request = base_request()
        request["static_timing_policy"][key] = 1700000000000
        assert_invalid(
            validate_experiment_contract(request),
            "STATIC_TIMING_POLICY_INVALID",
            "STATIC_TIMING_FIELDS",
        )


def test_bar_duration_must_equal_timeframe():
    assert_invalid(
        validate_experiment_contract(timing(bar_duration_ms=DAY_MS // 2)),
        "STATIC_TIMING_POLICY_INVALID",
        "STATIC_TIMING_FIELDS",
    )


@pytest.mark.parametrize("value", [0, -1])
def test_bar_duration_must_be_positive(value):
    assert_invalid(
        validate_experiment_contract(timing(bar_duration_ms=value)),
        "UTC_MS_INVALID",
        "STATIC_TIMING_FIELDS",
    )


@pytest.mark.parametrize("field", ["settlement_lag_ms", "signal_delay_ms"])
@pytest.mark.parametrize("value", [-1, DAY_MS + 1])
def test_settlement_and_signal_offsets_are_bounded_by_one_bar(field, value):
    request = timing(**{field: value})
    if field == "settlement_lag_ms" and value > 0:
        request["static_timing_policy"]["feature_availability_rule"] = (
            "AT_BAR_CLOSE_PLUS_SETTLEMENT"
        )
    assert_invalid(
        validate_experiment_contract(request), "UTC_MS_INVALID", "STATIC_TIMING_FIELDS"
    )


@pytest.mark.parametrize("value", [-1, DAY_MS + 1])
def test_order_submission_latency_is_bounded_by_one_bar(value):
    assert_invalid(
        validate_experiment_contract(timing(order_submission_latency_ms=value)),
        "STATIC_TIMING_POLICY_INVALID",
        "STATIC_TIMING_FIELDS",
    )


def test_at_bar_close_requires_zero_settlement_lag():
    assert_invalid(
        validate_experiment_contract(
            timing(feature_availability_rule="AT_BAR_CLOSE", settlement_lag_ms=1)
        ),
        "STATIC_TIMING_COMBINATION_INVALID",
        "STATIC_TIMING_COMBINATION",
    )


def test_at_bar_close_plus_settlement_requires_non_zero_settlement_lag():
    assert_invalid(
        validate_experiment_contract(
            timing(feature_availability_rule="AT_BAR_CLOSE_PLUS_SETTLEMENT", settlement_lag_ms=0)
        ),
        "STATIC_TIMING_COMBINATION_INVALID",
        "STATIC_TIMING_COMBINATION",
    )


PERMITTED_COMBINATIONS = [
    # (feature rule, settlement, signal delay, latency, submission rule)
    ("AT_BAR_CLOSE", 0, 0, 0, "AT_ORDER_ELIGIBILITY"),
    ("AT_BAR_CLOSE", 0, 0, 0, FIRST_AT_ELIG),
    ("AT_BAR_CLOSE_PLUS_SETTLEMENT", 1000, 0, 0, "AT_ORDER_ELIGIBILITY"),
    ("AT_BAR_CLOSE_PLUS_SETTLEMENT", 1000, 2000, 3000, FIRST_AT_ELIG),
    ("AT_BAR_CLOSE", 0, DAY_MS, DAY_MS, "AT_ORDER_ELIGIBILITY"),
    ("AT_BAR_CLOSE_PLUS_SETTLEMENT", DAY_MS, DAY_MS, DAY_MS, FIRST_AT_ELIG),
]


@pytest.mark.parametrize("combo", PERMITTED_COMBINATIONS)
def test_permitted_timing_combinations_are_accepted(combo):
    feature_rule, settlement, delay, latency, submission_rule = combo
    result = validate_experiment_contract(
        timing(
            feature_availability_rule=feature_rule,
            settlement_lag_ms=settlement,
            signal_delay_ms=delay,
            order_submission_latency_ms=latency,
            order_submission_rule=submission_rule,
        )
    )
    assert_valid(result)


# ---------------------------------------------------------------------------
# Execution causality (brief step 10, R2B A.5 / A.7 / A.9)
# ---------------------------------------------------------------------------


def expected_offsets(feature_rule, bar, settlement, delay, latency):
    bar_available = bar if feature_rule == "AT_BAR_CLOSE" else bar + settlement
    signal_decision = bar_available + delay
    order_eligibility = signal_decision + latency
    return bar_available, signal_decision, order_eligibility


def ceil_to_grid(x, bar):
    return ((x + bar - 1) // bar) * bar


@pytest.mark.parametrize("combo", PERMITTED_COMBINATIONS)
def test_order_eligibility_is_signal_decision_plus_submission_latency(combo):
    feature_rule, settlement, delay, latency, submission_rule = combo
    result = validate_experiment_contract(
        timing(
            feature_availability_rule=feature_rule,
            settlement_lag_ms=settlement,
            signal_delay_ms=delay,
            order_submission_latency_ms=latency,
            order_submission_rule=submission_rule,
        )
    )
    assert_valid(result)
    offsets = result["derived_timing_offsets"]
    bar_available, signal_decision, order_eligibility = expected_offsets(
        feature_rule, DAY_MS, settlement, delay, latency
    )
    assert offsets["bar_available_offset_ms"] == bar_available
    assert offsets["signal_decision_offset_ms"] == signal_decision
    assert offsets["order_eligibility_offset_ms"] == order_eligibility
    assert (
        offsets["order_eligibility_offset_ms"]
        == offsets["signal_decision_offset_ms"] + latency
    )


@pytest.mark.parametrize("combo", PERMITTED_COMBINATIONS)
def test_fill_target_and_nominal_fill_never_precede_order_eligibility(combo):
    feature_rule, settlement, delay, latency, submission_rule = combo
    result = validate_experiment_contract(
        timing(
            feature_availability_rule=feature_rule,
            settlement_lag_ms=settlement,
            signal_delay_ms=delay,
            order_submission_latency_ms=latency,
            order_submission_rule=submission_rule,
        )
    )
    offsets = result["derived_timing_offsets"]
    assert offsets["fill_target"] == FIRST_AT_ELIG
    assert offsets["submission_target"] == submission_rule
    assert offsets["signal_bar_offset"] == 0
    assert offsets["fill_bar_offset_lower_bound"] == 1
    assert offsets["fill_resolution_status"] == "NOMINAL_GRID_RESOLVED"

    eligibility = offsets["order_eligibility_offset_ms"]
    fill = offsets["nominal_earliest_fill_offset_ms"]
    assert fill == ceil_to_grid(eligibility, DAY_MS)
    assert fill >= eligibility                                   # invariant I2
    assert offsets["nominal_earliest_fill_bar_offset"] >= 1       # invariant I3
    assert offsets["nominal_order_submission_offset_ms"] >= eligibility
    assert fill >= offsets["nominal_order_submission_offset_ms"]


def test_bar_zero_exclusion_is_a_theorem_of_order_eligibility():
    """No accepted policy may ever fill on the signal bar (bar offset 0)."""
    for settlement in (0, 1, 999, DAY_MS):
        for delay in (0, 1, DAY_MS):
            for latency in (0, 1, DAY_MS):
                feature_rule = "AT_BAR_CLOSE" if settlement == 0 else (
                    "AT_BAR_CLOSE_PLUS_SETTLEMENT"
                )
                for submission_rule in ("AT_ORDER_ELIGIBILITY", FIRST_AT_ELIG):
                    result = validate_experiment_contract(
                        timing(
                            feature_availability_rule=feature_rule,
                            settlement_lag_ms=settlement,
                            signal_delay_ms=delay,
                            order_submission_latency_ms=latency,
                            order_submission_rule=submission_rule,
                        )
                    )
                    assert_valid(result)
                    offsets = result["derived_timing_offsets"]
                    assert offsets["order_eligibility_offset_ms"] >= DAY_MS
                    assert offsets["nominal_earliest_fill_bar_offset"] >= 1
                    assert (
                        offsets["nominal_earliest_fill_offset_ms"]
                        >= offsets["order_eligibility_offset_ms"]
                    )


def test_settlement_lag_pushes_the_nominal_fill_to_a_later_bar():
    result = validate_experiment_contract(
        timing(
            feature_availability_rule="AT_BAR_CLOSE_PLUS_SETTLEMENT",
            settlement_lag_ms=1,
            signal_delay_ms=0,
            order_submission_latency_ms=0,
        )
    )
    offsets = result["derived_timing_offsets"]
    assert offsets["order_eligibility_offset_ms"] == DAY_MS + 1
    assert offsets["nominal_earliest_fill_offset_ms"] == 2 * DAY_MS
    assert offsets["nominal_earliest_fill_bar_offset"] == 2


def test_fill_bar_offset_lower_bound_is_a_lower_bound_not_an_equality():
    """R2B invariant I5 -- the accepted fill bar may be strictly later than 1."""
    late = validate_experiment_contract(
        timing(
            feature_availability_rule="AT_BAR_CLOSE_PLUS_SETTLEMENT",
            settlement_lag_ms=1,
        )
    )["derived_timing_offsets"]
    assert late["fill_bar_offset_lower_bound"] == 1
    assert late["nominal_earliest_fill_bar_offset"] == 2


def test_nominal_simultaneity_flag():
    simultaneous = validate_experiment_contract(
        timing(order_submission_rule=FIRST_AT_ELIG)
    )["derived_timing_offsets"]
    assert simultaneous["nominal_fill_simultaneity"] == "SIMULTANEOUS"

    strictly_after = validate_experiment_contract(
        timing(
            feature_availability_rule="AT_BAR_CLOSE_PLUS_SETTLEMENT",
            settlement_lag_ms=1,
            order_submission_rule="AT_ORDER_ELIGIBILITY",
        )
    )["derived_timing_offsets"]
    assert strictly_after["nominal_fill_simultaneity"] == "STRICTLY_AFTER"


def test_nominal_offsets_are_deferred_outside_continuous_mode():
    """R2B A.9: nominal fields are null and the fill bar is unresolved."""
    for factory in (session_request, sparse_request):
        offsets = validate_experiment_contract(factory())["derived_timing_offsets"]
        assert offsets["fill_resolution_status"] == "DEFERRED_TO_COVERAGE_VALIDATOR"
        for field in (
            "nominal_order_submission_offset_ms",
            "nominal_earliest_fill_offset_ms",
            "nominal_earliest_fill_bar_offset",
            "nominal_submission_bar_offset",
            "nominal_fill_simultaneity",
        ):
            assert offsets[field] is None, field
        # Structural offsets remain computable in every mode.
        assert offsets["bar_available_offset_ms"] == DAY_MS
        assert offsets["order_eligibility_offset_ms"] == DAY_MS
        assert offsets["fill_target"] == FIRST_AT_ELIG


def test_execution_invariants_block_is_emitted_verbatim():
    """R2B D-1 byte-exactness."""
    for factory in (base_request, session_request, sparse_request):
        result = validate_experiment_contract(factory())
        assert result["execution_invariants"] == EXPECTED_EXECUTION_INVARIANTS
        assert len(result["execution_invariants"]) == 5


# ===========================================================================
# 13.6 -- Boundary structure, alignment and dataset containment
# ===========================================================================


@pytest.mark.parametrize("name", ["train", "val", "holdout"])
def test_reversed_interval_is_refused(name):
    request = base_request()
    interval = request["boundaries"][name]
    start, end = interval["start_ms"], interval["end_ms"]
    interval["start_ms"], interval["end_ms"] = end, start
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_REVERSED", "BOUNDARY_STRUCTURE")


@pytest.mark.parametrize("name", ["train", "val", "holdout"])
def test_empty_interval_is_refused(name):
    """Half-open [start, end) with start == end contains no bar."""
    request = base_request()
    interval = request["boundaries"][name]
    interval["end_ms"] = interval["start_ms"]
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_EMPTY", "BOUNDARY_STRUCTURE")


def test_boundary_reversal_is_tested_before_emptiness():
    """A reversed interval is never reported as merely empty."""
    request = base_request()
    request["boundaries"]["val"]["start_ms"] = VAL_END
    request["boundaries"]["val"]["end_ms"] = VAL_START
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_REVERSED", "BOUNDARY_STRUCTURE")


def test_validation_order_val_start_before_train_start_is_order_invalid():
    request = base_request()
    request["boundaries"]["train"]["start_ms"] = FIRST_OPEN + 10 * DAY_MS
    request["boundaries"]["train"]["end_ms"] = FIRST_OPEN + 200 * DAY_MS
    request["boundaries"]["val"]["start_ms"] = FIRST_OPEN
    request["boundaries"]["val"]["end_ms"] = FIRST_OPEN + 5 * DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_ORDER_INVALID", "BOUNDARY_STRUCTURE")


def test_holdout_start_before_val_start_is_order_invalid():
    """Hardening R1 ruling 1: within BOUNDARY_STRUCTURE the evaluation order is
    reversed -> empty -> chronological order -> overlap.  A holdout that starts
    before val is therefore BOUNDARY_ORDER_INVALID, never BOUNDARY_OVERLAP.
    Exactly one outcome is accepted."""
    request = base_request()
    request["boundaries"]["holdout"]["start_ms"] = TRAIN_START
    request["boundaries"]["holdout"]["end_ms"] = TRAIN_START + DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_ORDER_INVALID", "BOUNDARY_STRUCTURE")
    assert result["reason_code"] != "BOUNDARY_OVERLAP"


def test_boundary_structure_evaluation_order_is_reversed_empty_order_overlap():
    """Ruling 1: each earlier limb wins when several defects are present."""
    # reversed beats empty, order and overlap
    request = base_request()
    request["boundaries"]["holdout"]["start_ms"] = HOLDOUT_END
    request["boundaries"]["holdout"]["end_ms"] = TRAIN_START
    assert_invalid(
        validate_experiment_contract(request), "BOUNDARY_REVERSED", "BOUNDARY_STRUCTURE"
    )
    # empty beats order and overlap
    request = base_request()
    request["boundaries"]["val"]["start_ms"] = TRAIN_START
    request["boundaries"]["val"]["end_ms"] = TRAIN_START
    assert_invalid(
        validate_experiment_contract(request), "BOUNDARY_EMPTY", "BOUNDARY_STRUCTURE"
    )
    # order beats overlap
    request = base_request()
    request["boundaries"]["holdout"]["start_ms"] = TRAIN_START
    request["boundaries"]["holdout"]["end_ms"] = TRAIN_START + DAY_MS
    assert_invalid(
        validate_experiment_contract(request), "BOUNDARY_ORDER_INVALID", "BOUNDARY_STRUCTURE"
    )


def test_train_overlapping_val_is_refused():
    request = base_request()
    request["boundaries"]["train"]["end_ms"] = VAL_START + DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_OVERLAP", "BOUNDARY_STRUCTURE")


def test_val_overlapping_holdout_is_refused():
    request = base_request()
    request["boundaries"]["val"]["end_ms"] = HOLDOUT_START + DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_OVERLAP", "BOUNDARY_STRUCTURE")


def test_adjacent_intervals_with_zero_gap_are_accepted():
    """Half-open semantics: train.end == val.start is adjacency, not overlap."""
    request = base_request()
    request["boundaries"]["train"]["end_ms"] = VAL_START
    request["boundaries"]["val"]["end_ms"] = HOLDOUT_START
    result = validate_experiment_contract(request)
    assert_valid(result)


@pytest.mark.parametrize(
    "path",
    [
        ("boundaries", "train", "start_ms"),
        ("boundaries", "train", "end_ms"),
        ("boundaries", "val", "start_ms"),
        ("boundaries", "val", "end_ms"),
        ("boundaries", "holdout", "start_ms"),
        ("boundaries", "holdout", "end_ms"),
    ],
)
def test_every_boundary_edge_must_be_grid_aligned_under_continuous(path):
    result = validate_experiment_contract(mutate(path, _edge_value(path) + 1))
    assert_invalid(result, "BOUNDARY_NOT_ALIGNED", "BOUNDARY_ALIGNMENT")


def _edge_value(path):
    node = base_request()
    for key in path:
        node = node[key]
    return node


def test_misaligned_boundary_is_accepted_outside_continuous_mode():
    """Alignment is deferred, never asserted, for SESSION_CALENDAR."""
    request = session_request()
    request["boundaries"]["val"]["start_ms"] += 1
    result = validate_experiment_contract(request)
    assert_valid(result)
    assert statuses_of(result)["BOUNDARY_ALIGNMENT"] == "NOT_APPLICABLE"


def test_misaligned_boundary_is_accepted_under_sparse_declared():
    request = sparse_request()
    request["boundaries"]["train"]["end_ms"] += 7
    request["boundaries"]["val"]["start_ms"] += 7
    result = validate_experiment_contract(request)
    assert_valid(result)
    assert statuses_of(result)["BOUNDARY_ALIGNMENT"] == "NOT_APPLICABLE"


def test_train_start_before_dataset_first_open_is_out_of_dataset():
    request = base_request()
    request["boundaries"]["train"]["start_ms"] = FIRST_OPEN - DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_OUT_OF_DATASET", "BOUNDARY_ALIGNMENT")


def test_holdout_end_after_dataset_end_exclusive_is_out_of_dataset():
    request = base_request()
    request["boundaries"]["holdout"]["end_ms"] = DATASET_END + DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_OUT_OF_DATASET", "BOUNDARY_ALIGNMENT")


def test_holdout_end_exactly_at_dataset_end_exclusive_is_accepted():
    """dataset_end_exclusive_ms is the sole exclusive upper containment bound."""
    request = base_request()
    assert request["boundaries"]["holdout"]["end_ms"] == DATASET_END
    assert_valid(validate_experiment_contract(request))


def test_holdout_end_one_ms_past_dataset_end_exclusive_is_refused():
    """Hardening R1 ruling 2: within check step 9, row 12 (alignment) is
    evaluated before row 13 (containment), matching the frozen truth-table
    order.  An edge that is both misaligned and out of the dataset is therefore
    BOUNDARY_NOT_ALIGNED.  Exactly one outcome is accepted."""
    request = base_request()
    request["boundaries"]["holdout"]["end_ms"] = DATASET_END + 1
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_NOT_ALIGNED", "BOUNDARY_ALIGNMENT")
    assert result["reason_code"] != "BOUNDARY_OUT_OF_DATASET"


def test_alignment_precedes_containment_on_the_lower_edge_too():
    """Ruling 2 applies symmetrically to row 13a."""
    request = base_request()
    request["boundaries"]["train"]["start_ms"] = FIRST_OPEN - DAY_MS + 1
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_NOT_ALIGNED", "BOUNDARY_ALIGNMENT")


def test_containment_without_misalignment_is_reported_as_containment():
    """Ruling 2 non-vacuity: when alignment holds, containment is still fatal."""
    request = base_request()
    request["boundaries"]["holdout"]["end_ms"] = DATASET_END + DAY_MS
    assert_invalid(
        validate_experiment_contract(request), "BOUNDARY_OUT_OF_DATASET", "BOUNDARY_ALIGNMENT"
    )


def test_containment_row_13a_precedes_row_13b():
    """Both containment defects present -> the lower-edge defect is reported."""
    request = base_request()
    request["boundaries"]["train"]["start_ms"] = FIRST_OPEN - DAY_MS
    request["boundaries"]["holdout"]["end_ms"] = DATASET_END + DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_OUT_OF_DATASET", "BOUNDARY_ALIGNMENT")
    # 13a and 13b share one reason code; the check must still be single-FAIL.
    assert status_list(result).count("FAIL") == 1


def test_containment_is_enforced_in_non_continuous_modes_too():
    """NOT_APPLICABLE is reported only when containment also passes."""
    request = session_request()
    request["boundaries"]["holdout"]["end_ms"] = (
        request["dataset_identity"]["dataset_end_exclusive_ms"] + 1
    )
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_OUT_OF_DATASET", "BOUNDARY_ALIGNMENT")
    assert "NOT_APPLICABLE" not in status_list(result)


@pytest.mark.parametrize("builder", [session_request, sparse_request])
def test_ruling5_step9_is_never_not_applicable_and_fail_together(builder):
    """Hardening R1 ruling 5 (frozen correction to R2B example 3): step 9 is
    NOT_APPLICABLE only when alignment is out of scope AND containment passes;
    it is FAIL when containment fails; it can never be both."""
    passing = validate_experiment_contract(builder())
    assert_valid(passing)
    assert statuses_of(passing)["BOUNDARY_ALIGNMENT"] == "NOT_APPLICABLE"

    request = builder()
    request["boundaries"]["train"]["start_ms"] = (
        request["dataset_identity"]["first_open_ms"] - 1
    )
    failing = validate_experiment_contract(request)
    assert_invalid(failing, "BOUNDARY_OUT_OF_DATASET", "BOUNDARY_ALIGNMENT")
    assert statuses_of(failing)["BOUNDARY_ALIGNMENT"] == "FAIL"
    assert "NOT_APPLICABLE" not in status_list(failing)


def test_boundary_structure_precedes_boundary_alignment():
    """A structural defect is never reported as a containment or grid defect."""
    request = base_request()
    request["boundaries"]["train"]["start_ms"] = FIRST_OPEN - DAY_MS + 1
    request["boundaries"]["train"]["end_ms"] = FIRST_OPEN - 2 * DAY_MS
    result = validate_experiment_contract(request)
    assert_invalid(result, "BOUNDARY_REVERSED", "BOUNDARY_STRUCTURE")


@pytest.mark.parametrize("name", ["train", "val", "holdout"])
@pytest.mark.parametrize("edge", ["start_ms", "end_ms"])
def test_boundary_edges_must_be_absolute_utc_ms(name, edge):
    result = validate_experiment_contract(mutate(("boundaries", name, edge), 12345))
    assert_invalid(result, "UTC_MS_OUT_OF_RANGE", "BOUNDARY_STRUCTURE")


def test_boundaries_unknown_field_is_refused():
    request = base_request()
    request["boundaries"]["extra"] = 1
    result = validate_experiment_contract(request)
    assert_invalid(result, "UNKNOWN_FIELD_PRESENT")


@pytest.mark.parametrize("name", ["train", "val", "holdout"])
def test_boundaries_missing_interval_is_refused(name):
    result = validate_experiment_contract(drop(("boundaries", name)))
    assert_invalid(result, "MISSING_FIELD")


@pytest.mark.parametrize("name", ["train", "val", "holdout"])
@pytest.mark.parametrize("edge", ["start_ms", "end_ms"])
def test_interval_missing_edge_is_refused(name, edge):
    result = validate_experiment_contract(drop(("boundaries", name, edge)))
    assert_invalid(result, "MISSING_FIELD")


@pytest.mark.parametrize("name", ["train", "val", "holdout"])
def test_interval_unknown_edge_is_refused(name):
    request = base_request()
    request["boundaries"][name]["midpoint_ms"] = TRAIN_END
    result = validate_experiment_contract(request)
    assert_invalid(result, "UNKNOWN_FIELD_PRESENT")


def test_boundaries_hash_is_bound_into_experiment_id():
    baseline = validate_experiment_contract(base_request())
    shifted = base_request()
    shifted["boundaries"]["val"]["end_ms"] = VAL_END - DAY_MS
    shifted["boundaries"]["holdout"]["start_ms"] = VAL_END
    moved = validate_experiment_contract(shifted)
    assert_valid(moved)
    assert moved["experiment_id"] != baseline["experiment_id"]
    assert moved["research_program_id"] == baseline["research_program_id"]


# ===========================================================================
# 13.7 -- Purity, import graph and static source properties
# ===========================================================================

ALLOWED_IMPORTS = {"hashlib", "json", "re", "unicodedata", "typing"}

FORBIDDEN_IMPORTS = {
    "os", "sys", "io", "pathlib", "shutil", "tempfile", "glob",
    "time", "datetime", "calendar", "zoneinfo",
    "random", "secrets", "uuid",
    "socket", "http", "urllib", "requests", "httpx", "ssl", "ftplib",
    "subprocess", "multiprocessing", "threading", "asyncio",
    "sqlite3", "pickle", "shelve", "csv",
    "decimal", "fractions", "math", "statistics", "numpy", "pandas",
    "logging", "warnings",
}

# NOTE: ``float`` itself is NOT forbidden as a name: the type-discipline check
# must reference it in ``isinstance(value, float)`` in order to REJECT floats.
# Float *arithmetic* is excluded by the float-literal and true-division tests.
FORBIDDEN_NAMES = {
    "open", "input", "eval", "exec", "compile", "__import__",
    "globals", "locals", "vars", "print", "round", "sum", "divmod",
}


def module_source():
    module = importlib.import_module(MODULE_NAME)
    return inspect.getsource(module)


def module_tree():
    return ast.parse(module_source())


def test_module_imports_only_the_allowed_standard_library_modules():
    imported = set()
    for node in ast.walk(module_tree()):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError("relative import found: level=%d" % node.level)
            imported.add((node.module or "").split(".")[0])
    assert imported <= ALLOWED_IMPORTS, sorted(imported - ALLOWED_IMPORTS)
    assert not (imported & FORBIDDEN_IMPORTS), sorted(imported & FORBIDDEN_IMPORTS)


def test_module_does_not_import_any_phase_four_or_project_module():
    for node in ast.walk(module_tree()):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app."), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("app."), node.module


def test_module_contains_no_imports_below_module_level():
    tree = module_tree()
    top_level = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top_level.add(id(node))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert id(node) in top_level, "deferred import found at line %d" % node.lineno


def test_module_never_references_forbidden_builtins_or_io():
    for node in ast.walk(module_tree()):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            assert node.id not in FORBIDDEN_NAMES, (
                "forbidden name %r at line %d" % (node.id, node.lineno)
            )
        if isinstance(node, ast.Attribute):
            assert node.attr not in (
                "time", "time_ns", "now", "utcnow", "today", "monotonic",
                "random", "randint", "choice", "shuffle", "seed", "urandom",
                "read", "write", "readlines", "writelines", "urlopen", "connect",
                "system", "popen", "getenv", "environ",
            ), "forbidden attribute %r at line %d" % (node.attr, node.lineno)


def test_module_contains_no_floating_point_literals():
    for node in ast.walk(module_tree()):
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), (
                "float literal at line %d" % node.lineno
            )
            assert not isinstance(node.value, complex), (
                "complex literal at line %d" % node.lineno
            )


def test_module_contains_no_true_division_operator():
    """Integer arithmetic only -- '/' would introduce a float."""
    for node in ast.walk(module_tree()):
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, ast.Div), (
                "true division at line %d" % node.lineno
            )
        if isinstance(node, ast.AugAssign):
            assert not isinstance(node.op, ast.Div), (
                "augmented true division at line %d" % node.lineno
            )


def test_module_has_no_executable_top_level_side_effects():
    allowed = (
        ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
        ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Expr,
    )
    for node in module_tree().body:
        assert isinstance(node, allowed), type(node).__name__
        if isinstance(node, ast.Expr):
            # Only docstrings are permitted as bare top-level expressions.
            assert isinstance(node.value, ast.Constant), node.lineno
            assert isinstance(node.value.value, str), node.lineno
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None:
                continue
            for sub in ast.walk(value):
                assert not isinstance(sub, ast.Call) or _is_pure_literal_call(sub), (
                    "top-level call at line %d" % node.lineno
                )


def _is_pure_literal_call(node):
    """Permit only constant folding helpers used to build frozen vocabularies."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"tuple", "frozenset", "set", "range", "len", "sorted"}
    if isinstance(func, ast.Attribute):
        # e.g. hashlib.sha256(b"").hexdigest() for the empty-hash placeholder,
        # and str/tuple methods used to build frozen constants.
        return func.attr in {
            "sha256", "hexdigest", "digest", "encode", "join", "index", "compile",
        }
    return False


def test_module_defines_the_frozen_public_surface():
    module = importlib.import_module(MODULE_NAME)
    assert module.__all__ == ["validate_experiment_contract"]
    assert callable(module.validate_experiment_contract)


def test_module_import_is_idempotent_and_state_free():
    first = importlib.import_module(MODULE_NAME)
    second = importlib.reload(first)
    baseline = validate_experiment_contract(base_request())
    after = second.validate_experiment_contract(base_request())
    assert after["contract_hash"] == baseline["contract_hash"]


def test_validator_signature_is_the_frozen_contract():
    module = importlib.import_module(MODULE_NAME)
    signature = inspect.signature(module.validate_experiment_contract)
    assert list(signature.parameters) == ["request"]
    parameter = signature.parameters["request"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_no_module_level_mutable_registry_is_mutated_by_validation():
    module = importlib.import_module(MODULE_NAME)
    snapshot = {}
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue  # interpreter-managed namespace, not module state
        if isinstance(value, (list, dict, set)):
            snapshot[name] = copy.deepcopy(value)
    for factory in (base_request, session_request, sparse_request):
        validate_experiment_contract(factory())
    validate_experiment_contract({"schema_version": "nope"})
    for name, value in snapshot.items():
        assert vars(module)[name] == value, name


# ===========================================================================
# 13.8 -- Output schema, closed vocabularies and never-raises behaviour
# ===========================================================================


def test_reason_code_vocabulary_is_exactly_the_frozen_forty_three():
    module = importlib.import_module(MODULE_NAME)
    codes = list(module.REASON_CODES)
    assert len(codes) == 43
    assert len(set(codes)) == 43, "duplicate reason code"
    assert codes == EXPECTED_REASON_CODES, [
        (i, a, b)
        for i, (a, b) in enumerate(zip(codes, EXPECTED_REASON_CODES))
        if a != b
    ]


def test_check_id_vocabulary_is_exactly_the_frozen_fourteen():
    module = importlib.import_module(MODULE_NAME)
    ids = list(module.CHECK_IDS)
    assert len(ids) == 14
    assert len(set(ids)) == 14
    assert ids == EXPECTED_CHECK_IDS


def test_check_status_vocabulary_is_closed():
    module = importlib.import_module(MODULE_NAME)
    assert list(module.CHECK_STATUSES) == [
        "PASS",
        "FAIL",
        "NOT_EVALUATED",
        "NOT_APPLICABLE",
    ]
    assert set(module.CHECK_STATUSES) == EXPECTED_STATUSES
    assert len(module.CHECK_STATUSES) == 4


def test_every_emitted_reason_code_belongs_to_the_frozen_vocabulary():
    """Sweep every negative case exercised in this module."""
    seen = set()
    for request in _negative_corpus():
        result = validate_experiment_contract(request)
        assert_output_shape(result)
        if result["contract_status"] == "INVALID":
            assert result["reason_code"] in EXPECTED_REASON_CODES, result["reason_code"]
            seen.add(result["reason_code"])
    assert seen, "negative corpus produced no failures"
    assert seen <= set(EXPECTED_REASON_CODES)


def _negative_corpus():
    """Deliberately malformed requests spanning every check step."""
    corpus = [
        None,
        [],
        "",
        0,
        {},
        {"schema_version": "wp5_0a1.request.v3"},
        mutate(("research_scope", "universe_hash"), "zz"),
        mutate(("dataset_identity", "dataset_hash"), "0" * 64),
        mutate(("dataset_identity", "rows"), 0),
        mutate(("dataset_identity", "coverage_mode"), "MYSTERY"),
        mutate(("dataset_identity", "timeframe_ms"), 0),
        mutate(("dataset_identity", "dataset_end_exclusive_ms"), LAST_OPEN),
        mutate(("static_timing_policy", "fill_rule"), "SAME_BAR_CLOSE"),
        mutate(("static_timing_policy", "bar_duration_ms"), DAY_MS + 1),
        mutate(("static_timing_policy", "settlement_lag_ms"), -1),
        mutate(("boundaries", "train", "end_ms"), TRAIN_START),
        mutate(("boundaries", "holdout", "end_ms"), DATASET_END + DAY_MS),
        mutate(("boundaries", "val", "start_ms"), VAL_START + 1),
        mutate(("experiment_configuration", "strategy_id"), "!!"),
        mutate(("research_program_registration", "owner"), "!!"),
        mutate(("timing_ledger_schema_version",), "nope"),
        drop(("preregistration",)),
    ]
    for path in [
        ("dataset_identity", "rows"),
        ("boundaries", "train", "start_ms"),
        ("static_timing_policy", "signal_delay_ms"),
    ]:
        corpus.append(mutate(path, True))
        corpus.append(mutate(path, 1.0))
        corpus.append(mutate(path, None))
    unknown = base_request()
    unknown["surprise"] = 1
    corpus.append(unknown)
    self_ref = base_request()
    self_ref["research_scope"]["contract_hash"] = h("x")
    corpus.append(self_ref)
    admin = base_request()
    admin["experiment_configuration"]["owner"] = "anteneh"
    corpus.append(admin)
    trial = base_request()
    trial["trial_id"] = 1
    corpus.append(trial)
    return corpus


def test_invalid_output_omits_derived_blocks_and_nulls_the_identity_triple():
    result = validate_experiment_contract(mutate(("dataset_identity", "rows"), 0))
    assert result["contract_status"] == "INVALID"
    assert result["research_program_id"] is None
    assert result["experiment_id"] is None
    assert result["counter_key"] is None
    for block in (
        "research_scope",
        "dataset_identity",
        "boundaries",
        "static_timing_policy",
        "experiment_configuration",
        "research_program_registration",
        "preregistration",
        "derived_timing_offsets",
        "timing_ledger_schema_version",
    ):
        assert block not in result, block

    # I-2: the INVALID body is exactly this closed key set and nothing more.
    assert set(result.keys()) == {
        "schema_version",
        "contract_status",
        "reason_code",
        "research_program_id",
        "experiment_id",
        "counter_key",
        "checks",
        "execution_invariants",
        "limitations",
        "contract_hash",
    }, sorted(result.keys())


def test_valid_output_emits_the_full_identity_chain():
    result = validate_experiment_contract(base_request())
    assert_valid(result)
    for key in ("research_program_id", "experiment_id", "counter_key"):
        assert isinstance(result[key], str) and len(result[key]) == 64
    assert result["counter_key"] == result["research_program_id"]

    # Non-vacuity guard for the INVALID-omission test: every block listed there
    # as absent from an INVALID body must be PRESENT in a VALID body.
    for block in (
        "research_scope",
        "dataset_identity",
        "boundaries",
        "static_timing_policy",
        "experiment_configuration",
        "research_program_registration",
        "preregistration",
        "derived_timing_offsets",
        "timing_ledger_schema_version",
    ):
        assert block in result, block
    assert result["dataset_identity"]["dataset_identity_hash"]
    assert result["boundaries"]["boundaries_hash"]
    assert result["static_timing_policy"]["static_timing_policy_hash"]
    assert (
        result["experiment_configuration"]["experiment_configuration_hash"]
        == result["experiment_id"]
    )
    assert result["research_program_registration"]["registration_record_hash"]
    assert result["preregistration"]["preregistration_record_hash"]


def test_output_contains_no_trial_or_counter_fields():
    module = importlib.import_module(MODULE_NAME)
    for factory in (base_request, session_request, sparse_request):
        result = validate_experiment_contract(factory())
        blob = canonical_bytes(result).decode("utf-8")
        for token in module.TRIAL_KEYS + module.COUNTER_KEYS:
            assert '"%s"' % token not in blob, token


def test_output_is_canonically_serialisable():
    """Every emitted value must survive canonical JSON with allow_nan=False."""
    for request in _negative_corpus() + [base_request(), session_request(), sparse_request()]:
        result = validate_experiment_contract(request)
        canonical_bytes(result)


def test_limitations_block_is_nineteen_frozen_single_line_strings():
    result = validate_experiment_contract(base_request())
    limitations = result["limitations"]
    assert len(limitations) == 19
    assert limitations == EXPECTED_LIMITATIONS
    for line in limitations:
        assert isinstance(line, str)
        assert "\n" not in line
        assert line == line.strip()
        assert "  " not in line


def test_contract_hash_covers_the_whole_body():
    """Any observable output difference changes contract_hash."""
    seen = {}
    for index, request in enumerate(_negative_corpus()):
        result = validate_experiment_contract(request)
        body = {k: v for k, v in result.items() if k != "contract_hash"}
        key = canonical_bytes(body)
        digest = result["contract_hash"]
        if key in seen:
            assert seen[key] == digest
        else:
            seen[key] = digest
    for a_key, a_digest in seen.items():
        for b_key, b_digest in seen.items():
            if a_key != b_key:
                assert a_digest != b_digest


def test_validator_never_raises_on_hostile_input():
    hostile = [
        None, True, False, 0, -1, 1.5, "x", b"x", (), [], set(),
        {None: 1}, {1: 2}, {(): 3}, {"schema_version": None},
        {"schema_version": []}, {"schema_version": {}},
        [{"schema_version": "wp5_0a1.request.v4"}],
        {"schema_version": "wp5_0a1.request.v4"},
    ]
    deep = {"schema_version": "wp5_0a1.request.v4"}
    node = deep
    for _ in range(50):
        node["research_scope"] = {}
        node = node["research_scope"]
    hostile.append(deep)
    # Hardening R2 finding 1: this fixture was previously named "cyclic_safe",
    # which was inaccurate -- it contains no reference cycle at all, only nested
    # acyclic containers, and therefore never exercised cycle handling.  Renamed
    # to what it actually is.  Genuine cycles are covered by the dedicated
    # section "Hardening R2 finding 1" at the end of this file.
    acyclic_nested_containers = base_request()
    acyclic_nested_containers["research_scope"]["universe_hash"] = {"nested": [1, [2, [3]]]}
    hostile.append(acyclic_nested_containers)
    for item in hostile:
        result = validate_experiment_contract(item)
        assert isinstance(result, dict)
        assert result["contract_status"] in ("VALID", "INVALID")
        assert_output_shape(result)


def test_validator_is_a_pure_function_of_its_argument():
    """Two structurally equal but distinct requests give identical output."""
    a = validate_experiment_contract(base_request())
    b = validate_experiment_contract(copy.deepcopy(base_request()))
    assert canonical_bytes(a) == canonical_bytes(b)


def test_repeated_validation_of_invalid_input_is_stable():
    request = mutate(("dataset_identity", "rows"), 0)
    first = validate_experiment_contract(request)
    second = validate_experiment_contract(copy.deepcopy(request))
    assert canonical_bytes(first) == canonical_bytes(second)


# ===========================================================================
# D-2 / D-3 / D-4 -- disclosure evidence
# ===========================================================================


def test_d2_no_check_reports_a_deferred_property_as_verified():
    """D-2: deferral statuses are never upgraded to verification statuses."""
    # Non-vacuity guard: the verification tokens DO appear under CONTINUOUS_24_7,
    # so their absence below is a real property of the non-continuous modes.
    continuous = canonical_bytes(validate_experiment_contract(base_request())).decode("utf-8")
    for token in (
        "GRID_ARITHMETIC_VERIFIED",
        "GRID_DERIVED_END_VERIFIED",
        "GRID_ALIGNMENT_VERIFIED",
        "NOMINAL_GRID_RESOLVED",
    ):
        assert token in continuous, token
    for token in (
        "IDENTITY_BOUND_ONLY_MEMBERSHIP_DEFERRED",
        "CALLER_ASSERTED_END_UNVERIFIED",
        "ALIGNMENT_DEFERRED_TO_COVERAGE_VALIDATOR",
    ):
        assert token not in continuous, token

    for factory in (session_request, sparse_request):
        result = validate_experiment_contract(factory())
        assert_valid(result)
        blob = canonical_bytes(result).decode("utf-8")
        for token in (
            "GRID_ARITHMETIC_VERIFIED",
            "GRID_DERIVED_END_VERIFIED",
            "GRID_ALIGNMENT_VERIFIED",
            "NOMINAL_GRID_RESOLVED",
        ):
            assert token not in blob, token
        assert "IDENTITY_BOUND_ONLY_MEMBERSHIP_DEFERRED" in blob
        assert "CALLER_ASSERTED_END_UNVERIFIED" in blob
        assert "ALIGNMENT_DEFERRED_TO_COVERAGE_VALIDATOR" in blob
        assert "DEFERRED_TO_COVERAGE_VALIDATOR" in blob


def test_d3_no_output_asserts_live_execution_or_release_eligibility():
    for factory in (base_request, session_request, sparse_request):
        blob = canonical_bytes(validate_experiment_contract(factory())).decode("utf-8")
        for token in (
            "live_execution_enabled",
            "live_execution_eligible",
            "release_eligible",
            "production_allowlist",
        ):
            assert token not in blob, token


def test_d4_same_bar_close_token_never_appears_in_an_accepted_output():
    for factory in (base_request, session_request, sparse_request):
        blob = canonical_bytes(validate_experiment_contract(factory())).decode("utf-8")
        assert "SAME_BAR_CLOSE" not in blob


# ===========================================================================
# Hardening R2 finding 1 -- totality under cyclic and deeply nested input
#
# The validator is a total function: it must return a well-formed INVALID
# contract for every input, including hostile structural shapes, and must never
# raise.  Before this hardening round a reference cycle or a sufficiently deep
# nesting chain exhausted the interpreter stack inside the recursive
# forbidden-key / non-string-key / type walks and raised RecursionError.
#
# Frozen semantics under test (independently retyped here, never imported):
#   * depth is defined on container nodes only; the root request is depth 0;
#   * a container nested directly inside a depth-d container has depth d + 1;
#   * depth <= 64 is permitted, depth > 64 is INVALID;
#   * a reference cycle at any depth is INVALID;
#   * both produce reason_code TYPE_INVALID at failing check INPUT_ENVELOPE;
#   * RecursionError is never raised.
# ===========================================================================


FROZEN_MAX_INPUT_NESTING_DEPTH = 64

# Independently retyped, never imported from the module under test.
REQUEST_SCHEMA_VERSION = "wp5_0a1.request.v4"


def _container_chain(depth):
    """A request-rooted chain whose deepest container node is at ``depth``."""
    root = {"schema_version": REQUEST_SCHEMA_VERSION}
    node = root
    for _ in range(depth):
        node["nested"] = {}
        node = node["nested"]
    return root


def _call_without_raising(request):
    """Call the validator and fail the test loudly if it raises anything."""
    try:
        return validate_experiment_contract(request)
    except RecursionError:  # pragma: no cover - the defect this section pins
        pytest.fail("validate_experiment_contract raised RecursionError")
    except BaseException as exc:  # pragma: no cover - totality violation
        pytest.fail(
            "validate_experiment_contract raised %s: %s" % (type(exc).__name__, exc)
        )


def test_frozen_structural_limit_is_sixty_four():
    """The module's frozen bound equals the independently retyped value."""
    module = importlib.import_module(MODULE_NAME)
    assert module.MAX_INPUT_NESTING_DEPTH == FROZEN_MAX_INPUT_NESTING_DEPTH


def test_dictionary_directly_referencing_itself_is_refused():
    """D.1: a dictionary that is its own value."""
    request = {"schema_version": REQUEST_SCHEMA_VERSION}
    request["research_scope"] = request
    result = _call_without_raising(request)
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_list_directly_referencing_itself_is_refused():
    """D.2: a list that is its own element, reached from a dictionary root."""
    loop = []
    loop.append(loop)
    request = {"schema_version": REQUEST_SCHEMA_VERSION, "research_scope": loop}
    result = _call_without_raising(request)
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_indirect_dictionary_list_dictionary_cycle_is_refused():
    """D.3: dict -> list -> dict back to the original dict."""
    request = {"schema_version": REQUEST_SCHEMA_VERSION}
    request["research_scope"] = [{"back": request}]
    result = _call_without_raising(request)
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_cycle_nested_inside_research_scope_is_refused():
    """D.4: a cycle buried inside an otherwise well-formed request."""
    request = base_request()
    inner = {"level": {}}
    inner["level"]["back"] = inner
    request["research_scope"]["asset_universe_hash"] = inner
    result = _call_without_raising(request)
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_exactly_depth_sixty_four_is_accepted_structurally():
    """D.5: depth 64 clears the structural bound.

    It is still an INVALID request -- it carries none of the required top-level
    blocks -- but it is refused for MISSING_FIELD, not for structure.  A single
    exact outcome is asserted; TYPE_INVALID would mean the bound rejected it.
    """
    result = _call_without_raising(_container_chain(FROZEN_MAX_INPUT_NESTING_DEPTH))
    assert_invalid(result, "MISSING_FIELD", "INPUT_ENVELOPE")
    assert result["reason_code"] != "TYPE_INVALID"


def test_depth_sixty_five_is_refused_as_type_invalid_at_input_envelope():
    """D.6: one level past the frozen bound."""
    result = _call_without_raising(_container_chain(FROZEN_MAX_INPUT_NESTING_DEPTH + 1))
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_pathologically_deep_input_returns_instead_of_raising():
    """D.6 (extreme): far past the interpreter's own recursion limit."""
    result = _call_without_raising(_container_chain(20000))
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_repeated_non_cyclic_shared_object_is_not_treated_as_a_cycle():
    """D.7: identity in two separate completed branches is not a cycle.

    Cycle detection is scoped to the active ancestor path.  A shared object
    referenced twice from sibling branches must survive the structural bound;
    the request is then refused for its missing blocks, not for its structure.
    """
    shared = {"nested": {"deeper": [1, 2, 3]}}
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "research_scope": shared,
        "preregistration": shared,
    }
    result = _call_without_raising(request)
    assert_invalid(result, "MISSING_FIELD", "INPUT_ENVELOPE")
    assert result["reason_code"] != "TYPE_INVALID"


def test_shared_object_deep_in_two_branches_is_not_treated_as_a_cycle():
    """D.7 (deep variant): sharing at depth, still within the frozen bound."""
    shared = {"leaf": [0]}
    request = {"schema_version": REQUEST_SCHEMA_VERSION}
    left = request["research_scope"] = {}
    right = request["preregistration"] = {}
    for _ in range(30):
        left["nested"] = {}
        left = left["nested"]
        right["nested"] = {}
        right = right["nested"]
    left["shared"] = shared
    right["shared"] = shared
    result = _call_without_raising(request)
    assert_invalid(result, "MISSING_FIELD", "INPUT_ENVELOPE")
    assert result["reason_code"] != "TYPE_INVALID"


def test_valid_request_is_unaffected_by_the_structural_preflight():
    """The bound is a rejection path only: an accepted request is untouched."""
    result = validate_experiment_contract(base_request())
    assert_valid(result)


@pytest.mark.parametrize(
    "builder",
    [
        lambda: _self_referencing_dict(),
        lambda: _self_referencing_list_request(),
        lambda: _indirect_cycle_request(),
        lambda: _container_chain(FROZEN_MAX_INPUT_NESTING_DEPTH + 1),
        lambda: _container_chain(20000),
    ],
)
def test_hostile_structure_always_returns_the_frozen_output_shape(builder):
    """D.8: every structural refusal returns normally and is well formed."""
    result = _call_without_raising(builder())
    assert isinstance(result, dict)
    assert result["contract_status"] == "INVALID"
    assert_output_shape(result)
    assert status_list(result).count("FAIL") == 1


def _self_referencing_dict():
    request = {"schema_version": REQUEST_SCHEMA_VERSION}
    request["research_scope"] = request
    return request


def _self_referencing_list_request():
    loop = []
    loop.append(loop)
    return {"schema_version": REQUEST_SCHEMA_VERSION, "research_scope": loop}


def _indirect_cycle_request():
    request = {"schema_version": REQUEST_SCHEMA_VERSION}
    request["research_scope"] = [{"back": request}]
    return request


def test_cyclic_input_is_not_mutated_by_validation():
    """D.9 (cyclic case): the argument is inspected, never edited.

    Structural equality cannot be used on a cyclic object, so the invariants are
    checked directly: the key set is unchanged and the back-reference still
    points at the same object.
    """
    request = {"schema_version": REQUEST_SCHEMA_VERSION}
    request["research_scope"] = request
    keys_before = sorted(request.keys())

    _call_without_raising(request)

    assert sorted(request.keys()) == keys_before
    assert request["research_scope"] is request
    assert request["schema_version"] == REQUEST_SCHEMA_VERSION


def test_deep_input_is_not_mutated_by_validation():
    """D.9 (deep case): no truncation, no added marker keys."""
    request = _container_chain(FROZEN_MAX_INPUT_NESTING_DEPTH + 1)

    _call_without_raising(request)

    depth = 0
    node = request
    while isinstance(node, dict) and "nested" in node:
        depth += 1
        node = node["nested"]
    assert depth == FROZEN_MAX_INPUT_NESTING_DEPTH + 1
    assert sorted(request.keys()) == ["nested", "schema_version"]


def test_shared_object_input_is_not_mutated_by_validation():
    """D.9 (shared case): the sharing itself survives validation."""
    shared = {"nested": {"deeper": [1, 2, 3]}}
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "research_scope": shared,
        "preregistration": shared,
    }

    _call_without_raising(request)

    assert request["research_scope"] is shared
    assert request["preregistration"] is shared
    assert shared == {"nested": {"deeper": [1, 2, 3]}}


# ===========================================================================
# Hardening R3 -- total traversal work is bounded (MAX_INPUT_NODE_VISITS)
# ===========================================================================
#
# MAX_INPUT_NESTING_DEPTH bounds how deep a request may be.  It does not bound
# how much of it there is.  A shallow acyclic DAG that references the same child
# object at every level stays far inside depth 64, contains no cycle at all, and
# still expands into exponentially many node occurrences across the preflight,
# the forbidden-key scan, the non-string-key scan, the type walk and canonical
# JSON construction.  That is a resource-exhaustion path.  The second frozen
# bound closes it.
#
# Frozen semantics, retyped here independently of the module under test:
#   1. The root request counts as one visit.
#   2. Every mapping value occurrence counts as one visit.
#   3. Every list element occurrence counts as one visit.
#   4. Repeated references count once per occurrence; identity is not deduped.
#   5. Mapping keys are not visits -- they stay under the string-key checks.
#   6. Exactly 100000 visits is permitted.
#   7. More than 100000 visits is rejected.
#   8. Rejection is TYPE_INVALID at INPUT_ENVELOPE.
#   9. MemoryError and RecursionError are never raised.
#  10. The input is never modified.

FROZEN_MAX_INPUT_NODE_VISITS = 100000


def _flat_mapping_of_visits(total_visits):
    """A root mapping whose whole-request visit count is exactly ``total_visits``.

    The root is one visit (rule 1) and each of its scalar values is one more
    (rule 2), so the mapping needs ``total_visits - 1`` entries.  Keys are plain
    strings, so the string-key checks are not implicated (rule 5).
    """
    return dict(("k%06d" % index, 1) for index in range(total_visits - 1))


def _list_bearing_request_of_visits(total_visits):
    """Same exact count, reached through a list instead of a mapping.

    Root mapping (1) + the list itself as a mapping value (1) + one visit per
    list element (rule 3) == ``total_visits``.
    """
    return {"a": [0] * (total_visits - 2)}


def _binary_shared_dag(levels):
    """An acyclic DAG of ``levels`` depth whose occurrence count is exponential.

    Every level holds two references to the *same* next-level object, so the
    graph is tiny in memory and contains no cycle, while the number of node
    occurrences a full traversal would perform is on the order of 2 ** levels.
    At 30 levels that is over two billion occurrences -- unbounded in practice.
    """
    node = {"leaf": 1}
    for _ in range(levels):
        node = {"a": node, "b": node}
    return {"schema_version": REQUEST_SCHEMA_VERSION, "research_scope": node}


def test_frozen_node_visit_budget_is_one_hundred_thousand():
    """The frozen constant is exactly 100000 and is an int, not a bool."""
    from app.research.phase5_identity_contract import MAX_INPUT_NODE_VISITS

    assert MAX_INPUT_NODE_VISITS == FROZEN_MAX_INPUT_NODE_VISITS
    assert isinstance(MAX_INPUT_NODE_VISITS, int)
    assert not isinstance(MAX_INPUT_NODE_VISITS, bool)


def test_binary_shared_dag_beyond_the_budget_is_refused():
    """R3.1: an exponentially expanding acyclic DAG is refused, not traversed.

    Thirty levels of two-way sharing is roughly 2 ** 31 occurrences.  If the
    budget were absent or charged after the fact, this call would not return in
    any usable time.  It must return promptly with the structural refusal.
    """
    result = _call_without_raising(_binary_shared_dag(30))
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_acyclic_wide_list_above_the_budget_is_refused():
    """R3.2: width alone, at depth 2, is enough to exceed the budget."""
    request = {"schema_version": REQUEST_SCHEMA_VERSION, "research_scope": [0] * 200000}
    result = _call_without_raising(request)
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_exactly_one_hundred_thousand_visits_is_accepted_structurally():
    """R3.3: the budget is inclusive -- exactly 100000 visits clears the bound.

    The request is still INVALID; it carries none of the required top-level
    blocks.  It is refused for MISSING_FIELD, which is the point: the structural
    bound did not fire.  The explicit inequality pins non-vacuity.
    """
    result = _call_without_raising(
        _flat_mapping_of_visits(FROZEN_MAX_INPUT_NODE_VISITS)
    )
    assert_invalid(result, "MISSING_FIELD", "INPUT_ENVELOPE")
    assert result["reason_code"] != "TYPE_INVALID"


def test_exactly_one_hundred_thousand_and_one_visits_is_refused():
    """R3.4: one occurrence past the bound flips to the structural refusal."""
    result = _call_without_raising(
        _flat_mapping_of_visits(FROZEN_MAX_INPUT_NODE_VISITS + 1)
    )
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_list_element_occurrences_are_counted_the_same_as_mapping_values():
    """R3.3 / R3.4 again through list elements, pinning rule 3 against rule 2.

    The same two exact counts, reached by a different container kind, must land
    on the same two outcomes.  This is what makes the counting rule frozen
    rather than incidental to mappings.
    """
    at_bound = _call_without_raising(
        _list_bearing_request_of_visits(FROZEN_MAX_INPUT_NODE_VISITS)
    )
    assert_invalid(at_bound, "MISSING_FIELD", "INPUT_ENVELOPE")
    assert at_bound["reason_code"] != "TYPE_INVALID"

    past_bound = _call_without_raising(
        _list_bearing_request_of_visits(FROZEN_MAX_INPUT_NODE_VISITS + 1)
    )
    assert_invalid(past_bound, "TYPE_INVALID", "INPUT_ENVELOPE")


def test_ordinary_shared_object_below_the_budget_is_not_refused():
    """R3.5: sharing is legal.  Below the budget it costs visits and nothing more.

    The same object appears under two separate top-level keys.  It is not a
    cycle -- neither occurrence is an ancestor of the other -- and the pair is
    nowhere near the budget, so neither structural rule may fire.  Exactly one
    outcome is asserted, and it is not TYPE_INVALID.
    """
    shared = {"nested": {"deeper": [1, 2, 3]}}
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "research_scope": shared,
        "preregistration": shared,
    }
    result = _call_without_raising(request)
    assert_invalid(result, "MISSING_FIELD", "INPUT_ENVELOPE")
    assert result["reason_code"] != "TYPE_INVALID"


def test_excessive_visits_outrank_an_out_of_scope_trial_id():
    """R3.6: structural safety runs before recursive out-of-scope scanning.

    A request carrying both an over-budget shape and a trial_id reports
    TYPE_INVALID, not TRIAL_ID_OUT_OF_SCOPE.  It has to: discovering the
    trial_id requires the recursive scan that the budget exists to make safe.
    The ordering ruling for structurally sound requests is unaffected and is
    still pinned by its own tests above.
    """
    request = _flat_mapping_of_visits(FROZEN_MAX_INPUT_NODE_VISITS + 1)
    request["trial_id"] = "T-0001"
    result = _call_without_raising(request)
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")
    assert result["reason_code"] != "TRIAL_ID_OUT_OF_SCOPE"


@pytest.mark.parametrize(
    "builder",
    [
        lambda: _binary_shared_dag(30),
        lambda: {"schema_version": REQUEST_SCHEMA_VERSION, "research_scope": [0] * 200000},
        lambda: _flat_mapping_of_visits(FROZEN_MAX_INPUT_NODE_VISITS + 1),
        lambda: _list_bearing_request_of_visits(FROZEN_MAX_INPUT_NODE_VISITS + 1),
    ],
)
def test_over_budget_input_returns_the_frozen_output_shape(builder):
    """R3.7: every over-budget shape returns normally in the frozen shape.

    ``_call_without_raising`` converts RecursionError, MemoryError or any other
    escaping exception into an explicit test failure, so reaching the assertions
    at all is itself the totality proof.
    """
    result = _call_without_raising(builder())
    assert_invalid(result, "TYPE_INVALID", "INPUT_ENVELOPE")
    assert result["research_program_id"] is None
    assert result["experiment_id"] is None
    assert result["counter_key"] is None
    assert len([e for e in result["checks"] if e["status"] == "FAIL"]) == 1


def test_over_budget_input_is_not_mutated_by_validation():
    """R3.8: refusal is read-only.  The rejected object is returned untouched."""
    request = _flat_mapping_of_visits(FROZEN_MAX_INPUT_NODE_VISITS + 1)
    request["trial_id"] = "T-0001"
    key_count_before = len(request)
    sample_key = "k000042"
    sample_value = request[sample_key]

    _call_without_raising(request)

    assert len(request) == key_count_before
    assert request[sample_key] == sample_value
    assert request["trial_id"] == "T-0001"


def test_shared_dag_input_is_not_mutated_by_validation():
    """R3.8 (DAG case): the sharing structure itself survives refusal."""
    request = _binary_shared_dag(30)
    root = request["research_scope"]

    _call_without_raising(request)

    assert sorted(request.keys()) == ["research_scope", "schema_version"]
    assert request["research_scope"] is root
    assert sorted(root.keys()) == ["a", "b"]
    assert root["a"] is root["b"]


def test_node_budget_does_not_disturb_the_approved_valid_identities():
    """R3.9: the frozen identities of a normal VALID request are unchanged.

    The budget only ever fires on rejection paths.  A real request costs a few
    dozen visits, three orders of magnitude inside the bound, so every approved
    identity and the contract_hash must be byte-identical to the values frozen
    before this hardening round.
    """
    result = validate_experiment_contract(base_request())

    assert result["contract_status"] == "VALID"
    assert result["research_program_id"] == (
        "c578301371c44cb8ffcb767cd66f00ce632c2606e19b5b01827003e26940d9d2"
    )
    assert result["counter_key"] == (
        "c578301371c44cb8ffcb767cd66f00ce632c2606e19b5b01827003e26940d9d2"
    )
    assert result["experiment_id"] == (
        "4c60a0e913201f7ef851f372182b41c155a9af9c3c56306715fe0c613408ca32"
    )
    assert result["contract_hash"] == (
        "523d59fb4e07d30a729665c5490ef563e52a50de03b03e80704867791858ffca"
    )
    assert result["dataset_identity"]["dataset_identity_hash"] == (
        "f685d7c06b364ad7fb831efec085533a12b2797e8217cde5a08a5834a332ec62"
    )
    assert result["boundaries"]["boundaries_hash"] == (
        "b72d43520023135c5565066a8ed169ea433d95c237bfb0c08cd928de76520ed9"
    )
    assert result["static_timing_policy"]["static_timing_policy_hash"] == (
        "ea75eb4322ed3529028da161424585a054360795d323cb777dcf18c1a0c5fff7"
    )


def test_a_realistic_request_is_far_inside_the_node_budget():
    """The bound is generous, not restrictive: a real request costs ~55 visits.

    Counted here with an independent iterative walk that applies the frozen
    counting rules directly, so the headroom claim does not depend on the
    module under test.
    """
    request = base_request()

    visits = 1
    stack = [request]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            visits += len(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            visits += len(node)
            stack.extend(node)

    assert visits < FROZEN_MAX_INPUT_NODE_VISITS // 100
    assert_valid(validate_experiment_contract(request))
