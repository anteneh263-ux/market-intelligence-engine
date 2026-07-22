"""Tests for the paper-trading module (paper-trading-v1). All OFFLINE: an injected
FakeSource supplies deterministic candles, so no network is used. Covers look-ahead,
double candles, restart/reconstruction, fees, short vs long-only, missing/dup/
mis-sorted data (safe stop), max-1x exposure, and buy-and-hold parity."""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from app.strategy_research.providers.base import DataSource
from app.paper.engine import PaperEngine, PaperDataError, strategy_ids
from app.paper.schema import BUY_AND_HOLD_ID, resolve_target, tf_ms
from app.paper import ledger
from app.paper.schema import drop_incomplete
from app.strategy_research.providers.http_sources import normalize_coinbase

BASE_MS = 1_577_836_800_000  # 2020-01-01T00:00:00Z
STEP = 86_400_000
FEE_BPS, SLIP_BPS = 10.0, 5.0
FEE_RATE, SLIP_RATE = FEE_BPS / 1e4, SLIP_BPS / 1e4


def build_candles(n: int) -> pd.DataFrame:
    i = np.arange(n)
    close = 100.0 + 10.0 * np.sin(i / 15.0) + i * 0.05
    # opens deliberately DIFFER from the previous close so a look-ahead test can tell
    # "next candle open" apart from "signal candle close".
    openp = close * 0.98 + 0.13
    high = np.maximum(openp, close) * 1.01
    low = np.minimum(openp, close) * 0.99
    return pd.DataFrame({
        "open_time": BASE_MS + i * STEP, "open": openp, "high": high,
        "low": low, "close": close, "volume": np.full(n, 1000.0),
    })


class FakeSource(DataSource):
    name = "fake"
    is_real = True

    def __init__(self, df: pd.DataFrame, visible: int):
        self.df = df
        self.visible = visible

    def _fetch(self, req):
        return self.df.head(self.visible).copy()


def _engine(tmp_path, df, visible):
    fake = FakeSource(df, visible)
    eng = PaperEngine(root=str(tmp_path / "paper"), provider="fake", data_source=fake)
    return eng, fake


def _bh_events(eng, market="BTC/USD"):
    return ledger.read_events(eng.log_dir, BUY_AND_HOLD_ID, market)


# --------------------------------------------------------------------------- #
# 1. look-ahead: signal at close t, fill at the NEXT candle's OPEN
# --------------------------------------------------------------------------- #
def test_lookahead_fill_uses_next_candle_open(tmp_path):
    df = build_candles(285)
    eng, fake = _engine(tmp_path, df, visible=280)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    assert _bh_events(eng) == []                      # init makes NO trades (warm-up only)
    fake.visible = 281                                # one new completed candle appears
    eng.update()
    ev = _bh_events(eng)[-1]
    nxt = df.iloc[280]                                # the candle we should fill on
    prev = df.iloc[279]                               # the signal candle (its close must NOT be used)
    assert ev["traded"] and ev["prev_position"] == 0 and ev["new_position"] == 1
    assert ev["raw_open"] == float(nxt["open"])
    assert abs(ev["fill_price"] - float(nxt["open"]) * (1 + SLIP_RATE)) < 1e-9
    assert ev["execution_time_ms"] == int(nxt["open_time"])
    # fill is on the NEXT open, never the signal candle's close
    assert abs(ev["fill_price"] - float(prev["close"]) * (1 + SLIP_RATE)) > 1e-6


# --------------------------------------------------------------------------- #
# 2. fees + notional exact, and max 1x exposure
# --------------------------------------------------------------------------- #
def test_fees_notional_and_max_1x(tmp_path):
    df = build_candles(285)
    eng, fake = _engine(tmp_path, df, visible=280)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    fake.visible = 281
    eng.update()
    ev = _bh_events(eng)[-1]
    E = 10_000.0
    # fee-reserved sizing: notional == E*(1-fee_rate); fee == notional*fee_rate; cash ~ 0
    assert abs(ev["notional"] - E * (1 - FEE_RATE)) < 1e-6
    assert abs(ev["fee"] - ev["notional"] * FEE_RATE) < 1e-9
    assert ev["cash"] >= -1e-9                                   # never financed by negative cash
    # max 1x: gross exposure <= pre-trade equity
    assert abs(ev["total_units"] * ev["fill_price"]) <= E + 1e-6
    # slippage recorded separately, on the traded notional at the raw open
    assert abs(ev["slippage"] - abs(ev["total_units"]) * ev["raw_open"] * SLIP_RATE) < 1e-6


# --------------------------------------------------------------------------- #
# 3. same candle never processed twice
# --------------------------------------------------------------------------- #
def test_no_double_processing(tmp_path):
    df = build_candles(285)
    eng, fake = _engine(tmp_path, df, visible=280)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    fake.visible = 282
    eng.update()
    n1 = len(_bh_events(eng))
    st1 = json.load(open(eng.state_path))
    eng.update()                                       # no new candles revealed
    n2 = len(_bh_events(eng))
    st2 = json.load(open(eng.state_path))
    assert n1 == n2                                    # no new ledger events
    assert st1["accounts"] == st2["accounts"]          # state unchanged (idempotent)


# --------------------------------------------------------------------------- #
# 4. restart: reconstruct state from JSONL if state.json is deleted; pending survives
# --------------------------------------------------------------------------- #
def test_restart_reconstructs_and_pending_survives(tmp_path):
    df = build_candles(285)
    eng, fake = _engine(tmp_path, df, visible=280)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    fake.visible = 282
    eng.update()
    live = json.load(open(eng.state_path))["accounts"]
    os.remove(eng.state_path)                           # simulate lost state
    rebuilt = eng._load_state(eng._load_config())["accounts"]
    for key in live:
        for f in ("cash", "total_units", "position", "pending_signal",
                  "watermark_open_ms", "last_close"):
            assert abs(float(live[key][f]) - float(rebuilt[key][f])) < 1e-9 \
                if isinstance(live[key][f], (int, float)) else live[key][f] == rebuilt[key][f]
    # continue after restart: no new candles -> no re-trade
    n_before = len(_bh_events(eng))
    eng.update()
    assert len(_bh_events(eng)) == n_before


# --------------------------------------------------------------------------- #
# 5. short vs long-only
# --------------------------------------------------------------------------- #
def test_resolve_target_respects_long_only():
    assert resolve_target("long_only", -1) == 0
    assert resolve_target("long_only", 1) == 1
    assert resolve_target("long_short", -1) == -1
    assert resolve_target("long_short", 0) == 0


def test_long_only_account_never_shorts(tmp_path):
    df = build_candles(300)
    eng, fake = _engine(tmp_path, df, visible=270)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=270)
    fake.visible = 300
    eng.update()
    # H-025 is a long-only strategy: no event may ever show a short position
    for ev in ledger.read_events(eng.log_dir, "H-025", "BTC/USD"):
        assert ev["new_position"] >= 0


def test_short_accounting_is_1x(tmp_path):
    df = build_candles(10)
    eng, _ = _engine(tmp_path, df, visible=10)
    cfg = {"fee_bps": FEE_BPS, "slippage_bps": SLIP_BPS}
    acc = {"cash": 10_000.0, "total_units": 0.0, "position": 0,
           "pending_signal": -1, "pending_signal_time_ms": int(df.iloc[0]["open_time"])}
    acc = eng._fill_and_record(acc, "long_short", df, 1, np.zeros(10),
                               "BTC/USD", "H-005", "fake", cfg)
    assert acc["position"] == -1 and acc["total_units"] < 0     # actually short
    assert acc["cash"] >= -1e-9
    ev = ledger.read_events(eng.log_dir, "H-005", "BTC/USD")[-1]
    assert abs(ev["total_units"] * ev["fill_price"]) <= 10_000.0 + 1e-6   # 1x short


# --------------------------------------------------------------------------- #
# 6. missing / duplicate / mis-sorted candles -> SAFE STOP (no state change)
# --------------------------------------------------------------------------- #
def test_new_candles_rejects_duplicate(tmp_path):
    eng, _ = _engine(tmp_path, build_candles(5), 5)
    dup = pd.DataFrame({"open_time": [BASE_MS, BASE_MS, BASE_MS + STEP],
                        "open": [1, 1, 1], "high": [1, 1, 1], "low": [1, 1, 1],
                        "close": [1, 1, 1], "volume": [1, 1, 1]})
    try:
        eng._new_candles(dup, None); assert False, "expected safe stop"
    except PaperDataError as e:
        assert "duplicat" in str(e).lower() or "ascending" in str(e).lower()


def test_new_candles_rejects_missorted(tmp_path):
    eng, _ = _engine(tmp_path, build_candles(5), 5)
    bad = build_candles(5).iloc[::-1].reset_index(drop=True)
    try:
        eng._new_candles(bad, None); assert False
    except PaperDataError:
        pass


def test_new_candles_rejects_gap(tmp_path):
    eng, _ = _engine(tmp_path, build_candles(5), 5)
    df = build_candles(5)
    wm = int(df.iloc[1]["open_time"])
    gapped = df.drop(index=2).reset_index(drop=True)   # candle 2 missing -> gap after watermark
    try:
        eng._new_candles(gapped, wm); assert False
    except PaperDataError as e:
        assert "missing" in str(e).lower()


def test_empty_provider_is_safe_noop(tmp_path):
    df = build_candles(285)
    eng, fake = _engine(tmp_path, df, visible=280)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    st_before = json.load(open(eng.state_path))
    fake.df = df.head(0)                                # provider now returns nothing
    out = eng.update()
    st_after = json.load(open(eng.state_path))
    assert st_before["accounts"] == st_after["accounts"]   # unchanged, no crash


def test_gap_update_leaves_state_unchanged(tmp_path):
    df = build_candles(285)
    eng, fake = _engine(tmp_path, df, visible=280)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    st_before = json.load(open(eng.state_path))["accounts"]
    # reveal candles but with a hole right after the watermark
    holed = pd.concat([df.head(280), df.iloc[282:284]]).reset_index(drop=True)
    fake.df = holed; fake.visible = len(holed)
    try:
        eng.update(); raised = False
    except PaperDataError:
        raised = True
    st_after = json.load(open(eng.state_path))["accounts"]
    assert raised and st_before == st_after


# --------------------------------------------------------------------------- #
# 7. buy-and-hold parity + leaderboard
# --------------------------------------------------------------------------- #
def test_buy_and_hold_same_terms_and_leaderboard(tmp_path):
    df = build_candles(300)
    eng, fake = _engine(tmp_path, df, visible=280)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    fake.visible = 300
    eng.update()
    bh = _bh_events(eng)
    # B&H trades exactly once (its target never changes after going long) with same terms
    trades = [e for e in bh if e["traded"]]
    assert len(trades) == 1 and trades[0]["new_position"] == 1
    assert abs(trades[0]["fee"] - trades[0]["notional"] * FEE_RATE) < 1e-9
    board = eng.leaderboard()["markets"]["BTC/USD"]
    assert "buy_and_hold_equity" in board and len(board["ranking"]) == len(strategy_ids())
    assert board["ranking"][0]["rank"] == 1


def test_exposure_never_exceeds_1x_and_cash_nonnegative(tmp_path):
    """Across a full multi-candle run (with reversals), every account must keep
    cash >= 0 and gross exposure |units*close| <= equity (max 1x, longs and shorts)."""
    df = build_candles(300)
    eng, fake = _engine(tmp_path, df, visible=270)
    eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=270)
    fake.visible = 300
    eng.update()
    checked = 0
    for sid in strategy_ids() + [BUY_AND_HOLD_ID]:
        for ev in ledger.read_events(eng.log_dir, sid, "BTC/USD"):
            assert ev["cash"] >= -1e-6                             # never financed
            if ev["traded"]:                                       # 1x AT EXECUTION
                exposure = abs(ev["total_units"]) * ev["fill_price"]
                assert exposure <= ev["equity_pre"] + 1e-6
            checked += 1
    assert checked > 0


class PerMarketSource(DataSource):
    """Serves different data per asset (empty frame = market unavailable)."""
    name = "fake2"
    is_real = True

    def __init__(self, by_asset):
        self.by_asset = by_asset

    def _fetch(self, req):
        empty = pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
        return self.by_asset.get(req.asset, empty).copy()


def test_init_two_markets_creates_54_unique_strategy_accounts(tmp_path):
    df = build_candles(285)
    eng, _ = _engine(tmp_path, df, visible=280)
    res = eng.init(markets=["BTC/USD", "ETH/USD"], capital=10_000,
                   fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    assert res["markets"] == ["BTC/USD", "ETH/USD"]
    assert res["strategy_accounts"] == 54          # 27 paper-eligible strategies x 2 markets
    assert res["benchmark_accounts"] == 2          # one buy-and-hold per market
    st = json.load(open(eng.state_path))["accounts"]
    strat_keys = [k for k in st if not k.startswith(BUY_AND_HOLD_ID)]
    bench_keys = [k for k in st if k.startswith(BUY_AND_HOLD_ID)]
    assert len(strat_keys) == 54 and len(set(strat_keys)) == 54   # unique
    assert len(bench_keys) == 2
    # both markets actually represented
    assert sum(k.endswith("BTC-USD") for k in strat_keys) == 27
    assert sum(k.endswith("ETH-USD") for k in strat_keys) == 27


def test_missing_market_raises_not_silent(tmp_path):
    df = build_candles(285)
    src = PerMarketSource({"BTC/USD": df})             # ETH/USD absent
    eng = PaperEngine(root=str(tmp_path / "paper"), provider="fake2", data_source=src)
    raised = False
    try:
        eng.init(markets=["BTC/USD", "ETH/USD"], capital=10_000,
                 fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=280)
    except PaperDataError as e:
        raised = True
        assert "ETH" in str(e)                         # names the missing market
    assert raised, "missing market must fail loudly, never be silently dropped"
    # all-or-nothing: nothing persisted when a market is missing
    assert not os.path.exists(eng.state_path)
    assert not os.path.exists(eng.config_path)


def test_status_warns_on_post_execution_exposure_drift(tmp_path):
    """1x holds AT EXECUTION. A short whose price later rises has gross exposure > 1x;
    status must SHOW that (leverage_at_mark) and raise an exposure warning, not claim <=1x."""
    root = tmp_path / "paper"
    os.makedirs(root, exist_ok=True)
    eng = PaperEngine(root=str(root), provider="fake",
                      data_source=FakeSource(build_candles(5), 5))
    cfg = {"schema_version": "paper-v1", "markets": ["BTC/USD"], "capital": 10_000.0,
           "fee_bps": FEE_BPS, "slippage_bps": SLIP_BPS, "timeframe": "1d",
           "provider": "fake", "running": True}
    eng._atomic_write(eng.config_path, cfg)
    # 1x short opened at 100 (units=-100, cash=20000 -> equity 10000); price then rose to 150
    short = {"cash": 20_000.0, "total_units": -100.0, "position": -1, "pending_signal": -1,
             "pending_signal_time_ms": 0, "watermark_open_ms": 0, "last_close": 150.0,
             "warmup_done": True, "direction": "long_short"}
    bh = {"cash": 0.0, "total_units": 100.0, "position": 1, "pending_signal": 1,
          "pending_signal_time_ms": 0, "watermark_open_ms": 0, "last_close": 150.0,
          "warmup_done": True, "direction": "long_only"}
    eng._save_state({"schema_version": "paper-v1", "accounts": {
        ledger.account_key("H-005", "BTC/USD"): short,
        ledger.account_key(BUY_AND_HOLD_ID, "BTC/USD"): bh}})
    out = eng.status()
    row = [r for r in out["markets"]["BTC/USD"]["accounts"] if r["strategy_id"] == "H-005"][0]
    assert row["exposure_warning"] is True
    assert row["leverage_at_mark"] > 1.0               # ~3x from the adverse move
    assert any(w["strategy_id"] == "H-005" for w in out["exposure_warnings"])


# --------------------------------------------------------------------------- #
# Coinbase provider (offline: normalization only, no network)
# --------------------------------------------------------------------------- #
def _cb_row(ts, low, high, opn, close, vol):
    # Coinbase documented order: [time(s), low, high, open, close, volume]
    return [ts, low, high, opn, close, vol]


def test_coinbase_normalize_format_and_mapping():
    rows = [_cb_row(1_600_000_000, 90.0, 110.0, 100.0, 105.0, 5.0)]
    df = normalize_coinbase(rows)
    assert list(df.columns) == ["open_time", "open", "high", "low", "close", "volume"]
    r = df.iloc[0]
    assert r["open_time"] == 1_600_000_000 * 1000          # seconds -> ms
    assert r["open"] == 100.0 and r["high"] == 110.0 and r["low"] == 90.0 and r["close"] == 105.0


def test_coinbase_normalize_sorts_reversed_order():
    rows = [_cb_row(1_600_000_000 + 2 * 86400, 1, 2, 1.5, 1.7, 1),
            _cb_row(1_600_000_000 + 1 * 86400, 1, 2, 1.5, 1.7, 1),
            _cb_row(1_600_000_000 + 0 * 86400, 1, 2, 1.5, 1.7, 1)]   # newest-first
    df = normalize_coinbase(rows)
    ot = df["open_time"].to_numpy()
    assert (ot[:-1] < ot[1:]).all()                        # ascending after normalize


def test_coinbase_normalize_dedups():
    rows = [_cb_row(1_600_000_000, 1, 2, 1.5, 1.7, 1),
            _cb_row(1_600_000_000, 1, 2, 1.5, 1.9, 9)]     # duplicate timestamp
    df = normalize_coinbase(rows)
    assert len(df) == 1


def test_drop_incomplete_candle():
    df = build_candles(5)
    step = STEP
    # "now" placed so the LAST candle's close (open+step) is still in the future
    now_ms = int(df.iloc[-1]["open_time"]) + step - 1
    kept = drop_incomplete(df, step, now_ms)
    assert len(kept) == 4                                   # last (unfinished) candle dropped
    assert int(kept.iloc[-1]["open_time"]) == int(df.iloc[-2]["open_time"])


def test_too_few_candles_fails_and_leaves_nothing(tmp_path):
    eng, _ = _engine(tmp_path, build_candles(1), visible=1)
    raised = False
    try:
        eng.init(markets=["BTC/USD"], capital=10_000, fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, warmup=250)
    except PaperDataError:
        raised = True
    assert raised
    assert not os.path.exists(eng.state_path) and not os.path.exists(eng.config_path)


# --------------------------------------------------------------------------- #
# CLI: friendly "not initialized" messages before init (no traceback)
# --------------------------------------------------------------------------- #
def _args(**kw):
    import types
    base = dict(action="status", markets="BTC/USD,ETH/USD", capital=10_000.0,
                fee_bps=10.0, slippage_bps=5.0, warmup=300, provider="coinbase",
                csv_root="data_csv", root="/tmp/__no_such_paper__")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_cli_not_initialized_messages(tmp_path):
    import io, contextlib
    from app.paper.cli_paper import run_paper
    root = str(tmp_path / "empty")            # no config.json here
    for action in ("status", "update", "leaderboard", "stop"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_paper(_args(action=action, root=root))
        out = buf.getvalue()
        assert rc == 1
        assert "Paper trading is not initialized" in out
        assert "Traceback" not in out
