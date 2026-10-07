"""Pure ``metrics-v2`` behavioural metric functions (WP2B-0A, hardened R1).

Deterministic, read-only, fail-closed, bool-rejecting, finite-number-validated. No wall clock, no
RNG, no I/O; inputs are never mutated. Every metric is SEGMENT-LOCAL by construction — it is
computed from an explicitly-bound segment start (``E_start``) and the segment's own events, never
from a session-cumulative quantity (Stage 2B R2/R3/R4 contracts).

Each function returns ``{"status", "value", "reason"}`` with ``status`` in ``OK`` / ``NOT_AVAILABLE``
/ ``INVALID``:
  * OK            — a finite value was computed;
  * NOT_AVAILABLE — genuinely unavailable evidence (no completed cycles, E_start <= 0, empty path);
  * INVALID       — MALFORMED input (wrong datatype, non-finite where finite is required, unknown
                    cycle status, length mismatch, non-positive price, negative/bool tolerance, ...).

Cost treatment: equity/PnL fed in are already NET; these functions never re-subtract costs.
"""
from __future__ import annotations

import math
import numbers
from typing import Dict, Sequence

OK = "OK"
NOT_AVAILABLE = "NOT_AVAILABLE"
INVALID = "INVALID"

_WIN_EPS_DEFAULT = 1e-9              # documented absolute currency tolerance for win/loss near zero
_KNOWN_CYCLE_STATUS = ("completed", "open")


def _is_real(v) -> bool:
    return (not isinstance(v, bool)) and isinstance(v, numbers.Real)


def _finite(v) -> bool:
    return _is_real(v) and math.isfinite(float(v))


def _nonneg_int(v) -> bool:
    return isinstance(v, numbers.Integral) and not isinstance(v, bool) and int(v) >= 0


def _res(status: str, value=None, reason: str = "") -> Dict:
    return {"status": status, "value": value, "reason": reason}


def _validate_cycle_list(cycles):
    """Return (ok, reason). Every element must be a dict with a KNOWN status; a malformed record or
    an unknown status is a hard INVALID (never silently ignored)."""
    if not isinstance(cycles, (list, tuple)):
        return False, "cycles must be a list"
    for c in cycles:
        if not isinstance(c, dict):
            return False, "cycle record must be a dict"
        if c.get("status") not in _KNOWN_CYCLE_STATUS:
            return False, f"unknown cycle status {c.get('status')!r}"
    return True, ""


# --------------------------------------------------------------------------- #
# net_return                                                                   #
# --------------------------------------------------------------------------- #
def net_return(e_start, e_end) -> Dict:
    """(E_end - E_start) / E_start, using SEGMENT-LOCAL bound equity boundaries."""
    if not (_finite(e_start) and _finite(e_end)):
        return _res(INVALID, None, "E_start/E_end must be finite real numbers")
    if float(e_start) <= 0:
        return _res(NOT_AVAILABLE, None, "E_start <= 0; net_return undefined (never divide)")
    return _res(OK, (float(e_end) - float(e_start)) / float(e_start))


# --------------------------------------------------------------------------- #
# segment-local max drawdown                                                   #
# --------------------------------------------------------------------------- #
def segment_local_max_drawdown(equity_path: Sequence[float], e_start) -> Dict:
    """Max drawdown recomputed from the SEGMENT equity path with the peak initialized at
    ``E_start`` (never a session-cumulative peak)."""
    if not _finite(e_start) or float(e_start) <= 0:
        return _res(NOT_AVAILABLE, None, "invalid or non-positive E_start")
    if not isinstance(equity_path, (list, tuple)):
        return _res(INVALID, None, "equity_path must be a list/tuple")
    if len(equity_path) == 0:
        return _res(NOT_AVAILABLE, None, "empty segment equity path")
    peak = float(e_start)
    mdd = 0.0
    for e in equity_path:
        if not _finite(e):
            return _res(INVALID, None, "non-finite/bool equity in segment path")
        e = float(e)
        if e > peak:
            peak = e
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return _res(OK, mdd)


# --------------------------------------------------------------------------- #
# completed_trade_count (metric)                                               #
# --------------------------------------------------------------------------- #
def completed_trade_count_metric(cycles) -> Dict:
    """Count of completed economic cycles (== the checkpoint-axis count). Malformed/unknown-status
    records are INVALID, never silently skipped."""
    ok, why = _validate_cycle_list(cycles)
    if not ok:
        return _res(INVALID, None, why)
    n = sum(1 for c in cycles if c.get("status") == "completed")
    return _res(OK, int(n))


# --------------------------------------------------------------------------- #
# turnover                                                                     #
# --------------------------------------------------------------------------- #
def turnover(fills, e_start) -> Dict:
    """Sum of traded notional |filled_units * fill_price| normalized by SEGMENT-LOCAL E_start.
    Requires a positive fill_price; bool/non-finite/non-positive price is INVALID."""
    if not _finite(e_start) or float(e_start) <= 0:
        return _res(NOT_AVAILABLE, None, "invalid or non-positive E_start")
    if not isinstance(fills, (list, tuple)):
        return _res(INVALID, None, "fills must be a list")
    total = 0.0
    for f in fills:
        if not isinstance(f, dict):
            return _res(INVALID, None, "fill must be a dict")
        fu, fp = f.get("filled_units"), f.get("fill_price")
        if not _finite(fu):
            return _res(INVALID, None, "filled_units must be a finite real (bool rejected)")
        if not (_finite(fp) and float(fp) > 0):
            return _res(INVALID, None, "fill_price must be a finite positive number")
        total += abs(float(fu) * float(fp))
    return _res(OK, total / float(e_start))


# --------------------------------------------------------------------------- #
# exposure_time                                                                #
# --------------------------------------------------------------------------- #
def exposure_time(canonical_lot_flags, n_bars) -> Dict:
    """Fraction of segment bars with a non-flat canonical position. Requires
    ``len(canonical_lot_flags) == n_bars`` (a mismatch is INVALID), so the result can never exceed 1."""
    if not (isinstance(n_bars, int) and not isinstance(n_bars, bool) and n_bars > 0):
        return _res(NOT_AVAILABLE, None, "n_bars must be a positive int")
    if not isinstance(canonical_lot_flags, (list, tuple)):
        return _res(INVALID, None, "canonical_lot_flags must be a list of booleans")
    if len(canonical_lot_flags) != n_bars:
        return _res(INVALID, None, "len(canonical_lot_flags) must equal n_bars")
    exposed = 0
    for b in canonical_lot_flags:
        if not isinstance(b, bool):
            return _res(INVALID, None, "canonical_lot_flags must contain only booleans")
        if b:
            exposed += 1
    return _res(OK, exposed / float(n_bars))


# --------------------------------------------------------------------------- #
# win_rate                                                                     #
# --------------------------------------------------------------------------- #
def win_rate(cycles, *, eps_pnl: float = _WIN_EPS_DEFAULT) -> Dict:
    """Fraction of COMPLETED cycles with sealed ``realized_net`` above the scratch band. A cycle
    with ``|net| <= eps_pnl`` is a scratch (denominator, not a win). Open cycles excluded. No
    completed cycles -> NOT_AVAILABLE. Malformed/unknown-status cycles or a bad ``eps_pnl`` -> INVALID."""
    if not (_finite(eps_pnl) and float(eps_pnl) >= 0):
        return _res(INVALID, None, "eps_pnl must be a finite non-negative number (bool rejected)")
    ok, why = _validate_cycle_list(cycles)
    if not ok:
        return _res(INVALID, None, why)
    completed = [c for c in cycles if c.get("status") == "completed"]
    if not completed:
        return _res(NOT_AVAILABLE, None, "no completed cycles")
    wins = 0
    for c in completed:
        net = c.get("realized_net")
        if not _finite(net):
            return _res(INVALID, None, "cycle realized_net must be finite")
        if float(net) > float(eps_pnl):
            wins += 1
    return _res(OK, wins / float(len(completed)))


# --------------------------------------------------------------------------- #
# average_holding_period                                                       #
# --------------------------------------------------------------------------- #
def average_holding_period(cycles, bar_index_of: Dict) -> Dict:
    """Mean holding period in EXECUTABLE BARS: ``exit_execution_bar_index - entry_execution_bar_index``
    under the frozen market calendar (weekends/holidays are NOT bars). Open cycles excluded; no
    completed cycles -> NOT_AVAILABLE. For frozen Stage 1 a completed cycle spans >= 1 executable bar,
    so ``exit_index > entry_index`` is required; a violation, a missing/negative/bool index, or a
    malformed cycle is INVALID."""
    ok, why = _validate_cycle_list(cycles)
    if not ok:
        return _res(INVALID, None, why)
    if not isinstance(bar_index_of, dict):
        return _res(INVALID, None, "bar_index_of must be a dict")
    completed = [c for c in cycles if c.get("status") == "completed"]
    if not completed:
        return _res(NOT_AVAILABLE, None, "no completed cycles")
    total = 0
    for c in completed:
        entry_ot = c.get("entry_exec_open_time")
        exit_ot = c.get("exit_exec_open_time")
        if entry_ot is None or exit_ot is None:
            return _res(INVALID, None, "completed cycle missing entry/exit exec_open_time")
        if entry_ot not in bar_index_of or exit_ot not in bar_index_of:
            return _res(INVALID, None, "entry/exit exec_open_time missing from bar index map")
        ei, xi = bar_index_of[entry_ot], bar_index_of[exit_ot]
        if not (_nonneg_int(ei) and _nonneg_int(xi)):
            return _res(INVALID, None, "bar indices must be non-negative ints (bool rejected)")
        if int(xi) <= int(ei):
            return _res(INVALID, None, "exit index must be strictly greater than entry index (Stage 1)")
        total += int(xi) - int(ei)
    return _res(OK, total / float(len(completed)))
