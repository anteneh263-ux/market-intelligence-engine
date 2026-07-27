"""metrics-v2 completed economic position-cycle ledger (WP2B-0A, hardened R1).

READ-ONLY reconstruction of completed economic position cycles from journaled ``order`` evidence.
Pure, deterministic, fail-closed, side-effect-free — no wall clock, no RNG, no I/O; inputs are never
mutated.

The ledger mirrors the frozen paper broker's fill accounting exactly (``app/paper/session/broker.py``
``_apply_fill`` / ``_round_step_toward_zero``, lines 214-250 / 25-30): replaying the ordered fills
reproduces the broker's cash, raw position, gross realized PnL and fees, and (in the production
constructor) is reconciled against every ``order`` boundary's ``position_after`` / ``cash_after`` /
``equity_after``. On top of that replay it groups non-zero fills into COMPLETED ECONOMIC CYCLES
delimited by EXECUTION-FLAT boundaries (canonical zero lots under the frozen ``qty_step``), with an
explicit DUST-CARRY account.

Conservation guarantees (Stage 2B R3/R4 + hardening R1):
  * Every NON-ZERO fill has a deterministic identity and belongs to EXACTLY ONE accounting bucket
    (a sealed cycle, the open cycle, or the dust-carry). ``validate_fill_partition`` proves the
    disjoint union equals every non-zero input fill; a duplicate identity fails closed.
  * Only ``filled_units == 0.0`` is a no-fill; a finite non-zero fill below ``_FILL_EPS`` still
    updates cash/position/fees and lands in a bucket (``_FILL_EPS`` is used ONLY for the broker's
    fee-per-unit guard, never to discard a real fill).
  * Dust-carry net PnL is transferred into the folding cycle EXACTLY ONCE; clearing the dust account
    never changes aggregate net PnL.
  * ``execution_flat`` (canonical zero lots) closes a behavioural cycle; ``accounting_flat`` (raw
    ``new_pos == 0``, basis cleared) is a separate notion — they differ when a sub-``qty_step``
    residual remains, which is carried explicitly.

Research/simulation only — no broker, no real orders, no private keys.
"""
from __future__ import annotations

import math
import numbers
from typing import Dict, List, Optional, Sequence, Tuple

_FILL_EPS = 1e-9                     # matches the frozen broker _EPS (fee-per-unit guard ONLY)
_CASH_TOL = 1e-6                     # frozen broker _reconcile tolerance (broker.py:256)
_PERMITTED_STATUS = ("filled", "partial", "rejected", "no_trade")
_ZERO_FILL_STATUS = ("rejected", "no_trade")
_NONZERO_FILL_STATUS = ("filled", "partial")


class LedgerError(Exception):
    """Raised when the ledger fails closed on malformed input or a reconciliation mismatch."""


# --------------------------------------------------------------------------- #
# numeric helpers (reject bool, require finite)                                #
# --------------------------------------------------------------------------- #
def _is_real(v) -> bool:
    return (not isinstance(v, bool)) and isinstance(v, numbers.Real)


def _finite(v) -> bool:
    return _is_real(v) and math.isfinite(float(v))


def validate_qty_step(qty_step) -> float:
    if not (_finite(qty_step) and float(qty_step) > 0):
        raise LedgerError("qty_step must be a finite positive number")
    return float(qty_step)


# --------------------------------------------------------------------------- #
# canonical lots — AUTHORITATIVE (identical to frozen execution rounding)      #
# --------------------------------------------------------------------------- #
def canonical_lots(position_units, qty_step) -> int:
    """Authoritative canonical lot count — identical to the frozen execution rounding
    ``broker._round_step_toward_zero`` (toward-zero floor to a ``qty_step`` multiple with the same
    ``+1e-12`` binary-FP guard), carrying sign. Decimal is used only as a test oracle, never as a
    second gate."""
    qs = validate_qty_step(qty_step)
    if not _finite(position_units):
        raise LedgerError("position_units must be a finite real number")
    p = float(position_units)
    n = math.floor(abs(p) / qs + 1e-12)
    if n == 0:
        return 0
    return int(math.copysign(n, p))


def is_execution_flat(position_units, qty_step) -> bool:
    return canonical_lots(position_units, qty_step) == 0


def position_tolerance(qty_step) -> float:
    """Asset-specific position reconciliation tolerance (R4 erratum 2): ``min(1e-9, qty_step*1e-3)``,
    guaranteed ``0 < tau < qty_step`` so a one-lot discrepancy can never hide."""
    qs = validate_qty_step(qty_step)
    tau = min(1e-9, qs * 1e-3)
    if not (tau > 0 and tau < qs):
        raise LedgerError("derived position tolerance must satisfy 0 < tau < qty_step")
    return tau


# --------------------------------------------------------------------------- #
# validation                                                                  #
# --------------------------------------------------------------------------- #
def _validate_fill(f) -> None:
    if not isinstance(f, dict):
        raise LedgerError("fill must be a dict")
    for k in ("filled_units", "fill_price", "fee", "exec_open_time"):
        if k not in f:
            raise LedgerError(f"fill missing required field {k!r}")
    if not _finite(f["filled_units"]):
        raise LedgerError("filled_units must be a finite real number")
    if not (_finite(f["fill_price"]) and float(f["fill_price"]) > 0):
        raise LedgerError("fill_price must be a finite positive number")
    if not (_finite(f["fee"]) and float(f["fee"]) >= 0):
        raise LedgerError("fee must be a finite non-negative number")
    if not (isinstance(f["exec_open_time"], int) and not isinstance(f["exec_open_time"], bool)):
        raise LedgerError("exec_open_time must be an int (market time)")
    if f["exec_open_time"] < 0:
        raise LedgerError("exec_open_time must be non-negative")
    if float(f["filled_units"]) == 0.0 and float(f["fee"]) != 0.0:
        raise LedgerError("an exact-zero fill may not carry a non-zero fee")


def _validate_order_payload(o) -> None:
    """Validate the ``order`` PAYLOAD fields (identity/sequence are validated at the wrapper level)."""
    if not isinstance(o, dict):
        raise LedgerError("order payload must be a dict")
    for k in ("filled_units", "fill_price", "fee", "exec_open_time",
              "position_after", "cash_after", "equity_after", "status"):
        if k not in o:
            raise LedgerError(f"order payload missing required field {k!r}")
    if not _finite(o["filled_units"]):
        raise LedgerError("filled_units must be finite")
    if not (_finite(o["fill_price"]) and float(o["fill_price"]) > 0):
        raise LedgerError("fill_price must be finite positive")
    if not (_finite(o["fee"]) and float(o["fee"]) >= 0):
        raise LedgerError("fee must be finite non-negative")
    ot = o["exec_open_time"]
    if not (isinstance(ot, int) and not isinstance(ot, bool) and ot >= 0):
        raise LedgerError("exec_open_time must be a non-negative int")
    for k in ("position_after", "cash_after", "equity_after"):
        if not _finite(o[k]):
            raise LedgerError(f"{k} must be finite")
    status = o["status"]
    if status not in _PERMITTED_STATUS:
        raise LedgerError(f"unpermitted order status {status!r}")
    nonzero = float(o["filled_units"]) != 0.0
    if status in _ZERO_FILL_STATUS and nonzero:
        raise LedgerError(f"status {status!r} must have zero filled_units")
    if status in _NONZERO_FILL_STATUS and not nonzero:
        raise LedgerError(f"status {status!r} must have non-zero filled_units")
    # an exact-zero fill may not carry a non-zero fee or execution cost
    if not nonzero:
        if float(o["fee"]) != 0.0:
            raise LedgerError("an exact-zero fill may not carry a non-zero fee")
        for ck in ("spread_cost", "slippage_cost"):
            if ck in o and _finite(o[ck]) and float(o[ck]) != 0.0:
                raise LedgerError(f"an exact-zero fill may not carry a non-zero {ck}")


def _validate_journal_event(ev, prev_seq: Optional[int], prev_ot: Optional[int],
                            seen_ids: set) -> Tuple[Dict, int, int]:
    """Validate a REAL journal event wrapper and return (payload, seq, exec_open_time).
    Enforces: event_type == 'order', non-empty string event_id (hashable, unique), non-negative int
    seq (bool invalid, strictly increasing), non-decreasing exec_open_time. Malformed/missing/
    unhashable identity fails closed with LedgerError (never a raw TypeError)."""
    if not isinstance(ev, dict):
        raise LedgerError("journal event must be a dict")
    if ev.get("event_type") != "order":
        raise LedgerError(f"expected event_type 'order', got {ev.get('event_type')!r}")
    eid = ev.get("event_id")
    if not (isinstance(eid, str) and eid):
        raise LedgerError("event_id must be a non-empty string")
    if eid in seen_ids:
        raise LedgerError(f"duplicate event_id {eid!r}")
    seq = ev.get("seq")
    if not (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0):
        raise LedgerError("seq must be a non-negative int (bool invalid)")
    if prev_seq is not None and seq <= prev_seq:
        raise LedgerError(f"non-increasing seq {seq} (prev {prev_seq})")
    payload = ev.get("payload")
    if not isinstance(payload, dict):
        raise LedgerError("event payload must be a dict")
    _validate_order_payload(payload)
    ot = int(payload["exec_open_time"])
    if prev_ot is not None and ot < prev_ot:
        raise LedgerError(f"decreasing exec_open_time {ot} (prev {prev_ot})")
    return payload, seq, ot


# --------------------------------------------------------------------------- #
# bucket scaffolding                                                          #
# --------------------------------------------------------------------------- #
def _empty_dust() -> Dict:
    # ``fees`` accrues the actual fee paid on dust-reduction fills (distinct from the residual's
    # ``alloc_entry_fees``); both are preserved and transferred on a fold.
    return {"raw_units": 0.0, "cost_basis": 0.0, "alloc_entry_fees": 0.0, "net": 0.0, "gross": 0.0,
            "fees": 0.0}


def _new_cycle(index: int, carry_in: Optional[Dict], entry_ot: int, entry_fp: float,
               entry_fee: float, opening_basis: float, opening_fee_basis: float) -> Dict:
    return {
        "index": index,
        "entry_exec_open_time": entry_ot,
        "exit_exec_open_time": None,
        "status": "open",
        "n_fills": 0,
        "realized_net": 0.0,
        "gross": 0.0,
        "realized_gross": 0.0,
        "entry_fill_price": entry_fp,
        "entry_fee": entry_fee,
        "opening_cost_basis_after_fill": opening_basis,
        "opening_fee_basis_after_fill": opening_fee_basis,
        "exit_fill_price": None,
        "residual_carried_in": carry_in,
        "residual_carried_out": None,
        # truthful: True whenever ANY dust evidence was transferred (units, basis, fees, net/gross
        # or fill identities) — not only when raw residual units are non-zero.
        "includes_inherited_dust": carry_in is not None,
        "fill_ids": [],
    }


def _reconcile_step(pos: float, cash: float, fp: float, after: Dict, qs: float) -> None:
    tau = position_tolerance(qs)
    if abs(pos - after["position_after"]) > tau:
        raise LedgerError(f"position reconcile mismatch: {pos} vs {after['position_after']}")
    if abs(cash - after["cash_after"]) > _CASH_TOL:
        raise LedgerError(f"cash reconcile mismatch: {cash} vs {after['cash_after']}")
    if abs((cash + pos * fp) - after["equity_after"]) > _CASH_TOL:
        raise LedgerError("equity-at-fill reconcile mismatch")


# --------------------------------------------------------------------------- #
# core stepping (shared by both entry points)                                 #
# --------------------------------------------------------------------------- #
def _build_core(steps: Sequence[Dict], *, initial_capital, qty_step, reconcile: bool) -> Dict:
    qs = validate_qty_step(qty_step)
    if not (_finite(initial_capital) and float(initial_capital) > 0):
        raise LedgerError("initial_capital must be a finite positive number")
    C0 = float(initial_capital)

    pos = 0.0
    avg_px = 0.0
    avg_fee = 0.0
    cash = C0
    realized = 0.0
    fees = 0.0
    sum_fill_net = 0.0

    cycles: List[Dict] = []
    active: Optional[Dict] = None
    dust = _empty_dust()
    dust_fill_ids: List = []
    all_fill_ids: List = []
    seen_ids: set = set()

    for st in steps:
        filled = float(st["filled_units"])
        fp = float(st["fill_price"])
        fee = float(st["fee"])
        ot = int(st["exec_open_time"])
        after = st.get("after")

        if filled == 0.0:
            # exact no-fill: no accounting bucket; still reconcile in production (state unchanged)
            if reconcile and after is not None:
                _reconcile_step(pos, cash, fp, after, qs)
            continue

        fid = st["fill_id"]
        if fid in seen_ids:
            raise LedgerError(f"duplicate fill identity {fid!r}")
        seen_ids.add(fid)
        all_fill_ids.append(fid)

        lots_before = canonical_lots(pos, qs)
        new_pos = pos + filled

        # ---- mirror frozen broker _apply_fill ------------------------------------------ #
        cash -= filled * fp
        cash -= fee
        fee_per_unit = fee / abs(filled) if abs(filled) > _FILL_EPS else 0.0
        fill_net = 0.0
        fill_gross = 0.0
        if pos != 0 and (pos > 0) != (filled > 0):
            closed_units = min(abs(filled), abs(pos))
            direction = 1.0 if pos > 0 else -1.0
            fill_gross = direction * closed_units * (fp - avg_px)
            alloc_entry_fee = avg_fee * closed_units
            alloc_exit_fee = fee_per_unit * closed_units
            fill_net = fill_gross - alloc_entry_fee - alloc_exit_fee
            realized += fill_gross
        if new_pos == 0:
            avg_px = 0.0
            avg_fee = 0.0
        elif pos == 0 or (pos > 0) == (new_pos > 0) and abs(new_pos) > abs(pos):
            added = abs(filled)
            avg_px = (avg_px * abs(pos) + fp * added) / abs(new_pos)
            avg_fee = (avg_fee * abs(pos) + fee) / abs(new_pos)
        elif (pos > 0) != (new_pos > 0):
            avg_px = fp
            avg_fee = fee_per_unit
        pos = new_pos
        fees += fee
        sum_fill_net += fill_net

        lots_after = canonical_lots(pos, qs)
        was_flat = (lots_before == 0)
        now_flat = (lots_after == 0)

        if was_flat and not now_flat:
            # OPEN — fold any dust carry (units + basis + fees + NET) explicitly, exactly once
            carry_in = None
            if (dust["raw_units"] != 0.0 or dust["net"] != 0.0 or dust["alloc_entry_fees"] != 0.0
                    or dust["gross"] != 0.0 or dust["fees"] != 0.0 or dust_fill_ids):
                carry_in = dict(dust)
                carry_in["transferred_fill_ids"] = list(dust_fill_ids)
            active = _new_cycle(len(cycles), carry_in, ot, fp, fee, avg_px, avg_fee)
            if carry_in is not None:
                active["realized_net"] += carry_in.get("net", 0.0)
                active["gross"] += carry_in.get("gross", 0.0)
            dust = _empty_dust()
            dust_fill_ids = []
            active["fill_ids"].append(fid)
            active["n_fills"] += 1
            active["realized_net"] += fill_net
            active["gross"] += fill_gross
        elif (not was_flat) and (not now_flat):
            if active is None:
                raise LedgerError("non-flat continuation without an active cycle (corrupt fill order)")
            active["fill_ids"].append(fid)
            active["n_fills"] += 1
            active["realized_net"] += fill_net
            active["gross"] += fill_gross
        elif (not was_flat) and now_flat:
            if active is None:
                raise LedgerError("close without an active cycle (corrupt fill order)")
            active["fill_ids"].append(fid)
            active["n_fills"] += 1
            active["realized_net"] += fill_net
            active["gross"] += fill_gross
            active["exit_exec_open_time"] = ot
            active["exit_fill_price"] = fp
            active["status"] = "completed"
            active["realized_gross"] = active["gross"]
            carry_out = None
            if pos != 0.0:  # sub-qty_step raw residual persists (execution-flat, not accounting-flat)
                carry_out = {"raw_units": pos, "cost_basis": avg_px,
                             "alloc_entry_fees": avg_fee * abs(pos), "net": 0.0, "gross": 0.0,
                             "fees": 0.0, "transferred_fill_ids": []}
                dust = {"raw_units": pos, "cost_basis": avg_px,
                        "alloc_entry_fees": avg_fee * abs(pos), "net": 0.0, "gross": 0.0, "fees": 0.0}
                dust_fill_ids = []
            active["residual_carried_out"] = carry_out
            cycles.append(active)
            active = None
        else:
            # was_flat and now_flat: a dust fill while execution-flat -> accrue to dust-carry
            dust["net"] += fill_net
            dust["gross"] += fill_gross
            dust["fees"] += fee
            dust["raw_units"] = pos
            dust["cost_basis"] = avg_px if pos != 0.0 else 0.0
            dust["alloc_entry_fees"] = avg_fee * abs(pos) if pos != 0.0 else 0.0
            dust_fill_ids.append(fid)

        if reconcile and after is not None:
            _reconcile_step(pos, cash, fp, after, qs)

    if active is not None:
        active["realized_gross"] = active["gross"]
    dust_out = dict(dust)
    dust_out["fill_ids"] = list(dust_fill_ids)

    return {
        "cycles": cycles,
        "open_cycle": active,
        "dust_carry": dust_out,
        "reconstructed": {"cash": cash, "position_units": pos, "realized_pnl": realized, "fees": fees},
        "sum_fill_net": sum_fill_net,
        "all_fill_ids": all_fill_ids,
        "qty_step": qs,
        "initial_capital": C0,
        "position_tolerance": position_tolerance(qs),
        "reconciled": bool(reconcile),
    }


# --------------------------------------------------------------------------- #
# public entry points                                                         #
# --------------------------------------------------------------------------- #
def build_cycle_ledger(fills: Sequence[Dict], *, initial_capital, qty_step) -> Dict:
    """Unit/fixture entry point: reconstruct cycles from minimal fill dicts (``filled_units``,
    ``fill_price``, ``fee``, ``exec_open_time``, optional ``fill_id``/``event_id``). Only exact-zero
    fills are skipped. No order-boundary reconciliation (use ``build_cycle_ledger_from_orders`` for
    verified production evidence)."""
    if not isinstance(fills, (list, tuple)):
        raise LedgerError("fills must be a list/tuple of fills")
    steps: List[Dict] = []
    for idx, f in enumerate(fills):
        _validate_fill(f)
        fid = f.get("fill_id")
        if fid is None:
            fid = f.get("event_id")
        if fid is None:
            fid = idx  # deterministic input index for isolated fixtures
        steps.append({"filled_units": float(f["filled_units"]), "fill_price": float(f["fill_price"]),
                      "fee": float(f["fee"]), "exec_open_time": int(f["exec_open_time"]), "fill_id": fid})
    return _build_core(steps, initial_capital=initial_capital, qty_step=qty_step, reconcile=False)


def build_cycle_ledger_from_orders(orders: Sequence[Dict], *, initial_capital, qty_step) -> Dict:
    """PRODUCTION entry point: consume REAL verified journal ``order`` EVENTS (wrappers carrying
    ``event_type`` == 'order', a non-empty string ``event_id``, a non-negative int ``seq`` and a
    ``payload``). Strictly validates event identity, strictly-increasing seq, non-decreasing
    ``exec_open_time``, payload field shapes, permitted status, zero-fill consistency and exact-zero
    cost, then reconciles the replayed position/cash/equity against every order boundary. Any
    malformed identity or mismatch raises ``LedgerError`` (never a raw ``TypeError``). No
    ``strict=False`` escape hatch."""
    if not isinstance(orders, (list, tuple)):
        raise LedgerError("orders must be a list/tuple of journal order events")
    steps: List[Dict] = []
    prev_seq: Optional[int] = None
    prev_ot: Optional[int] = None
    seen_ids: set = set()
    for ev in orders:
        payload, seq, ot = _validate_journal_event(ev, prev_seq, prev_ot, seen_ids)
        seen_ids.add(ev["event_id"])
        prev_seq = seq
        prev_ot = ot
        steps.append({
            "filled_units": float(payload["filled_units"]), "fill_price": float(payload["fill_price"]),
            "fee": float(payload["fee"]), "exec_open_time": ot, "fill_id": ev["event_id"],
            "after": {"position_after": float(payload["position_after"]),
                      "cash_after": float(payload["cash_after"]),
                      "equity_after": float(payload["equity_after"])},
        })
    return _build_core(steps, initial_capital=initial_capital, qty_step=qty_step, reconcile=True)


# --------------------------------------------------------------------------- #
# partition / reconciliation helpers                                          #
# --------------------------------------------------------------------------- #
def _cycle_owned_ids(c: Dict) -> set:
    ids = set(c.get("fill_ids", []))
    ci = c.get("residual_carried_in")
    if ci:
        ids |= set(ci.get("transferred_fill_ids", []))
    return ids


def validate_fill_partition(ledger: Dict) -> Tuple[bool, str]:
    """Prove the I1 exactly-once invariant: the disjoint union of every accounting bucket's fill
    identities equals every non-zero input fill. Returns (ok, detail)."""
    buckets: List[set] = [_cycle_owned_ids(c) for c in ledger["cycles"]]
    if ledger["open_cycle"] is not None:
        buckets.append(_cycle_owned_ids(ledger["open_cycle"]))
    buckets.append(set(ledger["dust_carry"].get("fill_ids", [])))
    union: set = set()
    for b in buckets:
        overlap = union & b
        if overlap:
            return False, f"fill identities in more than one bucket: {sorted(map(str, overlap))}"
        union |= b
    allids = set(ledger["all_fill_ids"])
    if union != allids:
        return False, f"partition != all fills (missing {allids - union}, extra {union - allids})"
    return True, ""


def active_and_dust_units(ledger: Dict) -> Tuple[float, float]:
    """Disjoint partition of the broker raw position: (active_cycle_raw_units, dust_carry_raw_units).
    Their sum equals ``reconstructed.position_units`` (no double counting)."""
    pos = ledger["reconstructed"]["position_units"]
    dust_u = ledger["dust_carry"]["raw_units"]
    return pos - dust_u, dust_u


def reconstructed_equity(ledger: Dict, mark: float) -> float:
    """Single marked equity = cash + raw_position * mark. The dust is a SUB-partition of the raw
    position, never added a second time."""
    if not (_finite(mark) and float(mark) > 0):
        raise LedgerError("mark must be a finite positive number")
    r = ledger["reconstructed"]
    return r["cash"] + r["position_units"] * float(mark)


def reconcile_against_snapshot(ledger: Dict, snapshot, *,
                               cash_tol: float = _CASH_TOL, pnl_tol: float = 1e-9) -> Tuple[Dict, bool]:
    """Reconcile the replayed state against an ordinary ``portfolio_snapshot`` (Variant A boundary).
    Fails closed (``LedgerError``) on malformed evidence: non-dict snapshot, missing fields, bool or
    non-finite numeric fields, or non-finite/negative tolerances. Position uses the qty_step-derived
    tolerance; cash/equity use the frozen broker tolerance; fee/PnL use a currency tolerance."""
    if not isinstance(snapshot, dict):
        raise LedgerError("snapshot must be a dict")
    for k in ("cash", "position_units", "realized_pnl", "fees_paid", "mark_price", "equity"):
        if k not in snapshot:
            raise LedgerError(f"snapshot missing required field {k!r}")
        if not _finite(snapshot[k]):
            raise LedgerError(f"snapshot {k!r} must be a finite number (bool rejected)")
    if float(snapshot["mark_price"]) <= 0:
        raise LedgerError("snapshot mark_price must be positive")
    for name, t in (("cash_tol", cash_tol), ("pnl_tol", pnl_tol)):
        if not (_finite(t) and float(t) >= 0):
            raise LedgerError(f"{name} must be a finite non-negative number")
    tau = ledger["position_tolerance"]
    r = ledger["reconstructed"]
    mark = float(snapshot["mark_price"])
    recon_equity = r["cash"] + r["position_units"] * mark
    checks = {
        "cash": abs(r["cash"] - float(snapshot["cash"])) <= cash_tol,
        "position": abs(r["position_units"] - float(snapshot["position_units"])) <= tau,
        "realized_pnl": abs(r["realized_pnl"] - float(snapshot["realized_pnl"])) <= pnl_tol,
        "fees": abs(r["fees"] - float(snapshot["fees_paid"])) <= pnl_tol,
        "equity": abs(recon_equity - float(snapshot["equity"])) <= cash_tol,
    }
    return checks, all(checks.values())
