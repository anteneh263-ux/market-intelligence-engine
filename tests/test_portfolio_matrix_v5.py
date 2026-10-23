"""srp-matrix-v5 (portfolio evaluation): versioning boundaries and a planted-edge check.

All data are synthetic and in memory; nothing is written to the Knowledge Base.
"""
import contextlib
import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest

from app.research_core import versioning
from app.research_core.orchestrator import Orchestrator
from app.strategy_research.evaluation.frozen import (MATRIX_V1, MATRIX_V2, MATRIX_V3,
                                                     MATRIX_V4, MATRIX_V5, EvalRequest)
from app.strategy_research.evaluation.pipeline import evaluate
from app.strategy_research.evaluation.backtest import run_backtest
from app.strategy_research.providers import DataRequest
from app.strategy_research.providers import registry as provider_registry
from app.strategy_research.providers.base import CANONICAL_COLUMNS, DataSource
from app.strategy_research.providers.registry import register_provider
from app.strategy_research.strategies import registry as strategy_registry
from app.strategy_research.strategies.base import Strategy
from app.strategy_research.strategies.registry import register_strategy


def _seed(*parts) -> int:
    return int(hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:15], 16)


def _close(a: float, n: int, seed: int) -> np.ndarray:
    rng = np.random.Generator(np.random.PCG64(seed))
    e = rng.standard_normal(n)
    c = np.empty(n); c[0] = 100.0
    b = math.sqrt(1 - a * a)
    for t in range(1, n):
        s = np.sign(c[t - 1] / c[t - 61] - 1.0) if t >= 61 else 0.0
        c[t] = c[t - 1] * (1 + 0.01 * (a * s + b * e[t]))
    return c


class _PlantedSource(DataSource):
    name = "test-v5-planted"
    is_real = False

    def _fetch(self, req):
        _, a, seed = req.asset.split("|")
        c = _close(float(a), 2500, int(seed))
        t = 1_600_000_000_000 + np.arange(c.size, dtype=np.int64) * 86_400_000
        o = np.concatenate([[c[0]], c[:-1]])
        return pd.DataFrame({"open_time": t, "open": o, "high": np.maximum(o, c),
                             "low": np.minimum(o, c), "close": c,
                             "volume": np.full(c.size, 1e6)})[CANONICAL_COLUMNS]


class _TSMOM(Strategy):
    strategy_id = "TEST-V5-TSMOM"
    name = "test momentum"
    strategy_class = "calibration"
    direction = "long_short"
    default_params = {"lookback": 60}

    def positions(self, df, params=None):
        L = int((params or self.default_params)["lookback"])
        return np.sign(df["close"] / df["close"].shift(L) - 1.0).fillna(0.0).astype(float)

    def param_grid(self):
        return self._grid(lookback=[20, 40, 60, 120])


class _SwitchSource(DataSource):
    """Deterministic up-trend then steep down-trend: the in-sample winner switches."""
    name = "test-v5-switch"
    is_real = False

    def _fetch(self, req):
        rng = np.random.Generator(np.random.PCG64(11))
        drift = np.where(np.arange(2500) < 1500, 0.002, -0.01)
        c = 100 * np.cumprod(1 + drift + rng.normal(0, 0.005, 2500))
        t = 1_600_000_000_000 + np.arange(2500, dtype=np.int64) * 86_400_000
        o = np.concatenate([[c[0]], c[:-1]])
        return pd.DataFrame({"open_time": t, "open": o, "high": np.maximum(o, c),
                             "low": np.minimum(o, c), "close": c,
                             "volume": np.full(2500, 1e6)})[CANONICAL_COLUMNS]


class _Constant(Strategy):
    strategy_id = "TEST-V5-CONST"
    name = "test constant"
    strategy_class = "calibration"
    direction = "long_short"
    default_params = {"side": 1.0}

    def positions(self, df, params=None):
        return pd.Series(float((params or self.default_params)["side"]), index=df.index)

    def param_grid(self):
        return self._grid(side=[1.0, -1.0])


@contextlib.contextmanager
def _registered():
    """Register test-only strategies/providers and restore both registries afterwards,
    so registry-derived tests elsewhere never see them."""
    saved_s = dict(strategy_registry._REGISTRY)
    saved_p = dict(provider_registry._FACTORIES)
    try:
        register_provider("test-v5-planted", lambda **kw: _PlantedSource())
        register_provider("test-v5-switch", lambda **kw: _SwitchSource())
        register_strategy(_TSMOM())
        register_strategy(_Constant())
        yield
    finally:
        strategy_registry._REGISTRY.clear(); strategy_registry._REGISTRY.update(saved_s)
        provider_registry._FACTORIES.clear(); provider_registry._FACTORIES.update(saved_p)


def _req(a: float, tag: str, n: int = 30, matrix=MATRIX_V5):
    assets = [f"P|{a!r}|{_seed('test-v5', tag, i)}" for i in range(n)]
    return EvalRequest(strategy_id="TEST-V5-TSMOM", provider="test-v5-planted", assets=assets,
                       timeframe="1d", matrix_version=matrix.version)


def test_existing_matrix_hashes_and_active_default_unchanged():
    assert [m.hash() for m in (MATRIX_V1, MATRIX_V2, MATRIX_V3, MATRIX_V4)] == [
        "a740f3acbf6572d6", "4c2aa61eccfd276c", "3b838b31e151d7d1", "0ee7bc22dac18f76"]
    assert versioning.ACTIVE_VERSION == "srp-matrix-v4"
    assert versioning.resolve(None) is MATRIX_V4 and versioning.resolve("latest") is MATRIX_V4


def test_v5_is_registered_explicit_opt_in():
    assert versioning.resolve("v5") is MATRIX_V5
    assert MATRIX_V5.hash() == "a41f5eb49e7a1a4b"
    assert MATRIX_V5.pooling == "portfolio"
    assert MATRIX_V5.random_timing_method == "executed-runs-v1"
    assert not [v for v in versioning.list_versions() if v["version"] == "srp-matrix-v5"][0]["active"]


def test_v5_request_matrix_mismatch_rejected():
    with _registered(), pytest.raises(ValueError):
        evaluate(_req(0.0, "mismatch", 2, matrix=MATRIX_V4), MATRIX_V5)
    with _registered(), pytest.raises(ValueError):
        evaluate(_req(0.0, "mismatch", 2), MATRIX_V4)


def test_v5_only_daily_timeframe():
    req = EvalRequest(strategy_id="TEST-V5-TSMOM", provider="test-v5-planted",
                      assets=[f"P|0.0|{_seed('tf', 0)}"], timeframe="1h",
                      matrix_version=MATRIX_V5.version)
    with _registered(), pytest.raises(ValueError):
        evaluate(req, MATRIX_V5)


def test_v5_core_write_is_blocked_before_any_store_is_touched():
    class _NoKB:
        root = "/nonexistent"

        def record(self, rep):  # pragma: no cover - must never be reached
            raise AssertionError("KB must not be written for v5")
    with _registered(), pytest.raises(ValueError):
        Orchestrator(kb=_NoKB()).run("TEST-V5-TSMOM", ["P|0.0|1"], matrix_version="v5",
                                     provider="test-v5-planted", write=True)


def test_v5_is_deterministic_and_portfolio_shaped():
    with _registered():
        r1 = evaluate(_req(0.036, "det", 3), MATRIX_V5).to_dict()
        r2 = evaluate(_req(0.036, "det", 3), MATRIX_V5).to_dict()
    assert r1 == r2
    assert r1["headline"]["pooling"] == "portfolio"
    assert r1["headline"]["sharpe_uncertainty"]["status"] == "not_estimated"
    assert "sharpe_ci95" not in r1["headline"]
    assert {row["asset"] for row in r1["condition_rows"]} == {"PORTFOLIO"}
    assert len(r1["condition_rows"]) == 9 and r1["is_real"] is False


def test_v5_supports_planted_edge_and_rejects_no_edge():
    # a=0.0483 is the generator strength for true net annual Sharpe 0.75 per asset at 5+5 bps.
    with _registered():
        assert evaluate(_req(0.0483, "edge"), MATRIX_V5).verdict == "Supported"
        assert evaluate(_req(0.0, "null"), MATRIX_V5).verdict != "Supported"


def test_v5_registry_is_restored_after_use():
    before = set(strategy_registry._REGISTRY)
    with _registered():
        assert "TEST-V5-TSMOM" in strategy_registry._REGISTRY
    assert set(strategy_registry._REGISTRY) == before


def test_v5_fold_switch_is_executed_continuously_and_costed():
    """Independent recomputation: stitch the reported per-fold parameters, run ONE
    continuous backtest and compare with the reported headline return. The parameter
    switch (long -> short) must cost a 2-unit reversal, unlike splicing per-config nets."""
    req = EvalRequest(strategy_id="TEST-V5-CONST", provider="test-v5-switch", assets=["X"],
                      timeframe="1d", matrix_version=MATRIX_V5.version)
    with _registered():
        rep = evaluate(req, MATRIX_V5)
        df = _SwitchSource().get(DataRequest("X", "1d")).df
    folds = rep.headline["walk_forward"]["folds"]
    sides = [f["params"]["side"] for f in folds if f["params"]]
    assert 1.0 in sides and -1.0 in sides                      # a switch really happened
    dates = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    pos = np.zeros(len(df))
    oos = np.zeros(len(df), dtype=bool)
    for f in folds:
        m = ((dates >= f["start"]) & (dates <= f["end"])).to_numpy()
        oos |= m
        if f["params"]:
            pos[m] = f["params"]["side"]
    c, s = MATRIX_V5.headline_cost_bps, MATRIX_V5.headline_slippage_bps
    net = run_backtest(df, pd.Series(pos, index=df.index), "1d", c, s).net_returns[oos]
    assert rep.headline["total_return"] == pytest.approx(float(np.prod(1 + net) - 1), rel=1e-12)
    spliced = np.where(pos[oos] > 0,
                       run_backtest(df, pd.Series(1.0, index=df.index), "1d", c, s).net_returns[oos],
                       run_backtest(df, pd.Series(-1.0, index=df.index), "1d", c, s).net_returns[oos])
    assert float(np.prod(1 + net)) != pytest.approx(float(np.prod(1 + spliced)), rel=1e-9)
