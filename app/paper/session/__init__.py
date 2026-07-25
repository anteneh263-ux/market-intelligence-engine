"""Phase 4 — Controlled Paper Trading (simulated money only).

This subpackage is additive: it reuses the approved Phase 1-3 components (strategy
interface, data providers, forward manifest helpers and the Shadow-Forward regime /
range / consistency modules) and adds a controlled, deterministic paper-trading
lifecycle around them. It NEVER touches Phase 1-3 code.

Hard invariants for the whole subpackage:
    paper_trading_only  = True
    live_execution_enabled = False

There is no broker credential, no live-order method and no real-money execution path
anywhere in this package. Live execution is structurally impossible: the flags are
module constants, not parameters, and no code path submits a real order.
"""
from __future__ import annotations

PAPER_TRADING_ONLY = True
LIVE_EXECUTION_ENABLED = False
PAPER_SESSION_VERSION = "paper-session-v1"


class SessionSafetyError(Exception):
    """Raised when the paper/live safety invariants are not satisfied (fail-closed)."""


def check_paper_invariants():
    """Explicit runtime validation of the safety invariants. Returns (ok, reason).

    This does NOT use ``assert`` (which Python strips under -O); it reads the current module
    constants each call, so a tampered invariant is caught even in optimized mode.
    """
    import app.paper.session as _self  # read live module globals (patchable in tests)
    if _self.PAPER_TRADING_ONLY is not True:
        return False, "PAPER_TRADING_ONLY is not True"
    if _self.LIVE_EXECUTION_ENABLED is not False:
        return False, "LIVE_EXECUTION_ENABLED is not False"
    return True, ""


def require_paper_invariants() -> None:
    """Raise SessionSafetyError unless the safety invariants hold. Used at broker construction
    and session start so an invalid invariant cannot start a session."""
    ok, reason = check_paper_invariants()
    if not ok:
        raise SessionSafetyError(f"refusing to run: {reason}")
