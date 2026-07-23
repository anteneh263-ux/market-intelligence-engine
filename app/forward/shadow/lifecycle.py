"""Shadow-forward lifecycle: statuses + an explicit legal-transition table.

Statuses
--------
INVALID      methodological/technical failure (look-ahead, bad data, broken audit,
             simulator error, invalid result). TERMINAL — needs a NEW identity.
REJECT       technically valid but the hypothesis is weak / dominated / economically
             unsupported / outside acceptable risk.
SHADOW       technically valid and economically plausible, but evidence is insufficient
             or marginal; may collect NEW market observations with simulated orders only.
PROMOTE      strong enough research evidence to enter the existing locked forward
             VALIDATION lifecycle. Does NOT authorize live capital.
LIVE_READY   passed locked forward validation, behavioural-consistency, slippage stress
             and portfolio-risk checks. Eligible ONLY for a separate manual decision
             about a very small live pilot. Must NOT start trading automatically.
INVALIDATED  the frozen experiment was compromised (code / parameter / risk drift,
             manifest mismatch, broken history). TERMINAL — preserve all history.

INVALID vs INVALIDATED
----------------------
A methodological failure DISCOVERED AFTER admission (e.g. look-ahead or a broken audit
found only later) means the experiment was never methodologically valid: it moves to
INVALID. INVALIDATED is reserved for a *previously valid, frozen* experiment whose frozen
evidence was later compromised (drift or tampering). Both are terminal, and a changed
strategy must receive a NEW identity and a NEW preregistered experiment — no human action
may move INVALID or INVALIDATED into SHADOW / PROMOTE / LIVE_READY.

Research and simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

STATUSES: Set[str] = {"INVALID", "REJECT", "SHADOW", "PROMOTE", "LIVE_READY", "INVALIDATED"}

# Terminal statuses: never re-opened into an evidence-collecting or promotable state.
TERMINAL: Set[str] = {"INVALID", "INVALIDATED"}

# The complete set of LEGAL transitions (machine + system). `None` is the pre-status entry
# point used by admission to classify a fresh candidate. SHADOW/PROMOTE/LIVE_READY may
# fall to INVALID when a methodological flaw is discovered after admission, or to
# INVALIDATED when frozen evidence is compromised.
LEGAL_TRANSITIONS: Dict[Optional[str], Set[str]] = {
    None: {"INVALID", "REJECT", "SHADOW", "PROMOTE"},
    "SHADOW": {"PROMOTE", "REJECT", "INVALID", "INVALIDATED"},
    "PROMOTE": {"LIVE_READY", "REJECT", "INVALID", "INVALIDATED"},
    "LIVE_READY": {"INVALID", "INVALIDATED"},
    "REJECT": {"INVALIDATED"},
    "INVALID": set(),
    "INVALIDATED": set(),
}

# The subset a HUMAN may approve through the manual gate. Everything else is either a
# machine/admission classification or an automatic integrity/methodology event.
MANUAL_APPROVABLE: Set[tuple] = {
    ("SHADOW", "PROMOTE"),
    ("PROMOTE", "LIVE_READY"),
    ("SHADOW", "REJECT"),
    ("PROMOTE", "REJECT"),
}

# Which initial statuses are valid for a STARTED experiment in each mode.
MODE_INITIAL_STATUS: Dict[str, Set[str]] = {
    "shadow": {"SHADOW"},
    "validation": {"PROMOTE"},
}


class LifecycleError(ValueError):
    """Raised for an illegal or unsafe lifecycle transition."""


def is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL


def is_valid_status(status: Optional[str]) -> bool:
    return status in STATUSES


def mode_status_compatible(test_mode: str, status: str) -> bool:
    """A started experiment's mode must match its initial status: shadow<->SHADOW,
    validation<->PROMOTE."""
    return status in MODE_INITIAL_STATUS.get(test_mode, set())


def can_transition(frm: Optional[str], to: str) -> bool:
    if to not in STATUSES:
        return False
    return to in LEGAL_TRANSITIONS.get(frm, set())


def assert_transition(frm: Optional[str], to: str) -> None:
    """Raise LifecycleError if `frm -> to` is not legal. INVALID/INVALIDATED are terminal
    and can never be re-opened."""
    if to not in STATUSES:
        raise LifecycleError(f"unknown target status {to!r}; expected one of {sorted(STATUSES)}")
    if frm in TERMINAL:
        raise LifecycleError(
            f"{frm} is terminal: a compromised or invalid experiment cannot transition to "
            f"{to}. A changed strategy must receive a NEW identity and a NEW preregistered "
            f"experiment.")
    if not can_transition(frm, to):
        raise LifecycleError(
            f"illegal lifecycle transition {frm} -> {to}; legal targets from {frm} are "
            f"{sorted(LEGAL_TRANSITIONS.get(frm, set()))}")


def is_manually_approvable(frm: str, to: str) -> bool:
    """True iff a human may approve this exact transition through the manual gate."""
    return (frm, to) in MANUAL_APPROVABLE


def derive_current_status(initial_status: str, events: List[Dict]) -> str:
    """Derive the current lifecycle status from the event history, validating CONTINUITY.

    The current status is state — never stored in the frozen manifest — so legitimate
    transitions never rewrite it. IMPORTANT: the caller must verify the ledger hash chain
    (ShadowLedger.verify) BEFORE calling this, so derivation runs only over authenticated
    events (see ledger.verified_current_status).

    Event semantics:
      * A NON-transition event (observation, informational) must carry null old_status AND
        null new_status. Anything else is a discontinuous history.
      * A GENESIS event (old_status null, new_status set) establishes the initial status
        and must equal `initial_status`. AT MOST ONE genesis is allowed, and only as the
        FIRST status-bearing event — a later (null -> current status) event is NOT a second
        genesis and is rejected.
      * A TRANSITION event must have old_status == the current derived status, a recognised
        new_status, and a legal transition (assert_transition). INVALID/INVALIDATED are
        terminal and cannot reopen.

    Any illegal or discontinuous history raises LifecycleError.
    """
    if not is_valid_status(initial_status):
        raise LifecycleError(f"unknown initial status {initial_status!r}")
    status = initial_status
    seen_status_event = False
    for i, e in enumerate(events):
        old = e.get("old_status")
        new = e.get("new_status")
        if new is None:
            if old is not None:
                raise LifecycleError(
                    f"event {i}: a non-transition event must have null old_status and "
                    f"new_status (got old_status={old!r})")
            continue                                     # ordinary observation — no-op
        if new not in STATUSES:
            raise LifecycleError(f"event {i}: unknown new_status {new!r}")
        if old is None:
            # genesis: permitted only as the FIRST status-bearing event
            if seen_status_event:
                raise LifecycleError(
                    f"event {i}: a second genesis/status-establishment event is not allowed "
                    f"(genesis may appear once, as the first status-bearing event)")
            if new != status:
                raise LifecycleError(
                    f"event {i}: genesis new_status {new!r} != initial status {status!r}")
            seen_status_event = True
            continue
        if old != status:
            raise LifecycleError(
                f"event {i}: old_status {old!r} does not match the current status {status!r} "
                f"(discontinuous history)")
        assert_transition(status, new)                   # raises on an illegal transition
        status = new
        seen_status_event = True
    return status
