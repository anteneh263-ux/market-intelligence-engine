"""Explicit paper-session lifecycle state machine.

States:
    CREATED -> VALIDATED -> RUNNING -> PAUSED
                                    -> DATA_STOPPED / RISK_STOPPED / INVALIDATED
                                    -> COMPLETED

Every transition is checked against a frozen allow-list; an invalid transition fails closed
(raises ``LifecycleError``) and never silently proceeds. ``RISK_STOPPED -> RUNNING`` is only
permitted with explicit human approval — the system never auto-restarts after a hard risk
stop, and it never weakens that rule on its own.
"""
from __future__ import annotations

from typing import Dict, Tuple

CREATED = "CREATED"
VALIDATED = "VALIDATED"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
DATA_STOPPED = "DATA_STOPPED"
RISK_STOPPED = "RISK_STOPPED"
SIGNAL_STOPPED = "SIGNAL_STOPPED"   # invalid strategy output; halts trading, needs human review
INVALIDATED = "INVALIDATED"
COMPLETED = "COMPLETED"

STATES = (CREATED, VALIDATED, RUNNING, PAUSED, DATA_STOPPED, RISK_STOPPED, SIGNAL_STOPPED,
          INVALIDATED, COMPLETED)
TERMINAL_STATES = (COMPLETED, INVALIDATED)

# states a human must explicitly approve before RUNNING may resume (never auto-restart)
_HUMAN_RESTART = (RISK_STOPPED, SIGNAL_STOPPED)

# allowed transitions (safety/integrity stops and INVALIDATED reachable from active states)
_ALLOWED: Dict[str, Tuple[str, ...]] = {
    CREATED: (VALIDATED, INVALIDATED),
    VALIDATED: (RUNNING, INVALIDATED),
    RUNNING: (PAUSED, DATA_STOPPED, RISK_STOPPED, SIGNAL_STOPPED, INVALIDATED, COMPLETED),
    PAUSED: (RUNNING, DATA_STOPPED, RISK_STOPPED, SIGNAL_STOPPED, INVALIDATED, COMPLETED),
    # data can resume after a data stop; risk/signal stops need human approval (guarded below)
    DATA_STOPPED: (RUNNING, PAUSED, RISK_STOPPED, SIGNAL_STOPPED, INVALIDATED, COMPLETED),
    RISK_STOPPED: (RUNNING, INVALIDATED),     # RUNNING only via human approval (see transition)
    SIGNAL_STOPPED: (RUNNING, INVALIDATED),   # RUNNING only via human approval (see transition)
    INVALIDATED: (),
    COMPLETED: (),
}


class LifecycleError(Exception):
    """Raised on an invalid or unauthorized lifecycle transition (fail-closed)."""


def can_transition(current: str, to: str, *, human_approval: bool = False) -> bool:
    if current not in _ALLOWED or to not in STATES:
        return False
    if to not in _ALLOWED[current]:
        return False
    if current in _HUMAN_RESTART and to == RUNNING and not human_approval:
        return False  # never auto-restart after a hard risk/signal stop
    return True


def transition(current: str, to: str, *, human_approval: bool = False) -> str:
    """Return the new state or raise LifecycleError. Fail-closed."""
    if current in TERMINAL_STATES:
        raise LifecycleError(f"{current} is terminal; no transition to {to} is allowed")
    if not can_transition(current, to, human_approval=human_approval):
        if current in _HUMAN_RESTART and to == RUNNING:
            raise LifecycleError(f"{current} -> RUNNING requires explicit human approval")
        raise LifecycleError(f"invalid transition {current} -> {to}")
    return to
