"""Deterministic paper-session runner (Stage 1 core loop).

Drives one controlled paper session over a sequence of completed bars:

    check safety invariants + manifest drift + bind runtime inputs to the manifest -> VALIDATED
    per bar, in order:
        classify the gap (expected closure vs missing trading bars vs stale duration)
        validate the completed bar (malformed / out-of-order / duplicate -> DATA_STOPPED)
        execute the PENDING decision at THIS bar's OPEN   (never bar t's own H/L/C)
        mark to this bar's CLOSE, snapshot
        evaluate EVERY frozen absolute-risk limit on net equity after costs
        decide the target from bars <= t (causal; records signal timing)
    finalize -> COMPLETED / RISK_STOPPED / DATA_STOPPED

A decision made at bar t's close executes no earlier than bar t+1's open, so no order can use
bar t's high/low/close. The target for bar t is computed from ``frame.iloc[:t+1]`` only, so a
future bar cannot change a past decision. Every event timestamp is market time (never a wall
clock), so identical replay input yields identical events and hashes.

Recovery contract (Stage 1): recovery is DETERMINISTIC FULL REPLAY from the beginning using
the identical frozen inputs — NOT restoration from a saved broker checkpoint. Because
``run_replay`` first verifies the manifest and binds the runtime inputs (profile/risk/strategy/
params/dataset hashes), a run with any differing input fails closed (INVALIDATED) and produces
no events. Re-running with identical inputs reproduces cash, positions, equity and the final
event hash; re-running into the same journal directory is idempotent (event ids dedupe), so
recovery never duplicates events or trades.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import math

import pandas as pd

from app.forward.manifest import code_hash, param_hash
from app.forward.shadow.regime import (
    DEFAULT_REGIME_POLICY,
    LABEL_NA,
    align_labels_to_execution,
    classify_regimes,
)
from app.paper.session import (
    LIVE_EXECUTION_ENABLED,
    PAPER_TRADING_ONLY,
    check_paper_invariants,
)
from app.paper.session.broker import BrokerError, PaperBroker
from app.paper.session.ingest import (
    count_missing_trading_bars,
    dataset_hash as compute_dataset_hash,
    frame_to_bars,
    timeframe_ms,
    validate_bar,
)
from app.paper.session.journal import Journal
from app.paper.session.lifecycle import (
    COMPLETED,
    CREATED,
    DATA_STOPPED,
    INVALIDATED,
    RISK_STOPPED,
    RUNNING,
    SIGNAL_STOPPED,
    VALIDATED,
    transition,
)
from app.paper.session.manifest import detect_manifest_drift, expected_signal_policy
from app.paper.session.profiles import execution_profile_hash
from app.paper.session.risk import evaluate_risk, risk_limits_hash
from app.paper.session.sanitizer import CLAMPED, sanitize_target, signal_policy_hash

_CANON_COLS = ("open_time", "open", "high", "low", "close", "volume")


def derive_max_target_abs(manifest: Dict, risk_limits: Dict) -> float:
    """The maximum permitted |target|, derived from ALL frozen gross limits: the position-sizing
    max_gross, the absolute-risk max_position_gross and max_leverage. Each must be finite and
    positive; the tightest binds."""
    vals = {
        "position_sizing.max_gross": manifest["position_sizing_policy"].get("max_gross"),
        "risk.max_position_gross": risk_limits.get("max_position_gross"),
        "risk.max_leverage": risk_limits.get("max_leverage"),
    }
    for name, v in vals.items():
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(float(v)) and v > 0):
            raise ValueError(f"{name} must be a finite positive number for max_target_abs")
    return float(min(vals.values()))


def preflight_frame(frame, manifest: Dict) -> Tuple[bool, str, str]:
    """Guard EVERY malformed-frame path BEFORE any journal is created. Returns (ok, field,
    detail). All conversions (dataset hash, cadence, frame_to_bars) are wrapped so no
    KeyError / ValueError / OverflowError / type error escapes."""
    import math as _math
    try:
        if not isinstance(frame, pd.DataFrame):
            return False, "frame_type", "frame is not a pandas DataFrame"
        missing = [c for c in _CANON_COLS if c not in frame.columns]
        if missing:
            return False, "frame_columns", f"missing required columns {missing}"
        if len(frame) == 0:
            # a zero-bar dataset has no trading evidence -> never a successful COMPLETED
            return False, "empty_dataset", "empty dataset: no bars, no trading evidence"
        ot = pd.to_numeric(frame["open_time"], errors="coerce")
        if ot.isna().any() or not ot.map(lambda x: _math.isfinite(x)).all():
            return False, "open_time", "open_time contains NaN/inf/non-numeric values"
        if (ot.astype("float64") % 1 != 0).any():
            return False, "open_time", "open_time must be integer epoch-ms"
        for c in ("open", "high", "low", "close", "volume"):
            col = pd.to_numeric(frame[c], errors="coerce")
            if col.isna().any() or not col.map(lambda x: _math.isfinite(x)).all():
                return False, "ohlcv", f"{c} contains NaN/inf/non-numeric values"
        # exercise the conversion paths that would otherwise raise mid-run
        _ = compute_dataset_hash(frame)
        diffs = [b - a for a, b in zip(ot.astype("int64").tolist(), ot.astype("int64").tolist()[1:])]
        _ = frame_to_bars(frame)
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as exc:
        return False, "frame_conversion", f"{type(exc).__name__}: {exc}"
    return True, "", ""

# risk rules that describe a DATA/integrity problem (drive DATA_STOPPED); the rest drive RISK_STOPPED
_DATA_RULES = {"max_missing_bars", "stale_data_tolerance_ms", "max_consecutive_errors"}


class _StrategyError:
    """Sentinel returned when a strategy raises; the sanitizer rejects it (never coerced)."""
    __slots__ = ()


def _raw_target(frame: pd.DataFrame, t: int, strategy, params: Optional[Dict]):
    """RAW strategy target for bar t computed from bars <= t ONLY (look-ahead impossible). The
    value is NOT coerced here — the sanitizer classifies it. A strategy exception yields a
    sentinel that the sanitizer rejects."""
    try:
        sig = strategy.positions(frame.iloc[: t + 1], params)
        return sig.iloc[-1]
    except Exception:
        return _StrategyError()


def _aligned_regime_labels(frame: pd.DataFrame) -> List[str]:
    try:
        res = classify_regimes(frame[["open_time", "close", "high", "low"]], DEFAULT_REGIME_POLICY)
        labels = res.get("labels") or []
        if not labels:
            return [LABEL_NA] * len(frame)
        return align_labels_to_execution(labels)
    except Exception:
        return [LABEL_NA] * len(frame)


def _invalidated(manifest: Dict, field: str, detail: str) -> Dict:
    return {"lifecycle_status": INVALIDATED, "reason": f"runtime/manifest mismatch: {field}",
            "mismatched_field": field, "detail": detail,
            "manifest_hash": manifest.get("manifest_hash") if isinstance(manifest, dict) else None,
            "session_id": manifest.get("session_id") if isinstance(manifest, dict) else None,
            "live_execution_enabled": LIVE_EXECUTION_ENABLED}


def bind_runtime_to_manifest(manifest: Dict, *, profile: Dict, risk_limits: Dict, strategy,
                             params: Optional[Dict], frame: pd.DataFrame) -> Tuple[bool, str, str]:
    """Verify the ACTUAL runtime inputs match the frozen manifest. Returns (ok, field, detail).

    A correctly-hashed manifest is NOT accepted while running a different strategy, parameter
    set, execution profile, weaker risk policy, asset, timeframe or dataset.
    """
    if execution_profile_hash(profile) != manifest.get("execution_profile_hash"):
        return False, "execution_profile", "execution profile hash does not match the manifest"
    if profile.get("instrument") != manifest.get("asset"):
        return False, "asset", f"profile instrument {profile.get('instrument')!r} != manifest asset {manifest.get('asset')!r}"
    if risk_limits_hash(risk_limits) != manifest.get("absolute_risk_limits_hash"):
        return False, "absolute_risk_limits", "risk-limits hash does not match the manifest (weaker/altered policy)"
    if getattr(strategy, "strategy_id", None) != manifest["strategy"]["strategy_id"]:
        return False, "strategy_id", "strategy id does not match the manifest"
    if code_hash(strategy) != manifest["strategy"]["strategy_hash"]:
        return False, "strategy_hash", "strategy code hash does not match the manifest"
    if param_hash(params) != manifest.get("param_hash"):
        return False, "param_hash", "parameter hash does not match the manifest"
    tf_ms = timeframe_ms(manifest["timeframe"])
    ot = frame["open_time"].astype("int64").tolist()
    diffs = [b - a for a, b in zip(ot, ot[1:]) if b - a > 0]
    if diffs and min(diffs) != tf_ms:
        return False, "timeframe", f"dataset base cadence {min(diffs)}ms != timeframe {tf_ms}ms"
    declared_ds = manifest.get("dataset_hash")
    if declared_ds is not None and compute_dataset_hash(frame) != declared_ds:
        return False, "dataset_hash", "dataset content hash does not match the manifest"
    # the frozen SignalPolicy must reproduce from the runtime limits (catches a tampered
    # over_limit / max_target_abs / policy_version / shorting even if the manifest was re-hashed)
    if signal_policy_hash(expected_signal_policy(manifest, risk_limits)) != manifest.get("signal_policy_hash"):
        return False, "signal_policy", "frozen signal policy does not reproduce from the runtime limits"
    return True, "", ""


def run_replay(
    *,
    manifest: Dict,
    profile: Dict,
    risk_limits: Dict,
    strategy,
    params: Optional[Dict],
    frame: pd.DataFrame,
    journal_dir: str,
) -> Dict:
    """Run one deterministic paper session and return a structured result. Writes an append-only
    hash-chained journal to ``journal_dir/journal.jsonl``. Simulated funds only."""
    # ---- safety invariants (explicit; NOT an assert) -------------------------------- #
    ok_inv, why_inv = check_paper_invariants()
    if not ok_inv:
        return _invalidated(manifest, "paper_live_invariant", why_inv)

    state = CREATED
    drifted, why = detect_manifest_drift(manifest)
    if drifted:
        return _invalidated(manifest, "manifest_hash", why)

    # ---- fail closed on a malformed runtime frame BEFORE any journal ---------------- #
    okf, ffield, fdetail = preflight_frame(frame, manifest)
    if not okf:
        return _invalidated(manifest, ffield, fdetail)

    # ---- bind runtime inputs to the frozen manifest BEFORE any journal/event -------- #
    try:
        ok, field, detail = bind_runtime_to_manifest(
            manifest, profile=profile, risk_limits=risk_limits, strategy=strategy,
            params=params, frame=frame)
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        return _invalidated(manifest, "binding_error", f"{type(exc).__name__}: {exc}")
    if not ok:
        return _invalidated(manifest, field, detail)
    state = transition(state, VALIDATED)

    os.makedirs(journal_dir, exist_ok=True)
    journal_path = os.path.join(journal_dir, "journal.jsonl")
    journal = Journal(journal_path, manifest["manifest_hash"])
    tf_ms = timeframe_ms(manifest["timeframe"])
    start_ts = int(manifest["start_timestamp_ms"])
    journal.append("session_created", {
        "session_id": manifest["session_id"], "manifest_hash": manifest["manifest_hash"],
        "paper_trading_only": PAPER_TRADING_ONLY, "live_execution_enabled": LIVE_EXECUTION_ENABLED,
        "shorting_allowed": manifest.get("shorting_allowed", False),
        "signal_policy_hash": manifest["signal_policy_hash"],
        "regime_policy_hash": manifest["regime_policy_hash"],
        "consistency_policy_hash": manifest["consistency_policy_hash"],
        "reference_range_hash": manifest["reference_range_hash"],
    }, timestamp_ms=start_ts, event_id="session_created")

    # broker's INDEPENDENT defensive exposure cap = tightest of leverage / position-gross
    broker_cap = min(float(risk_limits["max_leverage"]), float(risk_limits["max_position_gross"]))
    broker = PaperBroker(profile, initial_capital=float(manifest["initial_capital"]),
                         max_leverage=broker_cap,
                         shorting_allowed=bool(manifest.get("shorting_allowed", False)))
    bars = frame_to_bars(frame)
    aligned_regimes = _aligned_regime_labels(frame)
    init_cap = float(manifest["initial_capital"])
    # use the SignalPolicy FROZEN in the manifest (not created dynamically here)
    signal_policy = manifest["signal_policy"]

    state = transition(state, RUNNING)
    counts = {"bars": 0, "signals": 0, "orders": 0, "fills": 0, "partials": 0,
              "rejects": 0, "risk_events": 0, "safe_exits": 0, "execution_errors": 0}
    absolute_risk = {"breached": False, "breaches": []}

    prev_ot: Optional[int] = None
    pending: Optional[float] = None
    risk_stopped = False
    safe_exit_pending = False
    consecutive_errors = 0
    n_closed_trades = 0
    cur_utc_day: Optional[int] = None
    day_start_equity = init_cap
    final_state = COMPLETED

    def _stop_with_breach(kind: str, breaches: List[Dict], bar: Dict, *, timestamp_ms: int) -> str:
        # data/integrity conditions are detected on ARRIVAL (bar open); risk is computed from the
        # COMPLETED bar close — the caller passes the correct detection/computation timestamp.
        journal.append("risk_event", {
            "open_time": bar["open_time"], "kind": kind, "absolute_risk": {"breached": True, "breaches": breaches},
            "action": ("block_new_entries; single_safe_exit; RISK_STOPPED" if kind == "risk"
                       else "record_evidence; DATA_STOPPED"),
        }, timestamp_ms=timestamp_ms, event_id=f"{kind}:{bar['open_time']}")
        counts["risk_events"] += 1
        return RISK_STOPPED if kind == "risk" else DATA_STOPPED

    for t, bar in enumerate(bars):
        # ---- gap analysis: expected closure vs missing trading bars vs stale --------- #
        # ``missing`` excludes weekends/closed sessions (expected closure => 0). Staleness is
        # measured in TRADING time (missing_bars * tf), so an expected weekend is never stale.
        missing = 0 if prev_ot is None else count_missing_trading_bars(
            prev_ot, bar["open_time"], timeframe=manifest["timeframe"], profile=profile)
        wall_gap_ms = 0 if prev_ot is None else int(bar["open_time"] - prev_ot)
        trading_gap_ms = missing * tf_ms
        data_breaches = evaluate_risk({"consecutive_missing_bars": missing, "data_gap_ms": trading_gap_ms},
                                      risk_limits)
        if data_breaches:
            journal.append("data_gap", {"open_time": bar["open_time"], "missing_trading_bars": missing,
                           "wall_gap_ms": wall_gap_ms, "trading_gap_ms": trading_gap_ms},
                           timestamp_ms=bar["open_time"])   # detected on arrival
            final_state = _stop_with_breach("data", data_breaches, bar, timestamp_ms=bar["open_time"])
            absolute_risk = {"breached": True, "breaches": data_breaches}
            break

        # ---- validate the completed bar (structural corruption fails closed) -------- #
        ok, reason = validate_bar(bar, prev_open_time=prev_ot)
        if not ok:
            journal.append("bar_rejected", {"open_time": bar.get("open_time"), "reason": reason},
                           timestamp_ms=int(bar.get("open_time") or start_ts))
            final_state = transition(state, DATA_STOPPED)
            break
        journal.append("bar_ingested", {k: bar[k] for k in
                       ("open_time", "open", "high", "low", "close", "volume")},
                       timestamp_ms=bar["open_time"], event_id=f"bar:{bar['open_time']}")
        counts["bars"] += 1

        # ---- UTC session-day boundary (explicit) ----------------------------------- #
        day = bar["open_time"] // 86_400_000
        if cur_utc_day is None or day != cur_utc_day:
            cur_utc_day = day
            day_start_equity = broker.equity

        # ---- execute the PENDING decision at THIS bar's OPEN ------------------------ #
        if pending is not None:
            try:
                order = broker.execute(pending, exec_open_time=bar["open_time"],
                                       exec_price=bar["open"], allow_entries=not risk_stopped)
                consecutive_errors = 0
            except BrokerError as exc:  # transient execution error
                consecutive_errors += 1
                journal.append("execution_error", {"open_time": bar["open_time"],
                               "error": type(exc).__name__, "consecutive_errors": consecutive_errors},
                               timestamp_ms=bar["open_time"], event_id=f"exerr:{bar['open_time']}")
                counts["execution_errors"] += 1
                err_breaches = evaluate_risk({"consecutive_errors": consecutive_errors}, risk_limits)
                if err_breaches:
                    final_state = _stop_with_breach("data", err_breaches, bar, timestamp_ms=bar["open_time"])
                    absolute_risk = {"breached": True, "breaches": err_breaches}
                    break
                prev_ot = bar["open_time"]
                continue
            journal.append("order", order, timestamp_ms=bar["open_time"], event_id=f"order:{bar['open_time']}")
            counts["orders"] += 1
            if order["status"] == "filled":
                counts["fills"] += 1
            elif order["status"] == "partial":
                counts["partials"] += 1
            elif order["status"] == "rejected":
                counts["rejects"] += 1
            if safe_exit_pending:  # the single post-risk-stop safe-exit just executed
                counts["safe_exits"] += 1
                # reconcile at the CURRENT legal execution price (this bar's open) so final equity
                # is correct whether the exit filled fully, partially, or was rejected.
                broker.mark(bar["open"])
                flat = abs(broker.position_units) < 1e-9
                journal.append("safe_exit", {
                    "open_time": bar["open_time"], "order_status": order["status"], "flat": flat,
                    "position_units": broker.position_units, "mark_price": bar["open"],
                    "equity": broker.equity,
                    "note": ("position flattened" if flat else
                             f"safe-exit {order['status']}; remaining position marked at execution price"),
                }, timestamp_ms=bar["open_time"], event_id=f"safeexit:{bar['open_time']}")
                pending = None  # exactly one safe-exit attempt; no repeated flat orders
                safe_exit_pending = False
                break  # stay in RISK_STOPPED, stop generating further events

        # ---- mark to CLOSE + snapshot ---------------------------------------------- #
        broker.mark(bar["close"])
        snap = broker.snapshot()
        journal.append("portfolio_snapshot", {"open_time": bar["open_time"], "close": bar["close"], **snap},
                       timestamp_ms=bar["open_time"] + tf_ms, event_id=f"snap:{bar['open_time']}")

        # ---- per-trade loss NET of all costs (closed this bar, no double counting) -- #
        last_trade_loss = None
        if len(broker.closed_trades) > n_closed_trades:
            n_closed_trades = len(broker.closed_trades)
            frac = broker.closed_trades[-1]["net_pnl_frac_of_capital"]
            if frac < 0:
                last_trade_loss = -frac

        # ---- evaluate EVERY frozen absolute-risk limit on net equity --------------- #
        risk_state = {
            "gross_exposure_frac": snap["gross_exposure_frac"],
            "leverage": snap["gross_exposure_frac"],
            "drawdown": snap["drawdown"],
            "session_loss_frac": max(0.0, (init_cap - snap["equity"]) / init_cap),
            "daily_loss_frac": max(0.0, (day_start_equity - snap["equity"]) / init_cap),
            "last_trade_loss_frac": last_trade_loss,
            "consecutive_missing_bars": 0, "data_gap_ms": 0, "consecutive_errors": 0,
        }
        breaches = evaluate_risk(risk_state, risk_limits)
        if breaches and not risk_stopped:
            absolute_risk = {"breached": True, "breaches": breaches}
            # risk is computed from the COMPLETED bar close -> stamp at bar-close time
            final_state = _stop_with_breach("risk", breaches, bar, timestamp_ms=bar["open_time"] + tf_ms)
            risk_stopped = True
            # arm exactly one safe-exit (flatten) at the NEXT bar's open; block new entries
            if abs(broker.position_units) > 1e-9 and t + 1 < len(bars):
                pending = 0.0
                safe_exit_pending = True
            else:
                journal.append("safe_exit", {"open_time": bar["open_time"], "order_status": "none",
                               "flat": abs(broker.position_units) < 1e-9,
                               "note": ("already flat" if abs(broker.position_units) < 1e-9
                                        else "no next bar to execute safe-exit")},
                               timestamp_ms=bar["open_time"] + tf_ms, event_id=f"safeexit:{bar['open_time']}")
                pending = None
                break
            prev_ot = bar["open_time"]
            continue

        # ---- decide target from bars <= t (causal), then SANITIZE ------------------ #
        raw_target = _raw_target(frame, t, strategy, params)
        if isinstance(raw_target, _StrategyError):
            # a strategy exception is an invalid output -> fail closed to SIGNAL_STOPPED
            pending_cancelled = pending is not None
            pending = None  # cancel any stale pending decision BEFORE stopping
            journal.append("signal_rejected", {
                "decision_bar": bar["open_time"], "status": "rejected",
                "reason_code": "strategy_exception", "original": "StrategyError",
                "normalized": None, "pending_cancelled": pending_cancelled,
                "action": "cancel_pending; block_new_orders; SIGNAL_STOPPED",
            }, timestamp_ms=bar["open_time"] + tf_ms, event_id=f"signal_rejected:{bar['open_time']}")
            final_state = transition(state, SIGNAL_STOPPED)
            break
        sig_event, normalized = sanitize_target(raw_target, policy=signal_policy,
                                                 decision_bar=bar["open_time"])
        if sig_event is not None and sig_event["status"] == CLAMPED:
            journal.append("signal_clamped", sig_event, timestamp_ms=bar["open_time"] + tf_ms,
                           event_id=f"signal_clamped:{bar['open_time']}")
        if normalized is None:
            # invalid strategy output (NaN/inf/type/short) -> fail closed to SIGNAL_STOPPED.
            # Infinity/NaN never reach broker arithmetic; any stale pending is cancelled.
            pending_cancelled = pending is not None
            pending = None
            sig_event = dict(sig_event or {}, pending_cancelled=pending_cancelled,
                             action="cancel_pending; block_new_orders; SIGNAL_STOPPED")
            journal.append("signal_rejected", sig_event, timestamp_ms=bar["open_time"] + tf_ms,
                           event_id=f"signal_rejected:{bar['open_time']}")
            final_state = transition(state, SIGNAL_STOPPED)
            break
        earliest_exec = bars[t + 1]["open_time"] if t + 1 < len(bars) else None
        journal.append("signal", {
            "decision_bar": bar["open_time"], "signal_time": bar["open_time"] + tf_ms,
            "earliest_execution_time": earliest_exec, "target_position": normalized,
            "aligned_regime_label": aligned_regimes[t] if t < len(aligned_regimes) else LABEL_NA,
        }, timestamp_ms=bar["open_time"] + tf_ms, event_id=f"signal:{bar['open_time']}")
        counts["signals"] += 1
        pending = normalized
        prev_ot = bar["open_time"]

    if final_state == COMPLETED and state == RUNNING:
        final_state = transition(state, COMPLETED)

    final_snap = broker.snapshot()
    final_equity = broker.cash + broker.position_units * broker.mark_price
    net_pnl = final_equity - init_cap           # single authoritative identity
    net_return = net_pnl / init_cap
    # finalize at the LAST legally-processed event time — never later than unprocessed market
    # data. On an early stop this is the stop/safe-exit/data-detection time; empty input uses
    # the frozen session start.
    processed = journal.events()
    final_ts = processed[-1]["timestamp"] if processed else start_ts
    journal.append("session_final", {
        "lifecycle_status": final_state, "manifest_hash": manifest["manifest_hash"],
        "absolute_risk": absolute_risk, "counts": counts, "broker": final_snap,
        "final_equity": final_equity, "initial_capital": init_cap,
        "net_pnl": net_pnl, "net_return": net_return,
    }, timestamp_ms=final_ts, event_id="session_final")

    return {
        "session_id": manifest["session_id"], "manifest_hash": manifest["manifest_hash"],
        "lifecycle_status": final_state, "journal_path": journal_path,
        "journal_summary": journal.summary(), "counts": counts, "absolute_risk": absolute_risk,
        "broker": final_snap, "final_equity": final_equity, "initial_capital": init_cap,
        "net_pnl": net_pnl, "net_return": net_return,
        "live_execution_enabled": LIVE_EXECUTION_ENABLED,
    }
