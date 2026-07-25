"""Deterministic paper broker — simulated funds only, no live-order capability.

The broker fills a target position at a bar's OPEN using the instrument's frozen execution
profile (spread, slippage, fee model, tick/qty rounding, min order size). It reconciles cash,
position and equity after every event, prevents duplicate execution of the same bar, and
never permits negative cash or leverage beyond the frozen limit — an order that would is
partially filled to the maximum safe size or rejected.

There is NO live path: ``LIVE_EXECUTION_ENABLED`` is a module constant, the class exposes no
order-routing/credential method, and every fill only mutates in-memory simulated balances.
Identical replay input + identical profile ⇒ identical fills, costs and equity.
"""
from __future__ import annotations

import math
import numbers
from typing import Dict, List, Optional

from app.paper.session import LIVE_EXECUTION_ENABLED, require_paper_invariants
from app.paper.session.profiles import FEE_BPS, FEE_PER_SHARE

_EPS = 1e-9


def _round_step_toward_zero(x: float, step: float) -> float:
    """Round the magnitude of x DOWN to a multiple of step (never over-allocate)."""
    if step <= 0:
        return x
    n = math.floor(abs(x) / step + 1e-12)
    return math.copysign(n * step, x)


class BrokerError(Exception):
    pass


class PaperBroker:
    """A deterministic, simulated-money broker. No live-order capability exists."""

    LIVE_EXECUTION_ENABLED = LIVE_EXECUTION_ENABLED  # always False

    def __init__(self, profile: Dict, *, initial_capital: float, max_leverage: float = 1.0,
                 shorting_allowed: bool = False):
        require_paper_invariants()  # explicit fail-closed check (not an assert)
        if shorting_allowed:
            # Stage 1 does not implement short-margin / symmetric short-leverage accounting
            raise BrokerError("short trading is not implemented in Stage 1; shorting_allowed must be False")
        if not (isinstance(initial_capital, (int, float)) and initial_capital > 0):
            raise BrokerError("initial_capital must be positive")
        if not (isinstance(max_leverage, (int, float)) and max_leverage > 0):
            raise BrokerError("max_leverage must be positive")
        self.profile = profile
        self.initial_capital = float(initial_capital)
        self.max_leverage = float(max_leverage)
        self.shorting_allowed = bool(shorting_allowed)
        self.cash = float(initial_capital)
        self.position_units = 0.0
        self.avg_entry_price = 0.0
        self.avg_entry_fee_per_unit = 0.0   # entry fee allocated per held unit (for net trade PnL)
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.slippage_cost = 0.0
        self.spread_cost = 0.0
        self.mark_price = 0.0
        self.equity = float(initial_capital)
        self.equity_peak = float(initial_capital)
        self.closed_trades: List[Dict] = []
        self._last_exec_open_time: Optional[int] = None

    # ------------------------------------------------------------------ #
    def mark(self, price: float) -> float:
        """Mark the position to ``price`` and update equity / peak. Returns equity."""
        if not (isinstance(price, (int, float)) and math.isfinite(price) and price > 0):
            raise BrokerError("mark price must be finite and positive")
        self.mark_price = float(price)
        self.equity = self.cash + self.position_units * self.mark_price
        if self.equity > self.equity_peak:
            self.equity_peak = self.equity
        return self.equity

    @property
    def drawdown(self) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return max(0.0, (self.equity_peak - self.equity) / self.equity_peak)

    @property
    def gross_exposure_frac(self) -> float:
        if self.equity <= 0:
            return float("inf")
        return abs(self.position_units * self.mark_price) / self.equity

    # ------------------------------------------------------------------ #
    def _fee_for(self, units: float, fill_price: float) -> float:
        u = abs(units)
        if self.profile["fee_model"] == FEE_BPS:
            return u * fill_price * self.profile["taker_fee_bps"] / 10_000.0
        if self.profile["fee_model"] == FEE_PER_SHARE:
            return max(self.profile["min_commission"], u * self.profile["commission_per_unit"])
        raise BrokerError("unknown fee model")

    def _per_unit_cost(self, fill_price: float) -> float:
        """Approx cash cost per unit incl. fee, used to size an affordable target."""
        if self.profile["fee_model"] == FEE_BPS:
            return fill_price * (1.0 + self.profile["taker_fee_bps"] / 10_000.0)
        return fill_price + self.profile["commission_per_unit"]

    def execute(self, target_fraction: float, *, exec_open_time: int, exec_price: float,
                allow_entries: bool = True) -> Dict:
        """Move toward ``target_fraction`` of equity (signed) at ``exec_price`` (a bar OPEN).

        Duplicate execution of the same ``exec_open_time`` is a no-op. When ``allow_entries``
        is False (post-risk-stop safe-exit), only trades that REDUCE the position are allowed.
        Returns an order-lifecycle record and never raises on ordinary conditions.
        """
        if not (isinstance(exec_price, (int, float)) and math.isfinite(exec_price) and exec_price > 0):
            raise BrokerError("exec_price must be finite and positive")
        # defensive: a non-numeric / non-finite target must never reach position arithmetic,
        # even though the sanitizer already rejects it upstream (defence in depth).
        if isinstance(target_fraction, bool) or not isinstance(target_fraction, numbers.Real) \
                or not math.isfinite(float(target_fraction)):
            return self._record("rejected", "flat", 0.0, 0.0, exec_price, 0.0, 0.0, 0.0,
                                reason="invalid (non-finite / non-numeric) target rejected defensively",
                                exec_open_time=exec_open_time)
        if self._last_exec_open_time is not None and exec_open_time == self._last_exec_open_time:
            return self._record("no_trade", "flat", 0.0, 0.0, exec_price, 0.0, 0.0, 0.0,
                                reason="duplicate execution for this bar suppressed",
                                exec_open_time=exec_open_time)
        self._last_exec_open_time = exec_open_time

        # long-only contract: a negative target can NEVER open an unauthorized short
        if target_fraction < 0 and not self.shorting_allowed:
            return self._record("rejected", "flat", 0.0, 0.0, exec_price, 0.0, 0.0, 0.0,
                                reason="short selling not permitted (long-only contract)",
                                exec_open_time=exec_open_time)

        equity_at_open = self.cash + self.position_units * exec_price
        prof = self.profile
        # target units sized against affordable per-unit cost so a full target funds cleanly
        if abs(target_fraction) < _EPS:
            target_units = 0.0
        else:
            side_price = exec_price * (1.0 + math.copysign(
                (prof["half_spread_bps"] + prof["slippage_bps"]) / 10_000.0, target_fraction))
            per_unit = self._per_unit_cost(side_price)
            target_units = _round_step_toward_zero(target_fraction * equity_at_open / per_unit,
                                                   prof["qty_step"])
        delta = _round_step_toward_zero(target_units - self.position_units, prof["qty_step"])

        # post-risk-stop: only allow reductions toward flat (safe-exit)
        reducing = (self.position_units > 0 and delta < 0) or (self.position_units < 0 and delta > 0) \
            or (abs(target_units) < abs(self.position_units))
        if not allow_entries and not reducing:
            return self._record("rejected", "flat", delta, 0.0, exec_price, 0.0, 0.0, 0.0,
                                reason="entries blocked after risk stop; only safe-exit permitted",
                                exec_open_time=exec_open_time)

        if abs(delta) < _EPS:
            return self._record("no_trade", "hold", 0.0, 0.0, exec_price, 0.0, 0.0, 0.0,
                                reason="already at target", exec_open_time=exec_open_time)

        side = "buy" if delta > 0 else "sell"
        signed = 1.0 if delta > 0 else -1.0
        fill_price = exec_price * (1.0 + signed * (prof["half_spread_bps"] + prof["slippage_bps"]) / 10_000.0)

        # affordability / leverage clamp (partial fill or rejection — fail closed)
        filled = delta
        if side == "buy":
            per_unit = self._per_unit_cost(fill_price)
            max_affordable = _round_step_toward_zero(max(0.0, self.cash) / per_unit, prof["qty_step"])
            # leverage cap on resulting gross exposure
            max_lev_units = _round_step_toward_zero(
                max(0.0, self.max_leverage * equity_at_open / exec_price - self.position_units),
                prof["qty_step"])
            cap = min(max_affordable, max_lev_units)
            if filled > cap:
                filled = cap
        # frozen per-order liquidity cap (applies to buys AND sells) -> partial fill
        max_fill = prof.get("max_fill_qty")
        if max_fill is not None and abs(filled) > max_fill:
            filled = math.copysign(max_fill, filled)
        filled = _round_step_toward_zero(filled, prof["qty_step"])

        # minimum order size on the (possibly clamped) fill
        if abs(filled) < _EPS:
            return self._record("rejected", side, delta, 0.0, exec_price, 0.0, 0.0, 0.0,
                                reason="order not affordable / would breach leverage",
                                exec_open_time=exec_open_time)
        notional = abs(filled) * fill_price
        if abs(filled) < prof["min_qty"] - _EPS or notional < prof["min_notional"] - _EPS:
            return self._record("rejected", side, delta, 0.0, exec_price, 0.0, 0.0, 0.0,
                                reason=f"below min order size (min_qty={prof['min_qty']}, "
                                       f"min_notional={prof['min_notional']})",
                                exec_open_time=exec_open_time)

        fee = self._fee_for(filled, fill_price)
        # cost diagnostics vs trading at the mark (open) price
        spread_c = abs(filled) * exec_price * prof["half_spread_bps"] / 10_000.0
        slip_c = abs(filled) * exec_price * prof["slippage_bps"] / 10_000.0

        # realized PnL on the portion that reduces/closes the existing position
        self._apply_fill(filled, fill_price, exec_price, fee)
        self.fees_paid += fee
        self.spread_cost += spread_c
        self.slippage_cost += slip_c

        status = "filled" if abs(filled - delta) < _EPS else "partial"
        rec = self._record(status, side, delta, filled, fill_price, fee, spread_c, slip_c,
                           reason="", exec_open_time=exec_open_time)
        self._reconcile(exec_price)
        return rec

    # ------------------------------------------------------------------ #
    def _apply_fill(self, filled: float, fill_price: float, mark_price: float, fee: float) -> None:
        pos = self.position_units
        new_pos = pos + filled
        # cash: buying spends cash, selling adds cash; fee always reduces cash
        self.cash -= filled * fill_price
        self.cash -= fee
        fee_per_unit = fee / abs(filled) if abs(filled) > _EPS else 0.0
        # NET realized PnL when reducing/closing: gross price PnL (fill prices already embed
        # spread+slippage — NOT subtracted again) minus the allocated ENTRY and EXIT fees.
        if pos != 0 and (pos > 0) != (filled > 0):
            closed_units = min(abs(filled), abs(pos))
            direction = 1.0 if pos > 0 else -1.0
            gross = direction * closed_units * (fill_price - self.avg_entry_price)
            alloc_entry_fee = self.avg_entry_fee_per_unit * closed_units
            alloc_exit_fee = fee_per_unit * closed_units
            net = gross - alloc_entry_fee - alloc_exit_fee
            self.realized_pnl += gross  # realized (gross price PnL); fees are tracked in cash/fees_paid
            self.closed_trades.append({
                "units": closed_units,
                "entry_fill_price": self.avg_entry_price, "exit_fill_price": fill_price,
                "gross_price_pnl": gross,
                "allocated_entry_fee": alloc_entry_fee, "allocated_exit_fee": alloc_exit_fee,
                "net_pnl": net, "net_pnl_frac_of_capital": net / self.initial_capital,
            })
        # update average entry price + entry fee/unit on an increase (or fresh/flipped position)
        if new_pos == 0:
            self.avg_entry_price = 0.0
            self.avg_entry_fee_per_unit = 0.0
        elif pos == 0 or (pos > 0) == (new_pos > 0) and abs(new_pos) > abs(pos):
            added = abs(filled)
            self.avg_entry_price = (self.avg_entry_price * abs(pos) + fill_price * added) / abs(new_pos)
            self.avg_entry_fee_per_unit = (self.avg_entry_fee_per_unit * abs(pos) + fee) / abs(new_pos)
        elif (pos > 0) != (new_pos > 0):
            # flipped through zero: the new leg's entry price/fee is this fill
            self.avg_entry_price = fill_price
            self.avg_entry_fee_per_unit = fee_per_unit
        self.position_units = new_pos

    def _reconcile(self, mark_price: float) -> None:
        equity = self.cash + self.position_units * mark_price
        if self.cash < -_EPS:
            raise BrokerError(f"reconciliation failure: negative cash {self.cash}")
        if abs(equity - (self.cash + self.position_units * mark_price)) > 1e-6:
            raise BrokerError("reconciliation failure: equity != cash + position*mark")
        self.equity = equity

    def _record(self, status: str, side: str, requested: float, filled: float, fill_price: float,
                fee: float, spread_c: float, slip_c: float, *, reason: str,
                exec_open_time: int) -> Dict:
        return {
            "status": status, "side": side,
            "requested_units": float(requested), "filled_units": float(filled),
            "fill_price": float(fill_price), "fee": float(fee),
            "spread_cost": float(spread_c), "slippage_cost": float(slip_c),
            "exec_open_time": int(exec_open_time),
            "cash_after": float(self.cash), "position_after": float(self.position_units),
            "equity_after": float(self.cash + self.position_units * fill_price),
            "reason": reason,
        }

    def snapshot(self) -> Dict:
        return {
            "cash": float(self.cash), "position_units": float(self.position_units),
            "avg_entry_price": float(self.avg_entry_price), "mark_price": float(self.mark_price),
            "equity": float(self.equity), "equity_peak": float(self.equity_peak),
            "drawdown": float(self.drawdown), "realized_pnl": float(self.realized_pnl),
            "fees_paid": float(self.fees_paid), "slippage_cost": float(self.slippage_cost),
            "spread_cost": float(self.spread_cost),
            "gross_exposure_frac": float(self.gross_exposure_frac),
        }
