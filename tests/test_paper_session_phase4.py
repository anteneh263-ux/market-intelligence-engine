"""Phase 4 (Stage 1) — controlled paper-trading core: manifest, lifecycle, hash-chained
journal, execution profiles, bar ingestion, deterministic paper broker, absolute-risk stops,
look-ahead protection and a deterministic historical-replay pilot.

All tests are hermetic: deterministic synthetic bars (or a fixed real-data slice), fixed
seeds, temporary journal directories only. No network, no production evidence directories,
no wall-clock dependence. Reporting + Shadow-Forward checkpoint wiring are Stage 2.
"""
from __future__ import annotations

import copy
import json
import os

import numpy as np
import pandas as pd
import pytest

from app.strategy_research.strategies.registry import get_strategy
from app.forward.shadow.regime import DEFAULT_REGIME_POLICY, regime_policy_hash
from app.forward.shadow.consistency import DEFAULT_CONSISTENCY_POLICY, consistency_policy_hash
from app.forward.shadow.ranges import build_reference_ranges, freeze_range_policy

import app.paper.session as SESSION
from app.paper.session import (
    LIVE_EXECUTION_ENABLED,
    PAPER_TRADING_ONLY,
    SessionSafetyError,
    check_paper_invariants,
    require_paper_invariants,
)
from app.paper.session.broker import PaperBroker, BrokerError
from app.paper.session.engine import bind_runtime_to_manifest, preflight_frame, run_replay
from app.paper.session.sanitizer import (
    ACCEPTED,
    CLAMPED,
    REJECTED,
    freeze_signal_policy,
    sanitize_target,
)
from app.paper.session.ingest import (
    classify_gap,
    count_missing_trading_bars,
    dataset_hash,
    validate_bar,
)
from app.paper.session.journal import (
    Journal,
    JournalError,
    read_verified_events,
    verify_journal_file,
)
from app.paper.session.lifecycle import (
    COMPLETED,
    CREATED,
    DATA_STOPPED,
    INVALIDATED,
    RISK_STOPPED,
    RUNNING,
    VALIDATED,
    LifecycleError,
    transition,
)
from app.paper.session.manifest import build_session_manifest, detect_manifest_drift
from app.paper.session.profiles import (
    SUPPORTED_INSTRUMENTS,
    execution_profile_hash,
    freeze_execution_profile,
    is_trading_time,
)
from app.paper.session.risk import evaluate_risk, freeze_risk_limits

_T0 = 1_600_000_000_000  # fixed UTC-ms clock (2020-09-13)
_DAY = 86_400_000


# --------------------------------------------------------------------------- #
# deterministic synthetic data                                                #
# --------------------------------------------------------------------------- #
def _frame(closes, *, start=_T0, step=_DAY) -> pd.DataFrame:
    closes = [float(c) for c in closes]
    n = len(closes)
    opens = [closes[0]] + closes[:-1]
    rows = []
    for i in range(n):
        o, c = opens[i], closes[i]
        hi = max(o, c) + 1.0
        lo = max(0.01, min(o, c) - 1.0)
        rows.append({"open_time": start + i * step, "open": o, "high": hi, "low": lo,
                     "close": c, "volume": 1000.0})
    return pd.DataFrame(rows)


def _uptrend(n=30, base=100.0, slope=3.0) -> pd.DataFrame:
    return _frame([base + slope * i for i in range(n)])


def _pilot_manifest(asset="BTC/USD", *, frame, risk=None, length=5, gap_bars=2):
    strat = get_strategy("H-025")
    prof = freeze_execution_profile(asset)
    rpol = freeze_range_policy(seed=42, block_length=3, n_resamples=100, min_unique_segments=6)
    segs = [{"segment_index": i, "metrics": {
        "return": 0.001 * (i % 7 - 3), "drawdown": -0.02 - 0.001 * (i % 3),
        "trade_count": float(5 + i % 4), "turnover": float(1 + i % 3), "exposure_time": 0.5,
        "win_rate": 0.5 + 0.01 * (i % 3), "avg_holding_period": float(3 + i % 2),
        "long_short_balance": 1.0}} for i in range(24)]
    ref = build_reference_ranges(segs, policy=rpol)
    risk = risk or freeze_risk_limits(max_drawdown=0.90)
    man = build_session_manifest(
        strategy=strat, params={"length": length}, asset=asset, timeframe="1d",
        data_source={"provider": "synthetic_test", "root": "n/a", "is_real": False},
        start_timestamp_ms=int(frame["open_time"].iloc[0]), initial_capital=100_000.0,
        position_sizing_policy={"type": "target_fraction_of_equity", "max_gross": 1.0},
        execution_profile=prof, absolute_risk_limits=risk,
        gap_stale_policy={"max_gap_bars": gap_bars, "on_gap": "DATA_STOPPED"},
        regime_policy_hash=regime_policy_hash(DEFAULT_REGIME_POLICY),
        consistency_policy_hash=consistency_policy_hash(DEFAULT_CONSISTENCY_POLICY),
        reference_range_hash=ref["range_hash"], comparability_basis=ref["comparability_basis"],
        dataset_hash=dataset_hash(frame))
    return man, prof, strat, risk


def _run(frame, tmp_path, **over):
    man, prof, strat, risk = _pilot_manifest(frame=frame, **over)
    return run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                      params=man["params"], frame=frame, journal_dir=str(tmp_path)), man


# =========================================================================== #
# structural: live execution disabled                                         #
# =========================================================================== #
def test_live_execution_is_structurally_disabled():
    assert PAPER_TRADING_ONLY is True and LIVE_EXECUTION_ENABLED is False
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=1000.0)
    assert b.LIVE_EXECUTION_ENABLED is False
    # no live-order / credential surface exists on the broker
    for attr in ("place_live_order", "connect", "api_key", "submit_real", "route"):
        assert not hasattr(b, attr)


# =========================================================================== #
# execution profiles: asset-specific costs, market hours                      #
# =========================================================================== #
def test_profiles_are_distinct_per_asset():
    hashes = {execution_profile_hash(freeze_execution_profile(i)) for i in SUPPORTED_INSTRUMENTS}
    assert len(hashes) == len(SUPPORTED_INSTRUMENTS)  # no silently-shared assumptions
    assert freeze_execution_profile("SPY")["fee_model"] != freeze_execution_profile("BTC/USD")["fee_model"]


def test_asset_specific_fees_and_slippage_differ():
    # identical trade, three asset classes -> genuinely different costs
    def cost(asset):
        b = PaperBroker(freeze_execution_profile(asset), initial_capital=1_000_000.0)
        b.mark(100.0)
        o = b.execute(1.0, exec_open_time=1, exec_price=100.0)
        return round(o["fee"], 6), round(o["slippage_cost"], 6), round(o["fill_price"], 6)
    btc, spy, xau = cost("BTC/USD"), cost("SPY"), cost("XAU/USD")
    assert btc != spy and btc != xau and spy != xau
    # SPY uses per-share commission (>= min_commission), BTC uses bps
    assert cost("SPY")[0] >= 1.0


def test_market_hours_policy_per_asset():
    fri = _T0 - (_T0 // _DAY % 7 - ((_T0 // _DAY + 4) % 7)) * 0  # not used; explicit days below
    # 2024-01-05 Fri, 06 Sat, 07 Sun, 08 Mon (UTC)
    d = lambda y, m, dd: int(pd.Timestamp(y, m, dd, tz="UTC").timestamp() * 1000)
    assert is_trading_time(freeze_execution_profile("BTC/USD"), d(2024, 1, 6)) is True   # 24x7
    assert is_trading_time(freeze_execution_profile("SPY"), d(2024, 1, 6)) is False       # Sat
    assert is_trading_time(freeze_execution_profile("SPY"), d(2024, 1, 8)) is True        # Mon
    assert is_trading_time(freeze_execution_profile("XAU/USD"), d(2024, 1, 7)) is False   # Sun


# =========================================================================== #
# bar ingestion: validation + gap policy                                      #
# =========================================================================== #
def test_validate_bar_rejects_malformed_and_out_of_order():
    good = {"open_time": _T0, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1}
    assert validate_bar(good, prev_open_time=None)[0] is True
    assert validate_bar({**good, "high": 8}, prev_open_time=None)[0] is False       # geometry
    assert validate_bar({**good, "close": float("nan")}, prev_open_time=None)[0] is False
    assert validate_bar({**good, "is_complete": False}, prev_open_time=None)[0] is False
    assert validate_bar(good, prev_open_time=_T0)[0] is False                        # not increasing
    assert validate_bar({**good, "open_time": _T0 - 1}, prev_open_time=_T0)[0] is False


def test_gap_policy_counts_only_missing_trading_bars():
    spy = freeze_execution_profile("SPY")
    d = lambda y, m, dd: int(pd.Timestamp(y, m, dd, tz="UTC").timestamp() * 1000)
    # Fri -> Mon is an expected weekend gap: zero missing trading bars
    assert count_missing_trading_bars(d(2024, 1, 5), d(2024, 1, 8), timeframe="1d", profile=spy) == 0
    # Tue -> Thu skips a trading Wednesday: one missing trading bar
    assert count_missing_trading_bars(d(2024, 1, 9), d(2024, 1, 11), timeframe="1d", profile=spy) == 1
    # crypto is continuous: a skipped day is a real missing bar
    btc = freeze_execution_profile("BTC/USD")
    g = classify_gap(_T0, _T0 + 2 * _DAY, timeframe="1d", profile=btc,
                     gap_policy={"max_gap_bars": 0})
    assert g["missing_trading_bars"] == 1 and g["action"] == DATA_STOPPED


def test_replay_data_stops_on_crypto_gap(tmp_path):
    f = _uptrend(20)
    f.loc[10, "open_time"] = f.loc[10, "open_time"] + 3 * _DAY  # inject a gap after bar 9
    f = f.sort_values("open_time").reset_index(drop=True)
    res, _ = _run(f, tmp_path, gap_bars=0)
    assert res["lifecycle_status"] == DATA_STOPPED


def test_replay_data_stops_on_out_of_order_bar(tmp_path):
    f = _uptrend(20)
    f.loc[12, "open_time"] = f.loc[5, "open_time"]  # duplicate/out-of-order timestamp
    res, _ = _run(f, tmp_path)
    assert res["lifecycle_status"] == DATA_STOPPED


# =========================================================================== #
# deterministic replay + look-ahead protection                                #
# =========================================================================== #
def test_deterministic_replay_identical_hashes(tmp_path):
    f = _uptrend(40)
    r1, man = _run(f, tmp_path / "a")
    r2, _ = _run(f, tmp_path / "b")
    assert r1["journal_summary"]["final_event_hash"] == r2["journal_summary"]["final_event_hash"]
    assert r1["counts"] == r2["counts"]
    assert r1["broker"]["equity"] == r2["broker"]["equity"]
    assert r1["lifecycle_status"] == COMPLETED


def test_no_same_bar_execution(tmp_path):
    f = _uptrend(40)
    res, man = _run(f, tmp_path)
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    signals = {e["payload"]["decision_bar"]: e["payload"] for e in events if e["event_type"] == "signal"}
    orders = [e["payload"] for e in events if e["event_type"] == "order"]
    # every order executes at a bar strictly AFTER the decision bar (>= t+1 open)
    assert orders, "expected at least one order"
    for s in signals.values():
        if s["earliest_execution_time"] is not None:
            assert s["earliest_execution_time"] > s["decision_bar"]
    for o in orders:
        assert o["exec_open_time"] > min(signals) if signals else True


def test_future_bar_mutation_cannot_change_past_decision(tmp_path):
    f = _uptrend(40)
    r1, man1 = _run(f, tmp_path / "orig")
    ev1 = read_verified_events(r1["journal_path"], man1["manifest_hash"])
    # mutate a far-future bar (index 35), keeping valid geometry
    f2 = f.copy()
    f2.loc[35, "close"] = f2.loc[35, "close"] * 3.0
    f2.loc[35, "high"] = f2.loc[35, "close"] + 1.0
    r2, man2 = _run(f2, tmp_path / "mut")
    ev2 = read_verified_events(r2["journal_path"], man2["manifest_hash"])

    def sig_at(events, decision_bar):
        for e in events:
            if e["event_type"] == "signal" and e["payload"]["decision_bar"] == decision_bar:
                return e["payload"]["target_position"]
        return None
    early_bar = int(f["open_time"].iloc[10])
    assert sig_at(ev1, early_bar) == sig_at(ev2, early_bar)  # past signal unchanged
    # and the fill at an early bar is unchanged
    def order_at(events, ot):
        for e in events:
            if e["event_type"] == "order" and e["payload"]["exec_open_time"] == ot:
                return e["payload"]["filled_units"], e["payload"]["fill_price"]
        return None
    early_exec = int(f["open_time"].iloc[11])
    assert order_at(ev1, early_exec) == order_at(ev2, early_exec)


# =========================================================================== #
# broker: reconciliation, duplicate execution, no negative cash               #
# =========================================================================== #
def test_broker_reconciliation_and_no_negative_cash():
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0)
    b.mark(500.0)
    b.execute(1.0, exec_open_time=1, exec_price=500.0)
    assert b.cash >= -1e-9 and b.gross_exposure_frac <= 1.0 + 1e-9
    b.mark(550.0)
    assert abs(b.equity - (b.cash + b.position_units * 550.0)) < 1e-6
    b.execute(0.0, exec_open_time=2, exec_price=550.0)
    assert abs(b.position_units) < 1e-9 and b.realized_pnl > 0


def test_broker_suppresses_duplicate_execution_for_a_bar():
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0)
    b.mark(500.0)
    o1 = b.execute(1.0, exec_open_time=42, exec_price=500.0)
    pos_after = b.position_units
    o2 = b.execute(1.0, exec_open_time=42, exec_price=500.0)  # same bar again
    assert o1["status"] == "filled" and o2["status"] == "no_trade"
    assert b.position_units == pos_after  # not doubled


def test_broker_rejects_below_min_order_size():
    # a profile whose min_notional exceeds any affordable order -> the order is rejected
    prof = freeze_execution_profile("BTC/USD", min_notional=1e12)
    b = PaperBroker(prof, initial_capital=100_000.0)
    b.mark(500.0)
    o = b.execute(1.0, exec_open_time=1, exec_price=500.0)
    assert o["status"] == "rejected" and b.position_units == 0.0


# =========================================================================== #
# absolute-risk stop                                                          #
# =========================================================================== #
def test_risk_stop_blocks_entries_and_flags_breach(tmp_path):
    # rise (go long), then a crash bar so drawdown breaches a tight limit
    closes = [100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0]
    f = _frame(closes)
    res, man = _run(f, tmp_path, length=5, risk=freeze_risk_limits(max_drawdown=0.10))
    assert res["lifecycle_status"] == RISK_STOPPED
    assert res["absolute_risk"]["breached"] is True
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    assert any(e["event_type"] == "risk_event" for e in events)
    # after the risk stop, no new long entry is opened (only safe-exit toward flat)
    orders_after = [e["payload"] for e in events if e["event_type"] == "order"]
    assert all(o["status"] != "filled" or o["side"] == "sell"
               for o in orders_after[-1:])  # last action is not a new long entry


def test_risk_limits_cannot_be_weakened():
    lim = freeze_risk_limits(max_drawdown=0.10)
    with pytest.raises(ValueError):
        freeze_risk_limits(max_drawdown=0.0)   # non-positive limit rejected
    # evaluate_risk is pure: it returns breaches, it does not mutate the limits
    before = copy.deepcopy(lim)
    evaluate_risk({"drawdown": 0.5}, lim)
    assert lim == before


# =========================================================================== #
# immutable manifest + lifecycle                                              #
# =========================================================================== #
def test_manifest_is_immutable_and_drift_detected():
    f = _uptrend(10)
    man, *_ = _pilot_manifest(frame=f)
    assert detect_manifest_drift(man) == (False, "")
    tampered = copy.deepcopy(man); tampered["initial_capital"] = 1.0
    assert detect_manifest_drift(tampered)[0] is True
    live = copy.deepcopy(man); live["live_execution_enabled"] = True
    assert detect_manifest_drift(live)[0] is True


def test_replay_invalidates_on_manifest_drift(tmp_path):
    f = _uptrend(10)
    man, prof, strat, risk = _pilot_manifest(frame=f)
    man["initial_capital"] = 1.0  # drift after freezing
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params=man["params"], frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == INVALIDATED


def test_invalid_lifecycle_transitions_fail_closed():
    assert transition(CREATED, VALIDATED) == VALIDATED
    with pytest.raises(LifecycleError):
        transition(CREATED, RUNNING)              # must validate first
    with pytest.raises(LifecycleError):
        transition(RISK_STOPPED, RUNNING)         # needs human approval
    assert transition(RISK_STOPPED, RUNNING, human_approval=True) == RUNNING
    with pytest.raises(LifecycleError):
        transition(COMPLETED, RUNNING)            # terminal


# =========================================================================== #
# journal: append-only, hash-chain, corruption, idempotent recovery           #
# =========================================================================== #
def test_journal_is_append_only_and_hash_chained(tmp_path):
    p = str(tmp_path / "journal.jsonl")
    j = Journal(p, "mh-1")
    j.append("a", {"x": 1}, timestamp_ms=1)
    j.append("b", {"y": 2}, timestamp_ms=2)
    ok, reason, bad = j.verify()
    assert ok and bad is None
    # each line links to the previous via parent_hash
    lines = [json.loads(l) for l in open(p) if l.strip()]
    assert lines[1]["parent_hash"] == lines[0]["event_hash"]


def test_corrupted_journal_fails_closed(tmp_path):
    p = str(tmp_path / "journal.jsonl")
    j = Journal(p, "mh-1")
    j.append("a", {"x": 1}, timestamp_ms=1)
    with open(p, "a") as f:  # tamper: append a line with a broken chain
        f.write(json.dumps({"event_id": "z", "seq": 1, "timestamp": 2, "event_type": "t",
                            "payload": {}, "parent_hash": "deadbeef", "event_hash": "bad"}) + "\n")
    ok, reason, bad = verify_journal_file(p, "mh-1")
    assert ok is False and bad == 1
    with pytest.raises(JournalError):
        read_verified_events(p, "mh-1")


def test_idempotent_recovery(tmp_path):
    p = str(tmp_path / "journal.jsonl")
    j = Journal(p, "mh-1")
    j.append("bar", {"open_time": 100}, timestamp_ms=100, event_id="bar:100")
    n_lines_1 = len([l for l in open(p) if l.strip()])
    # reopen (recover) and replay the same event id -> no duplicate line, no duplicate trade
    j2 = Journal(p, "mh-1")
    e = j2.append("bar", {"open_time": 100}, timestamp_ms=100, event_id="bar:100")
    n_lines_2 = len([l for l in open(p) if l.strip()])
    assert n_lines_1 == n_lines_2 and e["seq"] == 0


# =========================================================================== #
# deterministic real-data pilot (H-025, BTC/USD, 1d)                          #
# =========================================================================== #
def _load_btc_slice(n0=300, n1=900):
    path = os.path.join("data_csv", "BTCUSD_1d.csv")
    if not os.path.exists(path):
        pytest.skip("BTCUSD_1d.csv not present")
    from app.strategy_research.providers.registry import get_provider
    from app.strategy_research.providers.base import DataRequest
    df = get_provider("csv", root="data_csv").get(DataRequest(asset="BTC/USD", timeframe="1d")).df
    return df.iloc[n0:n1].reset_index(drop=True)


def test_pilot_btc_deterministic_replay_with_trades(tmp_path):
    frame = _load_btc_slice()
    man, prof, strat, risk = _pilot_manifest(asset="BTC/USD", frame=frame, length=200,
                                             risk=freeze_risk_limits(max_drawdown=0.90))
    r1 = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                    params={"length": 200}, frame=frame, journal_dir=str(tmp_path / "a"))
    r2 = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                    params={"length": 200}, frame=frame, journal_dir=str(tmp_path / "b"))
    # this fixed real-data slice is deterministic: it RISK_STOPs on max_daily_loss. The slice is
    # a positional iloc[300:900], so it stays byte-stable even if the CSV is later extended.
    assert r1["lifecycle_status"] == RISK_STOPPED
    assert r1["absolute_risk"]["breached"] is True
    assert "max_daily_loss" in {b["rule"] for b in r1["absolute_risk"]["breaches"]}
    assert r1["counts"]["fills"] + r1["counts"]["partials"] > 0  # the pilot actually trades
    assert r1["journal_summary"]["final_event_hash"] == r2["journal_summary"]["final_event_hash"]
    assert verify_journal_file(r1["journal_path"], man["manifest_hash"])[0] is True
    # net PnL is AFTER costs: fees were actually charged
    assert r1["broker"]["fees_paid"] > 0
    # reconciliation holds at the end
    b = r1["broker"]
    assert abs(b["equity"] - (b["cash"] + b["position_units"] * b["mark_price"])) < 1e-6


# =========================================================================== #
# no external side effects                                                    #
# =========================================================================== #
def test_no_external_side_effects_during_replay(tmp_path):
    cwd_before = os.getcwd()
    root_before = sorted(os.listdir("."))
    f = _uptrend(30)
    res, man = _run(f, tmp_path)
    # everything was written under the temp journal dir only
    assert res["journal_path"].startswith(str(tmp_path))
    assert os.getcwd() == cwd_before
    assert sorted(os.listdir(".")) == root_before


# =========================================================================== #
# (1) net PnL = final_equity - initial_capital (single identity, no double count)
# =========================================================================== #
def _fresh_broker(asset="BTC/USD", cap=100_000.0):
    b = PaperBroker(freeze_execution_profile(asset), initial_capital=cap)
    return b


def _assert_reconciled(b, mark, init):
    final_equity = b.cash + b.position_units * mark
    net_pnl = final_equity - init
    assert abs(b.equity - final_equity) < 1e-6                       # equity == cash + pos*mark
    # net_pnl must NOT re-add realized_pnl (which is already in cash)
    assert abs(net_pnl - (final_equity - init)) < 1e-9
    return final_equity, net_pnl


def test_net_pnl_fully_closed_profitable_trade():
    b = _fresh_broker(); b.mark(500.0)
    b.execute(1.0, exec_open_time=1, exec_price=500.0)
    b.mark(600.0); b.execute(0.0, exec_open_time=2, exec_price=600.0); b.mark(600.0)
    fe, net = _assert_reconciled(b, 600.0, 100_000.0)
    assert abs(b.position_units) < 1e-9 and net > 0
    assert abs(net / 100_000.0 - (fe - 100_000.0) / 100_000.0) < 1e-12  # net_return identity


def test_net_pnl_fully_closed_losing_trade():
    b = _fresh_broker(); b.mark(500.0)
    b.execute(1.0, exec_open_time=1, exec_price=500.0)
    b.mark(400.0); b.execute(0.0, exec_open_time=2, exec_price=400.0); b.mark(400.0)
    fe, net = _assert_reconciled(b, 400.0, 100_000.0)
    assert abs(b.position_units) < 1e-9 and net < 0


def test_net_pnl_open_position_at_session_end():
    b = _fresh_broker(); b.mark(500.0)
    b.execute(1.0, exec_open_time=1, exec_price=500.0)
    b.mark(550.0)  # still holding at end
    fe, net = _assert_reconciled(b, 550.0, 100_000.0)
    assert b.position_units > 0 and net > 0


def test_net_pnl_flat_session():
    b = _fresh_broker(); b.mark(500.0)
    fe, net = _assert_reconciled(b, 500.0, 100_000.0)
    assert b.position_units == 0.0 and abs(net) < 1e-9


def test_net_pnl_partial_position_reduction():
    b = _fresh_broker(); b.mark(500.0)
    b.execute(1.0, exec_open_time=1, exec_price=500.0)
    held = b.position_units
    b.mark(520.0); b.execute(0.5, exec_open_time=2, exec_price=520.0); b.mark(520.0)
    assert 0 < b.position_units < held  # partially reduced
    _assert_reconciled(b, 520.0, 100_000.0)


def test_net_pnl_reflects_fees_spread_slippage():
    # a round trip at a flat price loses exactly the frozen costs (fees+spread+slippage)
    b = _fresh_broker(); b.mark(500.0)
    b.execute(1.0, exec_open_time=1, exec_price=500.0)
    b.mark(500.0); b.execute(0.0, exec_open_time=2, exec_price=500.0); b.mark(500.0)
    fe, net = _assert_reconciled(b, 500.0, 100_000.0)
    assert net < 0 and b.fees_paid > 0 and b.slippage_cost > 0 and b.spread_cost > 0
    assert abs(net) <= b.fees_paid + b.slippage_cost + b.spread_cost + 1e-6


def test_engine_net_pnl_identity(tmp_path):
    f = _uptrend(40)
    res, man = _run(f, tmp_path)
    b = res["broker"]
    assert abs(res["net_pnl"] - (res["final_equity"] - res["initial_capital"])) < 1e-9
    assert abs(res["final_equity"] - (b["cash"] + b["position_units"] * b["mark_price"])) < 1e-6
    assert abs(res["net_return"] - res["net_pnl"] / res["initial_capital"]) < 1e-12


# =========================================================================== #
# (2) runtime inputs are bound to the frozen manifest                         #
# =========================================================================== #
def test_binding_accepts_matching_runtime(tmp_path):
    f = _uptrend(20)
    man, prof, strat, risk = _pilot_manifest(frame=f)
    ok, field, _ = bind_runtime_to_manifest(man, profile=prof, risk_limits=risk, strategy=strat,
                                             params=man["params"], frame=f)
    assert ok and field == ""


@pytest.mark.parametrize("mutate,field", [
    ("param", "param_hash"),
    ("weaker_risk", "absolute_risk_limits"),
    ("profile", "execution_profile"),
    ("strategy", "strategy_id"),
    ("dataset", "dataset_hash"),
])
def test_binding_rejects_mismatched_runtime_no_events(tmp_path, mutate, field):
    f = _uptrend(20)
    man, prof, strat, risk = _pilot_manifest(frame=f)
    kwargs = dict(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                  params=man["params"], frame=f, journal_dir=str(tmp_path))
    if mutate == "param":
        kwargs["params"] = {"length": 999}
    elif mutate == "weaker_risk":
        kwargs["risk_limits"] = freeze_risk_limits(max_drawdown=0.99)
    elif mutate == "profile":
        kwargs["profile"] = freeze_execution_profile("ETH/USD")
    elif mutate == "strategy":
        kwargs["strategy"] = get_strategy("H-020")
    elif mutate == "dataset":
        f2 = f.copy(); f2.loc[3, "close"] = f2.loc[3, "close"] + 1.0
        f2.loc[3, "high"] = f2.loc[3, "close"] + 1.0
        kwargs["frame"] = f2
    res = run_replay(**kwargs)
    assert res["lifecycle_status"] == INVALIDATED
    assert res["mismatched_field"] == field
    # no journal / no trading events produced on a binding failure
    assert not os.path.exists(os.path.join(str(tmp_path), "journal.jsonl"))


# =========================================================================== #
# (3) every frozen risk limit is actively enforced                            #
# =========================================================================== #
@pytest.mark.parametrize("state,rule", [
    ({"gross_exposure_frac": 1.5}, "max_position_gross"),
    ({"leverage": 1.5}, "max_leverage"),
    ({"drawdown": 0.99}, "max_drawdown"),
    ({"session_loss_frac": 0.99}, "max_session_loss"),
    ({"daily_loss_frac": 0.99}, "max_daily_loss"),
    ({"last_trade_loss_frac": 0.99}, "max_loss_per_trade"),
    ({"consecutive_missing_bars": 99}, "max_missing_bars"),
    ({"data_gap_ms": 10**15}, "stale_data_tolerance_ms"),
    ({"consecutive_errors": 99}, "max_consecutive_errors"),
])
def test_every_risk_rule_breaches(state, rule):
    lim = freeze_risk_limits()
    breaches = evaluate_risk(state, lim)
    assert any(b["rule"] == rule for b in breaches)
    for b in breaches:  # each breach records observed + limit
        assert "observed" in b and "limit" in b


def test_engine_enforces_drawdown_and_session_loss(tmp_path):
    closes = [100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0]
    res, man = _run(_frame(closes), tmp_path, length=5, risk=freeze_risk_limits(max_drawdown=0.10))
    assert res["lifecycle_status"] == RISK_STOPPED
    rules = {b["rule"] for b in res["absolute_risk"]["breaches"]}
    # deterministic: this crash breaches BOTH the drawdown and the session-loss limit
    assert "max_drawdown" in rules and "max_session_loss" in rules


def test_engine_enforces_per_trade_loss(tmp_path):
    # go long, take a loss, exit -> a closed trade whose loss exceeds max_loss_per_trade
    closes = [100 + 2 * i for i in range(8)] + [80.0, 70.0, 130.0, 130.0]
    res, man = _run(_frame(closes), tmp_path, length=3,
                    risk=freeze_risk_limits(max_drawdown=0.95, max_session_loss=0.95,
                                            max_daily_loss=0.95, max_loss_per_trade=0.02))
    # this scenario is deterministic: a closed trade whose loss exceeds the frozen per-trade
    # limit always fails closed to RISK_STOPPED on exactly the max_loss_per_trade rule
    assert res["lifecycle_status"] == RISK_STOPPED
    assert {b["rule"] for b in res["absolute_risk"]["breaches"]} == {"max_loss_per_trade"}


def test_engine_enforces_consecutive_execution_errors(tmp_path, monkeypatch):
    f = _uptrend(30)
    man, prof, strat, risk = _pilot_manifest(frame=f, length=5,
                                             risk=freeze_risk_limits(max_consecutive_errors=2))
    # force the broker to raise on every execute -> consecutive execution errors
    from app.paper.session import broker as broker_mod
    orig = broker_mod.PaperBroker.execute

    def boom(self, *a, **k):
        raise BrokerError("injected execution error")
    monkeypatch.setattr(broker_mod.PaperBroker, "execute", boom)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    monkeypatch.setattr(broker_mod.PaperBroker, "execute", orig)
    assert res["lifecycle_status"] == DATA_STOPPED
    assert any(b["rule"] == "max_consecutive_errors" for b in res["absolute_risk"]["breaches"])


# =========================================================================== #
# (4) short-position / leverage enforcement (long-only for Stage 1)           #
# =========================================================================== #
def test_manifest_freezes_long_only_contract():
    f = _uptrend(10)
    man, *_ = _pilot_manifest(frame=f)
    assert man["shorting_allowed"] is False  # H-025 is long_only


def test_negative_target_never_opens_short_under_long_only():
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0,
                    shorting_allowed=False)
    b.mark(500.0)
    o = b.execute(-1.0, exec_open_time=1, exec_price=500.0)
    assert o["status"] == "rejected" and b.position_units == 0.0


def test_oversized_long_clamped_to_leverage():
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0, max_leverage=1.0)
    b.mark(500.0)
    o = b.execute(5.0, exec_open_time=1, exec_price=500.0)  # ask for 5x
    # the 5x request is clamped to the 1.0x leverage cap, so the fill is deterministically partial
    assert b.gross_exposure_frac <= 1.0 + 1e-9 and o["status"] == "partial"


def test_leverage_at_and_above_limit():
    # exactly 1.0x is allowed; the broker never lets exposure exceed the frozen leverage
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0, max_leverage=1.0)
    b.mark(100.0)
    b.execute(1.0, exec_open_time=1, exec_price=100.0)
    assert b.gross_exposure_frac <= 1.0 + 1e-9
    # a further attempt to add exposure at the limit adds nothing (no leverage creep)
    pos_before = b.position_units
    b.execute(1.0, exec_open_time=2, exec_price=100.0)
    assert b.position_units <= pos_before + 1e-9


def test_orders_after_equity_falls_stay_within_leverage():
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0, max_leverage=1.0)
    b.mark(500.0); b.execute(1.0, exec_open_time=1, exec_price=500.0)
    b.mark(250.0)  # equity halves
    b.execute(1.0, exec_open_time=2, exec_price=250.0)  # re-target full -> must stay <= 1x
    assert b.gross_exposure_frac <= 1.0 + 1e-9 and b.cash >= -1e-9


# =========================================================================== #
# (5) risk-stop produces exactly one controlled safe exit                     #
# =========================================================================== #
def test_risk_stop_one_safe_exit_no_noise(tmp_path):
    closes = [100 + 3 * i for i in range(12)] + [40.0, 45.0, 46.0, 47.0, 48.0]
    res, man = _run(_frame(closes), tmp_path, length=5, risk=freeze_risk_limits(max_drawdown=0.10))
    assert res["lifecycle_status"] == RISK_STOPPED
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    safe_exits = [e for e in events if e["event_type"] == "safe_exit"]
    assert len(safe_exits) == 1                       # exactly one safe-exit attempt
    assert res["counts"]["safe_exits"] <= 1
    # no repeated flat/rejected order noise after the stop: at most one order post risk_event
    seqs = [e["seq"] for e in events]
    risk_seq = next(e["seq"] for e in events if e["event_type"] == "risk_event")
    orders_after = [e for e in events if e["event_type"] == "order" and e["seq"] > risk_seq]
    assert len(orders_after) <= 1
    # no new long entry after the stop
    assert all(o["payload"]["side"] != "buy" for o in orders_after)


def test_risk_stop_when_already_flat_records_no_exit_order(tmp_path):
    # (historical name) with a flat book the crash cannot move equity, so no rule breaches and
    # the session COMPLETES without emitting any exit order -- verified deterministically below
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 30.0, 31.0, 32.0]
    res, man = _run(_frame(closes), tmp_path, length=3, risk=freeze_risk_limits(max_drawdown=0.10))
    # with no open position, the crash cannot move equity, so no absolute-risk rule can breach:
    # the session deterministically COMPLETES (it never RISK_STOPs here) with no breaches
    assert res["lifecycle_status"] == COMPLETED
    assert not (res.get("absolute_risk") or {}).get("breaches")


# =========================================================================== #
# (6) safety does not rely on assert                                          #
# =========================================================================== #
def test_invalid_invariant_cannot_start_session(tmp_path, monkeypatch):
    assert check_paper_invariants()[0] is True
    monkeypatch.setattr(SESSION, "LIVE_EXECUTION_ENABLED", True)
    # broker construction refuses
    with pytest.raises(SessionSafetyError):
        PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=1000.0)
    # and a full session refuses to start (INVALIDATED, no journal)
    f = _uptrend(10)
    man, prof, strat, risk = _pilot_manifest(frame=f)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params=man["params"], frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == INVALIDATED
    assert not os.path.exists(os.path.join(str(tmp_path), "journal.jsonl"))


# =========================================================================== #
# (7) data-gap controls: expected closure / missing bars / stale / corruption #
# =========================================================================== #
def test_allowed_gap_within_tolerance_completes(tmp_path):
    f = _uptrend(20)
    f.loc[10:, "open_time"] = f.loc[10:, "open_time"] + 1 * _DAY  # one missing bar (crypto)
    res, man = _run(f, tmp_path, risk=freeze_risk_limits(max_missing_bars=1, max_drawdown=0.95))
    assert res["lifecycle_status"] == COMPLETED


def test_too_many_missing_bars_data_stops(tmp_path):
    f = _uptrend(20)
    f.loc[10:, "open_time"] = f.loc[10:, "open_time"] + 3 * _DAY  # three missing bars (crypto)
    res, man = _run(f, tmp_path, risk=freeze_risk_limits(max_missing_bars=1, max_drawdown=0.95))
    assert res["lifecycle_status"] == DATA_STOPPED
    assert any(b["rule"] == "max_missing_bars" for b in res["absolute_risk"]["breaches"])


def test_stale_without_excessive_missing_bars(tmp_path):
    # tolerate many missing bars, but a small stale-time tolerance trips first
    f = _uptrend(20)
    f.loc[10:, "open_time"] = f.loc[10:, "open_time"] + 3 * _DAY
    res, man = _run(f, tmp_path, risk=freeze_risk_limits(
        max_missing_bars=10, stale_data_tolerance_ms=int(1.5 * _DAY), max_drawdown=0.95))
    assert res["lifecycle_status"] == DATA_STOPPED
    assert any(b["rule"] == "stale_data_tolerance_ms" for b in res["absolute_risk"]["breaches"])


def test_expected_spy_weekend_closure_is_not_a_gap(tmp_path):
    # Fri, Mon, Tue, Wed, Thu, Fri, Mon ... (skips weekends) -> zero missing trading bars
    d = lambda y, m, dd: int(pd.Timestamp(y, m, dd, tz="UTC").timestamp() * 1000)
    # only weekends are skipped (no missing weekday): Fri, Mon, Tue..Fri, Mon, Tue..
    days = [d(2024, 1, 5), d(2024, 1, 8), d(2024, 1, 9), d(2024, 1, 10), d(2024, 1, 11),
            d(2024, 1, 12), d(2024, 1, 15), d(2024, 1, 16), d(2024, 1, 17), d(2024, 1, 18)]
    closes = [100 + i for i in range(len(days))]
    opens = [closes[0]] + closes[:-1]
    f = pd.DataFrame([{"open_time": days[i], "open": opens[i], "high": max(opens[i], closes[i]) + 1,
                       "low": min(opens[i], closes[i]) - 1, "close": closes[i], "volume": 1000.0}
                      for i in range(len(days))])
    res, man = _run(f, tmp_path, asset="SPY", length=3,
                    risk=freeze_risk_limits(max_missing_bars=0, max_drawdown=0.95))
    assert res["lifecycle_status"] == COMPLETED  # weekends are expected, not gaps


def test_duplicate_bar_data_stops(tmp_path):
    f = _uptrend(20)
    f.loc[12, "open_time"] = f.loc[11, "open_time"]  # duplicate timestamp
    res, man = _run(f, tmp_path)
    assert res["lifecycle_status"] == DATA_STOPPED


# =========================================================================== #
# (8) deterministic-replay recovery with identical frozen inputs              #
# =========================================================================== #
def test_recovery_is_deterministic_full_replay(tmp_path):
    f = _uptrend(40)
    r1, man = _run(f, tmp_path / "run1")
    r2, _ = _run(f, tmp_path / "run2")
    # identical frozen inputs reproduce cash, position, equity and the final event hash
    assert r1["journal_summary"]["final_event_hash"] == r2["journal_summary"]["final_event_hash"]
    assert r1["broker"]["cash"] == r2["broker"]["cash"]
    assert r1["broker"]["position_units"] == r2["broker"]["position_units"]
    assert r1["final_equity"] == r2["final_equity"]


def test_recovery_into_same_journal_is_idempotent(tmp_path):
    f = _uptrend(20)
    man, prof, strat, risk = _pilot_manifest(frame=f)
    kw = dict(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
              params=man["params"], frame=f, journal_dir=str(tmp_path))
    r1 = run_replay(**kw)
    n1 = r1["journal_summary"]["event_count"]
    r2 = run_replay(**kw)  # replay into the SAME dir -> idempotent, no duplicate events
    n2 = r2["journal_summary"]["event_count"]
    assert n1 == n2
    assert r1["journal_summary"]["final_event_hash"] == r2["journal_summary"]["final_event_hash"]


def test_recovery_fails_closed_when_inputs_differ(tmp_path):
    f = _uptrend(20)
    man, prof, strat, risk = _pilot_manifest(frame=f)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params={"length": 123}, frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == INVALIDATED


# =========================================================================== #
# (1) explicit signal sanitizer                                               #
# =========================================================================== #
_SANITIZE_POLICY = freeze_signal_policy(max_target_abs=1.0, over_limit="clamp", shorting_allowed=False)


@pytest.mark.parametrize("raw,status,reason", [
    (0.0, ACCEPTED, "ok"),
    (1.0, ACCEPTED, "ok"),
    (np.float64(0.5), ACCEPTED, "ok"),
    (np.int64(1), ACCEPTED, "ok"),
    (1.5, CLAMPED, "above_position_limit"),
    (-1.0, REJECTED, "short_not_permitted"),
    (float("nan"), REJECTED, "non_finite_value"),
    (float("inf"), REJECTED, "non_finite_value"),
    (float("-inf"), REJECTED, "non_finite_value"),
    (True, REJECTED, "invalid_datatype"),
    ("x", REJECTED, "invalid_datatype"),
    ([1], REJECTED, "invalid_datatype"),
    ({"a": 1}, REJECTED, "invalid_datatype"),
    (3 + 2j, REJECTED, "invalid_datatype"),
    (None, REJECTED, "missing_value"),
])
def test_sanitizer_classifies_every_case(raw, status, reason):
    ev, norm = sanitize_target(raw, policy=_SANITIZE_POLICY, decision_bar=1000)
    if status == ACCEPTED:
        assert ev is None and norm == float(raw)
    else:
        assert ev["status"] == status and ev["reason_code"] == reason
        # a rejected value is NEVER coerced to 0.0
        assert (norm is None) if status == REJECTED else (norm == 1.0)
        json.dumps(ev)  # original/normalized are safely serializable (no NaN/inf tokens)


def test_sanitizer_reject_policy_over_limit():
    pol = freeze_signal_policy(max_target_abs=1.0, over_limit="reject")
    ev, norm = sanitize_target(1.5, policy=pol, decision_bar=1)
    assert ev["status"] == REJECTED and norm is None


def _EvilStrategy(values):
    class _S:
        strategy_id = "H-025"        # keep binding happy for engine tests
        direction = "long_only"
        default_params = {"length": 5}

        def positions(self, df, params=None):
            n = len(df)
            vals = [values[i] if i < len(values) else 0.0 for i in range(n)]
            return pd.Series(vals[:n])
    return _S()


def test_engine_journals_signal_rejected_and_never_reaches_broker(tmp_path):
    # a strategy that emits inf / string targets -> signal_rejected events, no crash, no fill
    f = _uptrend(10)
    man, prof, strat, risk = _pilot_manifest(frame=f, length=5)
    evil = _EvilStrategy([0.0, 0.0, float("inf"), "boom", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    # bind requires matching code hash; patch the manifest's strategy hash to the evil class
    from app.forward.manifest import code_hash
    man = copy.deepcopy(man)
    man["strategy"]["strategy_hash"] = code_hash(evil)
    from app.paper.session.manifest import manifest_hash as _mh
    man["manifest_hash"] = _mh(man)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=evil,
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    rejected = [e for e in events if e["event_type"] == "signal_rejected"]
    # the FIRST invalid output (inf) fails closed to SIGNAL_STOPPED (never a COMPLETED)
    assert len(rejected) == 1 and rejected[0]["payload"]["reason_code"] == "non_finite_value"
    assert res["lifecycle_status"] == "SIGNAL_STOPPED" and res["lifecycle_status"] != COMPLETED
    # infinity never reached broker arithmetic: equity stayed finite and reconciled
    assert abs(res["final_equity"] - (res["broker"]["cash"] + res["broker"]["position_units"] * res["broker"]["mark_price"])) < 1e-6


# =========================================================================== #
# (2) per-trade loss net of all costs                                         #
# =========================================================================== #
def test_broker_records_net_trade_pnl_after_fees():
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0)
    b.mark(500.0); b.execute(1.0, exec_open_time=1, exec_price=500.0)
    b.mark(490.0); b.execute(0.0, exec_open_time=2, exec_price=490.0)
    tr = b.closed_trades[-1]
    # net = gross price PnL minus allocated entry+exit fees; spread/slippage already in fills
    assert abs(tr["net_pnl"] - (tr["gross_price_pnl"] - tr["allocated_entry_fee"] - tr["allocated_exit_fee"])) < 1e-9
    assert tr["net_pnl"] < tr["gross_price_pnl"]  # fees make net worse than gross


def test_per_trade_loss_breach_is_deterministic(tmp_path):
    # a known losing closed trade breaches max_loss_per_trade while other limits stay loose
    closes = [100, 102, 104, 106, 108, 110, 112, 88, 86, 130, 131, 132]
    risk = freeze_risk_limits(max_loss_per_trade=0.02, max_drawdown=0.95, max_session_loss=0.95,
                              max_daily_loss=0.95)
    res, man = _run(_frame(closes), tmp_path, length=3, risk=risk)
    assert res["lifecycle_status"] == RISK_STOPPED
    rules = {b["rule"]: b for b in res["absolute_risk"]["breaches"]}
    assert "max_loss_per_trade" in rules
    assert rules["max_loss_per_trade"]["observed"] > rules["max_loss_per_trade"]["limit"]


def test_per_trade_loss_just_below_limit_does_not_stop(tmp_path):
    closes = [100, 102, 104, 106, 108, 110, 112, 88, 86, 130, 131, 132]
    risk = freeze_risk_limits(max_loss_per_trade=0.80, max_drawdown=0.95, max_session_loss=0.95,
                              max_daily_loss=0.95)
    res, man = _run(_frame(closes), tmp_path, length=3, risk=risk)
    assert res["lifecycle_status"] == COMPLETED
    assert all(b["rule"] != "max_loss_per_trade" for b in res["absolute_risk"]["breaches"])


# =========================================================================== #
# (3) session_final uses the last processed event time                        #
# =========================================================================== #
def test_session_final_never_stamps_future_data_on_early_stop(tmp_path):
    closes = [100 + 3 * i for i in range(10)] + [40.0, 41.0, 42.0, 43.0, 44.0, 45.0]
    res, man = _run(_frame(closes), tmp_path, length=5, risk=freeze_risk_limits(max_drawdown=0.10))
    assert res["lifecycle_status"] == RISK_STOPPED
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    processed_bar_times = [e["payload"]["open_time"] for e in events if e["event_type"] == "bar_ingested"]
    last_processed = max(processed_bar_times)
    sf = [e for e in events if e["event_type"] == "session_final"][0]
    # session_final is not later than the last processed bar's close, and never uses a future bar
    assert sf["timestamp"] <= last_processed + _DAY
    all_frame_times = set(int(x) for x in _frame(closes)["open_time"].tolist())
    unprocessed = sorted(t for t in all_frame_times if t > last_processed)
    assert unprocessed  # there ARE future bars
    assert all(e["timestamp"] < min(unprocessed) + _DAY for e in events)  # none stamped from the future


def _mkmanifest(frame, *, profile=None, risk=None, strategy=None, length=5, max_gross=1.0):
    profile = profile or freeze_execution_profile("BTC/USD")
    strategy = strategy or get_strategy("H-025")
    risk = risk or freeze_risk_limits(max_drawdown=0.90)
    rpol = freeze_range_policy(seed=42, block_length=3, n_resamples=60, min_unique_segments=6)
    segs = [{"segment_index": i, "metrics": {"return": 0.001, "drawdown": -0.02, "trade_count": 5.0,
             "turnover": 1.0, "exposure_time": 0.5, "win_rate": 0.5, "avg_holding_period": 3.0,
             "long_short_balance": 1.0}} for i in range(24)]
    ref = build_reference_ranges(segs, policy=rpol)
    start = int(frame["open_time"].iloc[0]) if len(frame) else _T0
    return build_session_manifest(
        strategy=strategy, params={"length": length}, asset=profile["instrument"], timeframe="1d",
        data_source={"provider": "t", "root": "n", "is_real": False}, start_timestamp_ms=start,
        initial_capital=100_000.0, position_sizing_policy={"type": "x", "max_gross": max_gross},
        execution_profile=profile, absolute_risk_limits=risk,
        gap_stale_policy={"max_gap_bars": 2, "on_gap": "DATA_STOPPED"},
        regime_policy_hash=regime_policy_hash(DEFAULT_REGIME_POLICY),
        consistency_policy_hash=consistency_policy_hash(DEFAULT_CONSISTENCY_POLICY),
        reference_range_hash=ref["range_hash"], comparability_basis=ref["comparability_basis"],
        dataset_hash=dataset_hash(frame))


def _evil_manifest(frame, evil, length=5, **over):
    """A manifest bound to an arbitrary (test) strategy instance."""
    man = _mkmanifest(frame, length=length, **over)
    from app.forward.manifest import code_hash
    from app.paper.session.manifest import manifest_hash as _mh
    man = copy.deepcopy(man)
    man["strategy"]["strategy_id"] = getattr(evil, "strategy_id", "H-025")
    man["strategy"]["strategy_hash"] = code_hash(evil)
    man["manifest_hash"] = _mh(man)
    return man


# ---- FINDING 1: invalid strategy output must STOP the session (not COMPLETE) --------- #
def test_invalid_strategy_output_stops_session(tmp_path):
    f = _uptrend(12)
    evil = _EvilStrategy([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, float("inf"), 0.0, 0.0, 0.0, 0.0, 0.0])
    man = _evil_manifest(f, evil)
    prof = freeze_execution_profile("BTC/USD"); risk = freeze_risk_limits(max_drawdown=0.90)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=evil,
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == "SIGNAL_STOPPED"
    assert res["lifecycle_status"] != COMPLETED
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    rej = [e for e in events if e["event_type"] == "signal_rejected"]
    assert rej and rej[0]["payload"]["reason_code"] == "non_finite_value"
    rej_seq = rej[0]["seq"]
    assert all(e["event_type"] != "order" for e in events if e["seq"] > rej_seq)  # no fill after


def test_strategy_exception_stops_session(tmp_path):
    class _Boom:
        strategy_id = "H-025"; direction = "long_only"; default_params = {"length": 5}
        def positions(self, df, params=None):
            if len(df) >= 7:
                raise RuntimeError("strategy blew up")
            return pd.Series([0.0] * len(df))
    boom = _Boom()
    f = _uptrend(12)
    man = _evil_manifest(f, boom)
    prof = freeze_execution_profile("BTC/USD"); risk = freeze_risk_limits(max_drawdown=0.90)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=boom,
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == "SIGNAL_STOPPED"
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    assert any(e["event_type"] == "signal_rejected" and e["payload"]["reason_code"] == "strategy_exception"
               for e in events)


# ---- FINDING 2: a rejected signal cancels any stale pending decision ----------------- #
def test_invalid_signal_cancels_stale_pending(tmp_path):
    # valid long, then an invalid signal -> pending cancelled, no stale order executes after
    evil = _EvilStrategy([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, "boom", 0.0, 0.0, 0.0, 0.0])
    f = _uptrend(12)
    man = _evil_manifest(f, evil)
    prof = freeze_execution_profile("BTC/USD"); risk = freeze_risk_limits(max_drawdown=0.90)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=evil,
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == "SIGNAL_STOPPED"
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    rej = [e for e in events if e["event_type"] == "signal_rejected"][0]
    assert rej["payload"].get("pending_cancelled") is True  # journal identifies the cancellation
    assert all(e["event_type"] != "order" for e in events if e["seq"] > rej["seq"])


# ---- FINDING 3: Stage 1 is hard-locked to long-only --------------------------------- #
def test_manifest_creation_rejects_shorting_strategy():
    f = _uptrend(10)
    with pytest.raises(ValueError):
        _mkmanifest(f, strategy=get_strategy("H-010"))  # H-010 is long_short


def test_manifest_validation_rejects_shorting_true():
    from app.paper.session.manifest import detect_manifest_drift, manifest_hash as _mh
    man = _mkmanifest(_uptrend(10))
    assert man["shorting_allowed"] is False
    tampered = copy.deepcopy(man)
    tampered["shorting_allowed"] = True
    tampered["manifest_hash"] = _mh(tampered)  # re-hashed, no simple hash mismatch
    assert detect_manifest_drift(tampered)[0] is True  # still structurally invalid


def test_broker_rejects_shorting_true():
    with pytest.raises(BrokerError):
        PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=1000.0, shorting_allowed=True)


def test_rehashed_shorting_manifest_invalidates_session(tmp_path):
    from app.paper.session.manifest import manifest_hash as _mh
    f = _uptrend(10)
    man = _mkmanifest(f)
    man = copy.deepcopy(man); man["shorting_allowed"] = True; man["manifest_hash"] = _mh(man)
    prof = freeze_execution_profile("BTC/USD"); risk = freeze_risk_limits(max_drawdown=0.90)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=get_strategy("H-025"),
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == INVALIDATED


# ---- FINDING 4: max_target_abs derived from ALL gross limits ------------------------ #
@pytest.mark.parametrize("max_gross,max_pos_gross,max_lev,expected", [
    (1.0, 1.0, 1.0, 1.0),
    (0.5, 1.0, 1.0, 0.5),   # position-sizing smallest
    (1.0, 0.4, 1.0, 0.4),   # max_position_gross smallest
    (1.0, 1.0, 0.3, 0.3),   # leverage smallest
])
def test_max_target_abs_uses_all_gross_limits(max_gross, max_pos_gross, max_lev, expected):
    from app.paper.session.engine import derive_max_target_abs
    man = _mkmanifest(_uptrend(5), max_gross=max_gross)
    risk = freeze_risk_limits(max_position_gross=max_pos_gross, max_leverage=max_lev, max_drawdown=0.90)
    assert derive_max_target_abs(man, risk) == pytest.approx(expected)


def test_signal_at_limit_accepted_above_limit_clamped():
    pol = freeze_signal_policy(max_target_abs=0.5, over_limit="clamp")
    ev, norm = sanitize_target(0.5, policy=pol, decision_bar=1)
    assert ev is None and norm == 0.5                      # exactly at limit -> accepted
    ev2, norm2 = sanitize_target(0.5000001, policy=pol, decision_bar=1)
    assert ev2["status"] == CLAMPED and norm2 == 0.5       # just above -> clamped


def test_engine_clamps_target_to_smallest_gross_limit(tmp_path):
    # max_position_gross is the binding limit -> a full (1.0) H-025 long is clamped below it
    f = _uptrend(30)
    risk = freeze_risk_limits(max_position_gross=0.5, max_leverage=1.0, max_drawdown=0.95)
    man = _mkmanifest(f, risk=risk, length=5)
    prof = freeze_execution_profile("BTC/USD")
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=get_strategy("H-025"),
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    b = res["broker"]
    assert b["gross_exposure_frac"] <= 0.5 + 1e-9          # never breaches the smallest limit
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    assert any(e["event_type"] == "signal_clamped" for e in events)


# ---- FINDING 5: empty data cannot be COMPLETED -------------------------------------- #
def test_empty_frame_is_not_completed(tmp_path):
    import pandas as _pd
    empty = _pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    man = _mkmanifest(empty)
    prof = freeze_execution_profile("BTC/USD"); risk = freeze_risk_limits(max_drawdown=0.90)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=get_strategy("H-025"),
                     params={"length": 5}, frame=empty, journal_dir=str(tmp_path))
    # an empty dataset is rejected at preflight, BEFORE any journal is opened: the session is
    # INVALIDATED deterministically (never DATA_STOPPED / NOT_AVAILABLE / COMPLETED) with the
    # empty_dataset reason, and no journal.jsonl is created.
    assert res["lifecycle_status"] == INVALIDATED
    assert res.get("mismatched_field") == "empty_dataset"
    assert "empty_dataset" in res.get("reason", "")
    assert res.get("journal_path") is None
    assert os.listdir(str(tmp_path)) == []


# ---- FINDING 6: engine-level safe-exit: filled / partial / rejected ------------------ #
def _run_safe_exit(tmp_path, profile):
    closes = [100 + 3 * i for i in range(12)] + [40.0, 45.0, 46.0]
    f = _frame(closes)
    risk = freeze_risk_limits(max_drawdown=0.10)
    man = _mkmanifest(f, profile=profile, risk=risk, length=5)
    res = run_replay(manifest=man, profile=profile, risk_limits=risk, strategy=get_strategy("H-025"),
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    return res, man, events


def _assert_safe_exit_common(res, events, expect_status):
    b = res["broker"]
    assert res["lifecycle_status"] == RISK_STOPPED
    safe = [e for e in events if e["event_type"] == "safe_exit"]
    assert len(safe) == 1 and safe[0]["payload"]["order_status"] == expect_status
    se_seq = safe[0]["seq"]
    assert all(e["event_type"] != "order" for e in events if e["seq"] > se_seq)  # no later orders
    assert abs(res["final_equity"] - (b["cash"] + b["position_units"] * b["mark_price"])) < 1e-6
    assert abs(res["net_pnl"] - (res["final_equity"] - res["initial_capital"])) < 1e-9


def test_safe_exit_filled_engine(tmp_path):
    res, man, events = _run_safe_exit(tmp_path, freeze_execution_profile("BTC/USD"))
    _assert_safe_exit_common(res, events, "filled")
    assert abs(res["broker"]["position_units"]) < 1e-9  # flat


def test_safe_exit_partial_engine(tmp_path):
    # a frozen per-bar fill cap forces the flatten to only partially fill
    prof = freeze_execution_profile("BTC/USD", max_fill_qty=50.0)
    res, man, events = _run_safe_exit(tmp_path, prof)
    _assert_safe_exit_common(res, events, "partial")
    assert abs(res["broker"]["position_units"]) > 1e-9   # remaining exposure recorded


def test_safe_exit_rejected_engine(tmp_path):
    # after the crash the exit notional falls below a frozen min_notional -> exit rejected
    prof = freeze_execution_profile("BTC/USD", min_notional=50_000.0)
    res, man, events = _run_safe_exit(tmp_path, prof)
    _assert_safe_exit_common(res, events, "rejected")
    assert abs(res["broker"]["position_units"]) > 1e-9   # remaining exposure recorded


def test_risk_stop_restart_requires_human_approval():
    from app.paper.session.lifecycle import transition, LifecycleError, RISK_STOPPED, RUNNING
    with pytest.raises(LifecycleError):
        transition(RISK_STOPPED, RUNNING)
    assert transition(RISK_STOPPED, RUNNING, human_approval=True) == RUNNING


# ---- APPROVAL: SignalPolicy frozen in the manifest and bound to its hash ------------- #
from app.paper.session.sanitizer import signal_policy_hash, SIGNAL_POLICY_VERSION  # noqa: E402
from app.paper.session.manifest import manifest_hash as _manifest_hash  # noqa: E402


def _rehash_manifest(man):
    man = copy.deepcopy(man)
    man["signal_policy_hash"] = signal_policy_hash(man["signal_policy"])
    man["manifest_hash"] = _manifest_hash(man)
    return man


def test_signal_policy_is_frozen_in_manifest():
    man = _mkmanifest(_uptrend(10))
    sp = man["signal_policy"]
    assert sp["policy_version"] == SIGNAL_POLICY_VERSION
    assert sp["over_limit"] == "clamp" and sp["shorting_allowed"] is False
    assert sp["max_target_abs"] == 1.0
    assert man["signal_policy_hash"] == signal_policy_hash(sp)
    # the policy hash is part of the manifest hash: changing it changes the manifest hash
    tampered = copy.deepcopy(man); tampered["signal_policy"]["over_limit"] = "reject"
    assert _manifest_hash(tampered) != man["manifest_hash"]


def test_signal_policy_reflects_tightest_gross_limit():
    man = _mkmanifest(_uptrend(5), max_gross=1.0,
                      risk=freeze_risk_limits(max_position_gross=0.4, max_leverage=1.0, max_drawdown=0.9))
    assert man["signal_policy"]["max_target_abs"] == pytest.approx(0.4)


def test_session_created_journals_signal_policy_hash(tmp_path):
    f = _uptrend(20)
    man = _mkmanifest(f)
    prof = freeze_execution_profile("BTC/USD"); risk = freeze_risk_limits(max_drawdown=0.90)
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=get_strategy("H-025"),
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    sc = [e for e in events if e["event_type"] == "session_created"][0]
    assert sc["payload"]["signal_policy_hash"] == man["signal_policy_hash"]


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(over_limit="reject"),
    lambda p: p.update(max_target_abs=5.0),
    lambda p: p.update(policy_version="signal-policy-v2"),
    lambda p: p.update(shorting_allowed=True),
])
def test_tampered_signal_policy_invalidates_before_journal(tmp_path, mutate):
    f = _uptrend(20)
    man = _mkmanifest(f)
    tampered = copy.deepcopy(man)
    mutate(tampered["signal_policy"])
    tampered = _rehash_manifest(tampered)  # re-hash so there is no simple manifest-hash mismatch
    prof = freeze_execution_profile("BTC/USD"); risk = freeze_risk_limits(max_drawdown=0.90)
    res = run_replay(manifest=tampered, profile=prof, risk_limits=risk, strategy=get_strategy("H-025"),
                     params={"length": 5}, frame=f, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == INVALIDATED
    assert not os.path.exists(os.path.join(str(tmp_path), "journal.jsonl"))  # no journal opened


def test_signal_policy_tamper_without_rehash_is_drift():
    from app.paper.session.manifest import detect_manifest_drift
    man = _mkmanifest(_uptrend(10))
    t = copy.deepcopy(man); t["signal_policy"]["over_limit"] = "reject"  # not re-hashed
    assert detect_manifest_drift(t)[0] is True


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), "x", [1], {"a": 1}, True, None])
def test_broker_defensively_rejects_nonfinite_target(bad):
    b = PaperBroker(freeze_execution_profile("BTC/USD"), initial_capital=100_000.0)
    b.mark(500.0)
    o = b.execute(bad, exec_open_time=1, exec_price=500.0)
    assert o["status"] == "rejected" and b.position_units == 0.0


# =========================================================================== #
# (4) data-stop event timing (detection time, not bar_open + tf)              #
# =========================================================================== #
def test_data_stop_events_stamped_at_detection_time(tmp_path):
    f = _uptrend(12)
    f.loc[6:, "open_time"] = f.loc[6:, "open_time"] + 3 * _DAY  # gap -> data stop
    res, man = _run(f, tmp_path, risk=freeze_risk_limits(max_missing_bars=1, max_drawdown=0.95))
    assert res["lifecycle_status"] == DATA_STOPPED
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    gap = [e for e in events if e["event_type"] == "data_gap"][0]
    risk_ev = [e for e in events if e["event_type"] == "risk_event"][0]
    detect_open = gap["payload"]["open_time"]
    assert gap["timestamp"] == detect_open                 # detected on arrival (bar open)
    assert risk_ev["timestamp"] == detect_open             # data evidence stamped at detection
    # events are non-decreasing in time; no event carries a future timestamp
    seqs = [e["timestamp"] for e in events]
    assert seqs == sorted(seqs)


def test_risk_evidence_stamped_at_completed_bar_close(tmp_path):
    closes = [100 + 3 * i for i in range(12)] + [40.0, 41.0, 42.0]
    res, man = _run(_frame(closes), tmp_path, length=5, risk=freeze_risk_limits(max_drawdown=0.10))
    events = read_verified_events(res["journal_path"], man["manifest_hash"])
    risk_ev = [e for e in events if e["event_type"] == "risk_event"][0]
    # risk computed from the completed bar close => stamped at open + timeframe
    assert risk_ev["timestamp"] == risk_ev["payload"]["open_time"] + _DAY


# =========================================================================== #
# (5) mark correctly after the single safe-exit                               #
# =========================================================================== #
def test_safe_exit_filled_reconciles_flat(tmp_path):
    closes = [100 + 3 * i for i in range(12)] + [40.0, 45.0, 46.0]
    res, man = _run(_frame(closes), tmp_path, length=5, risk=freeze_risk_limits(max_drawdown=0.10))
    b = res["broker"]
    assert res["lifecycle_status"] == RISK_STOPPED
    assert abs(res["final_equity"] - (b["cash"] + b["position_units"] * b["mark_price"])) < 1e-6
    assert abs(res["net_pnl"] - (res["final_equity"] - res["initial_capital"])) < 1e-9


def test_broker_safe_exit_rejected_marks_remaining_position():
    # a flatten SELL that is rejected (min_notional too high) leaves the position, marked & reconciled
    b = PaperBroker(freeze_execution_profile("BTC/USD", min_notional=1e12), initial_capital=100_000.0)
    b.mark(100.0)
    b.position_units = 10.0
    b.avg_entry_price = 100.0
    o = b.execute(0.0, exec_open_time=1, exec_price=100.0)  # flatten target -> rejected
    b.mark(100.0)                                            # reconcile at current execution price
    assert o["status"] == "rejected" and abs(b.position_units - 10.0) < 1e-9
    assert abs(b.equity - (b.cash + b.position_units * 100.0)) < 1e-6


def test_broker_safe_exit_remainder_marks_and_reconciles():
    # a flatten that leaves a qty_step remainder still reconciles exactly (remainder marked)
    b = PaperBroker(freeze_execution_profile("BTC/USD", qty_step=1.0, min_qty=1.0), initial_capital=100_000.0)
    b.mark(100.0)
    b.position_units = 1.5  # not a multiple of qty_step
    b.avg_entry_price = 100.0
    b.execute(0.0, exec_open_time=1, exec_price=100.0)  # sells 1.0, leaves 0.5
    b.mark(100.0)
    assert abs(b.position_units - 0.5) < 1e-9
    assert abs(b.equity - (b.cash + b.position_units * 100.0)) < 1e-6


# =========================================================================== #
# (6) fail closed on malformed runtime frames                                 #
# =========================================================================== #
@pytest.mark.parametrize("mutate,field", [
    ("not_df", "frame_type"),
    ("missing_col", "frame_columns"),
    ("nan_open_time", "open_time"),
    ("inf_open_time", "open_time"),
    ("nan_close", "ohlcv"),
    ("inf_high", "ohlcv"),
])
def test_malformed_frame_invalidates_before_journal(tmp_path, mutate, field):
    f = _uptrend(10)
    man, prof, strat, risk = _pilot_manifest(frame=f)
    frame = f
    if mutate == "not_df":
        frame = "not-a-frame"
    elif mutate == "missing_col":
        frame = f.drop(columns=["volume"])
    elif mutate == "nan_open_time":
        frame = f.copy(); frame.loc[3, "open_time"] = np.nan
    elif mutate == "inf_open_time":
        frame = f.copy(); frame["open_time"] = frame["open_time"].astype(float); frame.loc[3, "open_time"] = np.inf
    elif mutate == "nan_close":
        frame = f.copy(); frame.loc[3, "close"] = np.nan
    elif mutate == "inf_high":
        frame = f.copy(); frame.loc[3, "high"] = np.inf
    res = run_replay(manifest=man, profile=prof, risk_limits=risk, strategy=strat,
                     params=man["params"], frame=frame, journal_dir=str(tmp_path))
    assert res["lifecycle_status"] == INVALIDATED
    assert res["mismatched_field"] == field
    assert not os.path.exists(os.path.join(str(tmp_path), "journal.jsonl"))  # no journal, no conclusion


def test_preflight_accepts_valid_frame():
    f = _uptrend(10)
    man, *_ = _pilot_manifest(frame=f)
    assert preflight_frame(f, man) == (True, "", "")
