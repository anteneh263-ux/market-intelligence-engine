"""Tests for the locked forward-testing lifecycle (app.forward).

Deterministic and offline: synthetic OHLCV frames + injected promotion reports, so no
network, no CSV data and no real promotion-gate run are required. Covers the 16
pre-registered requirements of the locked forward test.

Research only: the module under test is a SIMULATION — no live broker, no real orders.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.forward import (ForwardEngine, ForwardError, build_manifest, evaluate_forward)
from app.forward.simulate import executed_positions
from app.paper.schema import resolve_target
from app.strategy_research.strategies import (available_strategies, get_strategy,
                                              register_strategy)
from app.strategy_research.strategies.base import Strategy

BASE_MS = 1_577_836_800_000        # 2020-01-01 UTC
DAY = 86_400_000
PROMOTE = {"decision": "PROMOTE", "gates": []}


# --------------------------------------------------------------------------- #
# synthetic data + strategies
# --------------------------------------------------------------------------- #
def _df(n=400, seed=0):
    rng = np.random.default_rng(seed)
    close = 100.0 + rng.normal(0, 1, n).cumsum() + np.linspace(0, 25, n)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    ot = BASE_MS + np.arange(n) * DAY
    return pd.DataFrame({"open_time": ot, "open": open_, "high": high, "low": low,
                         "close": close, "volume": np.full(n, 1000.0)})


def _start_date(idx):
    ms = BASE_MS + idx * DAY
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class _MA(Strategy):
    strategy_id = "FWD-MA"
    name = "forward MA"
    direction = "long_short"
    default_params = {"n": 5}

    def positions(self, df, params=None):
        n = int((params or self.default_params).get("n", 5))
        c = df["close"]
        ma = c.rolling(n, min_periods=1).mean()
        return np.sign(c - ma).fillna(0.0)


class _MA_CodeChanged(_MA):
    """Same behaviour, DIFFERENT class source text -> different code hash."""
    strategy_id = "FWD-MA"

    def positions(self, df, params=None):
        n = int((params or self.default_params).get("n", 5))
        c = df["close"]
        moving_average = c.rolling(n, min_periods=1).mean()      # renamed local
        signal = np.sign(c - moving_average)                     # extra line
        return signal.fillna(0.0)


class _Repaint(Strategy):
    """Non-causal: position depends on the NEXT bar's close (look-ahead / repaint)."""
    strategy_id = "FWD-REPAINT"
    name = "forward repaint"
    direction = "long_short"
    default_params = {}

    def positions(self, df, params=None):
        c = df["close"].to_numpy(dtype=float)
        fwd = np.zeros(len(c))
        if len(c) > 1:
            fwd[:-1] = np.sign(c[1:] - c[:-1])                   # uses the future bar
        return pd.Series(fwd, index=df.index)


register_strategy(_MA())
register_strategy(_Repaint())


def _manifest_for(strategy=None, thresholds=None, tmp_root="."):
    s = strategy or _MA()
    return build_manifest(
        strategy=s, params=dict(s.default_params), assets=["BTC/USD"], timeframe="1d",
        start_date="2021-01-01", cost_bps=5.0, slippage_bps=5.0, declared_inputs=None,
        promotion_ref="p.json", backtest_ref=None, oos_ref=None, thresholds=thresholds,
        repo_root=tmp_root)


# --------------------------------------------------------------------------- #
# 1. Only PROMOTE can start a forward test
# --------------------------------------------------------------------------- #
def test_01_only_promote_can_start(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    sd = _start_date(300)
    for bad in ("HOLD", "REJECT"):
        raised = False
        try:
            eng.start("FWD-MA", ["BTC/USD"], start_date=sd, df=df,
                      promotion_report={"decision": bad})
        except ForwardError:
            raised = True
        assert raised, f"{bad} must not start a forward test"
    m = eng.start("FWD-MA", ["BTC/USD"], start_date=sd, df=df, promotion_report=PROMOTE,
                  ts="2021-01-01T00:00:00Z")
    assert m["status"] in {"ACTIVE", "DRAFT"}
    assert m["promotion_decision"] == "PROMOTE"


# --------------------------------------------------------------------------- #
# 2. Manifest and forward_test_id are deterministic
# --------------------------------------------------------------------------- #
def test_02_manifest_and_id_deterministic(tmp_path):
    from app.forward import canonical_json
    a = _manifest_for(tmp_root=str(tmp_path))
    b = _manifest_for(tmp_root=str(tmp_path))
    assert a["forward_test_id"] == b["forward_test_id"]
    assert canonical_json(a) == canonical_json(b)
    assert a["forward_test_id"].startswith("FT-FWD-MA-")


# --------------------------------------------------------------------------- #
# 3. Unchanged strategy passes the integrity check (and runs)
# --------------------------------------------------------------------------- #
def test_03_unchanged_passes_integrity(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    m = eng.start("FWD-MA", ["BTC/USD"], start_date=_start_date(300), df=df,
                  promotion_report=PROMOTE, ts="t0")
    assert m["status"] == "ACTIVE"
    out = eng.run(m["forward_test_id"], df=df, ts="t1")
    assert out["ran"] is True and out["status"] == "ACTIVE"
    assert out["integrity"]["ok"] is True
    assert out["new_observations"] > 0


# --------------------------------------------------------------------------- #
# 4. Changed CODE -> INVALIDATED
# --------------------------------------------------------------------------- #
def test_04_changed_code_invalidates(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    m = eng.start("FWD-MA", ["BTC/USD"], start_date=_start_date(300), df=df,
                  promotion_report=PROMOTE, ts="t0")
    ftid = m["forward_test_id"]
    out = eng.run(ftid, df=df, strategy=_MA_CodeChanged(), ts="t1")
    assert out["status"] == "INVALIDATED" and out["ran"] is False
    assert any(c["field"] == "code_hash" for c in out["integrity"]["changes"])
    assert eng.store.load_manifest(ftid)["status"] == "INVALIDATED"


# --------------------------------------------------------------------------- #
# 5. Changed PARAMETER -> INVALIDATED
# --------------------------------------------------------------------------- #
def test_05_changed_param_invalidates(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    m = eng.start("FWD-MA", ["BTC/USD"], start_date=_start_date(300), df=df,
                  promotion_report=PROMOTE, ts="t0")
    ftid = m["forward_test_id"]
    tweaked = _MA()
    tweaked.default_params = {"n": 9}                    # same code, different params
    out = eng.run(ftid, df=df, strategy=tweaked, ts="t1")
    assert out["status"] == "INVALIDATED"
    assert any(c["field"] == "param_hash" for c in out["integrity"]["changes"])


# --------------------------------------------------------------------------- #
# 6. Original history preserved after invalidation
# --------------------------------------------------------------------------- #
def test_06_history_preserved_after_invalidation(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    m = eng.start("FWD-MA", ["BTC/USD"], start_date=_start_date(300), df=df,
                  promotion_report=PROMOTE, ts="t0")
    ftid = m["forward_test_id"]
    locked_code = m["code_hash"]
    locked_param = m["param_hash"]
    eng.run(ftid, df=df, ts="t1")                         # collect some observations
    obs_before = len(eng.store.read_observations(ftid))
    eng.run(ftid, df=df, strategy=_MA_CodeChanged(), ts="t2")   # invalidate
    man = eng.store.load_manifest(ftid)
    assert man["status"] == "INVALIDATED"
    assert man["code_hash"] == locked_code and man["param_hash"] == locked_param
    assert len(eng.store.read_observations(ftid)) == obs_before   # observations untouched
    evs = eng.store.read_events(ftid)
    assert [e["to"] for e in evs][:2] == ["DRAFT", "ACTIVE"]      # prior events kept
    assert evs[-1]["to"] == "INVALIDATED"


# --------------------------------------------------------------------------- #
# 7. A new strategy version gets a NEW forward-test id
# --------------------------------------------------------------------------- #
def test_07_new_version_new_id(tmp_path):
    a = _manifest_for(_MA(), tmp_root=str(tmp_path))
    b = _manifest_for(_MA_CodeChanged(), tmp_root=str(tmp_path))
    assert a["code_hash"] != b["code_hash"]
    assert a["forward_test_id"] != b["forward_test_id"]


# --------------------------------------------------------------------------- #
# 8. Forward data is never used as training data
# --------------------------------------------------------------------------- #
def test_08_forward_data_separate_from_training(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    split = 300
    start_ms = BASE_MS + split * DAY
    m = eng.start("FWD-MA", ["BTC/USD"], start_date=_start_date(split), df=df,
                  promotion_report=PROMOTE, ts="t0")
    eng.run(m["forward_test_id"], df=df, ts="t1")
    obs = eng.store.read_observations(m["forward_test_id"])
    assert obs, "expected forward observations"
    assert all(o["candle_open_time_ms"] >= start_ms for o in obs)   # never a training bar
    assert all(o["data_partition"] == "forward" for o in obs)
    # reference was computed only on the training slice (bars strictly before start)
    assert m["backtest_reference"]["n_train_bars"] == split


# --------------------------------------------------------------------------- #
# 9. A signal is executed no earlier than the NEXT bar
# --------------------------------------------------------------------------- #
def test_09_signal_executed_next_bar():
    df = _df(50)
    strat = _MA()
    raw = strat.positions(df).to_numpy()
    tgt = np.array([resolve_target(strat.direction, float(x)) for x in raw], dtype=float)
    ex = executed_positions(strat, df)
    assert ex[0] == 0.0                                   # nothing held on the first bar
    assert np.array_equal(ex[1:], tgt[:-1])               # today's fill = yesterday's signal


# --------------------------------------------------------------------------- #
# 10. Historical-tail signal matches the backtest -> activation
# --------------------------------------------------------------------------- #
def test_10_tail_matches_backtest_activates(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    m = eng.start("FWD-MA", ["BTC/USD"], start_date=_start_date(300), df=df,
                  promotion_report=PROMOTE, ts="t0")
    assert m["status"] == "ACTIVE"
    chk = m["activation"]["check"]
    assert chk["ok"] and chk["causal"] and chk["engine_equivalent"]


# --------------------------------------------------------------------------- #
# 11. A signal deviation (look-ahead / repaint) BLOCKS activation
# --------------------------------------------------------------------------- #
def test_11_signal_deviation_blocks_activation(tmp_path):
    eng = ForwardEngine(root=str(tmp_path))
    df = _df(400)
    m = eng.start("FWD-REPAINT", ["BTC/USD"], start_date=_start_date(300), df=df,
                  promotion_report=PROMOTE, ts="t0")
    assert m["status"] == "DRAFT"                          # not activated
    assert m["activation"]["activated"] is False
    assert m["activation"]["check"]["causal"] is False
    # a DRAFT test refuses to collect forward observations
    raised = False
    try:
        eng.run(m["forward_test_id"], df=df, ts="t1")
    except ForwardError:
        raised = True
    assert raised


# --------------------------------------------------------------------------- #
# 12. Too little data -> EXTEND
# --------------------------------------------------------------------------- #
def test_12_too_little_data_extend(tmp_path):
    man = _manifest_for(tmp_root=str(tmp_path))
    fm = {"observations": 5, "active_days": 3, "num_trades": 1, "max_drawdown": 0.1,
          "sharpe_per_period": 0.2, "total_return": 0.05}
    rep = evaluate_forward(man, fm, {"sharpe_per_period": 0.1})
    assert rep["decision"] == "EXTEND"


# --------------------------------------------------------------------------- #
# 13. Too high drawdown -> FAILED
# --------------------------------------------------------------------------- #
def test_13_too_high_drawdown_failed(tmp_path):
    man = _manifest_for(tmp_root=str(tmp_path))
    fm = {"observations": 120, "active_days": 90, "num_trades": 25, "max_drawdown": 0.90,
          "sharpe_per_period": 0.3, "total_return": 0.4}
    rep = evaluate_forward(man, fm, {"sharpe_per_period": 0.2})
    assert rep["decision"] == "FAILED"
    assert any(g["gate"] == "max_drawdown" and g["status"] == "FAIL" for g in rep["gates"])


# --------------------------------------------------------------------------- #
# 14. All mandatory requirements met -> PASSED
# --------------------------------------------------------------------------- #
def test_14_all_mandatory_met_passed(tmp_path):
    man = _manifest_for(tmp_root=str(tmp_path))
    fm = {"observations": 120, "active_days": 90, "num_trades": 25, "max_drawdown": 0.20,
          "sharpe_per_period": 0.06, "total_return": 0.30}
    rep = evaluate_forward(man, fm, {"sharpe_per_period": 0.10})
    assert rep["decision"] == "PASSED"


# --------------------------------------------------------------------------- #
# 15. NOT_AVAILABLE is never treated as PASSED (and positive return alone is not PASSED)
# --------------------------------------------------------------------------- #
def test_15_not_available_never_passed(tmp_path):
    man = _manifest_for(tmp_root=str(tmp_path))
    # good, well-powered forward numbers, but the reference Sharpe is unavailable:
    fm = {"observations": 120, "active_days": 90, "num_trades": 25, "max_drawdown": 0.20,
          "sharpe_per_period": 0.06, "total_return": 0.30}
    rep = evaluate_forward(man, fm, {"sharpe_per_period": None})
    assert rep["decision"] != "PASSED" and rep["decision"] == "EXTEND"
    assert any(g["status"] == "NOT_AVAILABLE" for g in rep["gates"])
    # positive return alone must never PASS: strong return but Sharpe below the floor
    fm2 = {"observations": 120, "active_days": 90, "num_trades": 25, "max_drawdown": 0.20,
           "sharpe_per_period": -0.10, "total_return": 0.50}
    rep2 = evaluate_forward(man, fm2, {"sharpe_per_period": 0.10})
    assert rep2["decision"] == "FAILED"


# --------------------------------------------------------------------------- #
# 16. The forward package is additive — it does not disturb the strategy registry
#     (proxy for "the existing suite continues to pass").
# --------------------------------------------------------------------------- #
def test_16_forward_package_is_additive():
    import app.forward as fwd
    # importing the package registers no strategy and exposes no Strategy subclass
    assert not any(isinstance(getattr(fwd, a, None), Strategy) for a in dir(fwd))
    ids = available_strategies()
    assert not any(i.startswith("FT-") for i in ids)      # forward-test ids never leak in
    assert get_strategy("H-002") is not None              # existing strategies still resolve
