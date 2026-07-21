"""Tests for the Strategy Research module: providers, strategies, statistics,
no-look-ahead, determinism, the frozen pipeline, and the data-integrity guard."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

try:
    import pytest
except ModuleNotFoundError:  # PyPI blocked — provide a no-op shim for parametrize
    class _Mark:
        @staticmethod
        def parametrize(*_a, **_k):
            def deco(fn):
                return fn
            return deco

    class _Pytest:
        mark = _Mark()

    pytest = _Pytest()  # type: ignore

from app.strategy_research import (
    EvalRequest,
    available_providers,
    available_strategies,
    evaluate,
    get_provider,
    get_strategy,
)
from app.strategy_research.evaluation.backtest import run_backtest
from app.strategy_research.evaluation.frozen import DEFAULT_MATRIX
from app.strategy_research.evaluation.stats import (
    benjamini_hochberg,
    deflated_sharpe,
    probabilistic_sharpe,
    sharpe_per_period,
)
from app.strategy_research.providers import DataRequest


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def test_registry_lists():
    ids = set(available_strategies())
    assert {"H-002", "H-005", "H-010", "H-020"} <= ids       # the four reference strategies
    # Count only the permanent H-strategies. Generated G-#### strategies may be
    # registered by earlier tests (discovery/workflow) in the same process and
    # must not affect this count.
    h_ids = [s for s in available_strategies() if s.startswith("H-")]
    assert len(h_ids) == 26                                   # permanent H-strategy universe
    assert {"csv", "binance", "fmp", "coindesk", "synthetic"} <= set(available_providers())


def test_synthetic_is_not_real_and_deterministic():
    src = get_provider("synthetic")
    assert src.is_real is False
    a = src.get(DataRequest("BTC/USDT", "1d", limit=500)).df
    b = src.get(DataRequest("BTC/USDT", "1d", limit=500)).df
    pd.testing.assert_frame_equal(a, b)  # no wall-clock, no RNG leak


def test_ohlcv_frame_normalises_schema():
    src = get_provider("synthetic")
    f = src.get(DataRequest("ETH/USDT", "1d", limit=300))
    assert list(f.df.columns) == ["open_time", "open", "high", "low", "close", "volume"]
    assert f.df["open_time"].is_monotonic_increasing


def test_csv_provider_reads_real(tmp_path):
    p = tmp_path / "BTCUSDT_1d.csv"
    rows = pd.DataFrame({
        "timestamp": [1_600_000_000_000 + i * 86_400_000 for i in range(10)],
        "open": np.arange(10) + 100.0, "high": np.arange(10) + 101.0,
        "low": np.arange(10) + 99.0, "close": np.arange(10) + 100.5,
        "volume": np.ones(10),
    })
    rows.to_csv(p, index=False)
    src = get_provider("csv", root=str(tmp_path))
    f = src.get(DataRequest("BTC/USDT", "1d"))
    assert f.is_real is True and f.n == 10


# --------------------------------------------------------------------------- #
# Strategies + no look-ahead
# --------------------------------------------------------------------------- #
def _synth_df(n=600):
    return get_provider("synthetic").get(DataRequest("BTC/USDT", "1d", limit=n)).df


@pytest.mark.parametrize("sid", ["H-002", "H-005", "H-010", "H-020"])
def test_positions_in_range(sid):
    df = _synth_df()
    pos = get_strategy(sid).positions(df)
    assert set(np.unique(pos.dropna())) <= {-1.0, 0.0, 1.0}
    assert len(pos) == len(df)


@pytest.mark.parametrize("sid", ["H-002", "H-005", "H-010", "H-020"])
def test_no_lookahead_positions_depend_only_on_past(sid):
    """Truncating the future must not change past positions."""
    df = _synth_df(600)
    strat = get_strategy(sid)
    full = strat.positions(df).to_numpy()
    trunc = strat.positions(df.iloc[:400].reset_index(drop=True)).to_numpy()
    # First 400 positions identical whether or not future bars exist.
    np.testing.assert_array_equal(np.nan_to_num(full[:400]), np.nan_to_num(trunc[:400]))


def test_backtest_shifts_positions_no_lookahead():
    df = _synth_df(300)
    # A position perfectly correlated with the CURRENT bar return must NOT be
    # profitable, because the engine shifts positions forward one bar.
    ret = df["close"].pct_change().fillna(0.0)
    cheating = pd.Series(np.sign(ret.to_numpy()), index=df.index)
    r = run_backtest(df, cheating, "1d", 0.0, 0.0)
    assert r.metrics["sharpe_per_period"] < 0.5  # no free lunch from same-bar peeking


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def test_deflated_sharpe_penalises_more_trials():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.001, 0.01, 500)
    d1 = deflated_sharpe(rets, [], 1)["dsr"]
    d50 = deflated_sharpe(rets, [], 50)["dsr"]
    assert d50 <= d1  # more trials => harder to be "real"


def test_probabilistic_sharpe_monotone():
    assert probabilistic_sharpe(0.2, 0.0, 500, 0, 3) > probabilistic_sharpe(0.05, 0.0, 500, 0, 3)


def test_benjamini_hochberg_matches_known_case():
    rej, adj = benjamini_hochberg([0.001, 0.2, 0.3, 0.4], q=0.05)
    assert rej[0] and not rej[1]


def test_sharpe_zero_on_constant():
    assert sharpe_per_period([0.01] * 50) == 0.0


# --------------------------------------------------------------------------- #
# Frozen pipeline + determinism + data integrity
# --------------------------------------------------------------------------- #
def test_pipeline_runs_and_is_deterministic():
    req = EvalRequest("H-010", "synthetic", ["BTC/USDT", "ETH/USDT"], "1d", limit=800)
    r1 = evaluate(req)
    r2 = evaluate(req)
    j1 = json.dumps(r1.condition_rows, sort_keys=True)
    j2 = json.dumps(r2.condition_rows, sort_keys=True)
    assert j1 == j2                       # byte-identical replay
    assert r1.headline["dsr"] == r2.headline["dsr"]
    assert r1.matrix_hash == DEFAULT_MATRIX.hash()


def test_synthetic_flagged_not_evidence():
    req = EvalRequest("H-020", "synthetic", ["BTC/USDT"], "1d", limit=800)
    rep = evaluate(req)
    assert rep.is_real is False
    assert any("ILLUSTRATIVE" in n for n in rep.notes)


def test_verdict_is_one_of_the_four():
    req = EvalRequest("H-005", "synthetic", ["BTC/USDT", "ETH/USDT"], "1d", limit=800)
    rep = evaluate(req)
    assert rep.verdict in {"Supported", "Not Supported", "Inconclusive", "Graveyard"}


def test_dossier_autoupdate_writes_step5_and_index(tmp_path):
    """Mechanism test for the auto-updater. Uses a CSV fixture routed through the
    real (is_real=True) CSV path and writes to a TEMP knowledge base — never the
    real dossiers. Asserts STEP 5 body is replaced and the INDEX row is updated."""
    from app.strategy_research.dossier import apply_report

    # A CSV fixture (numbers are arbitrary; this test checks plumbing, not a verdict).
    n = 500
    t0 = 1_600_000_000_000
    price = 100 + np.cumsum(np.sin(np.arange(n) / 5.0))
    pd.DataFrame({
        "timestamp": [t0 + i * 86_400_000 for i in range(n)],
        "open": price, "high": price + 1, "low": price - 1, "close": price,
        "volume": np.ones(n),
    }).to_csv(tmp_path / "BTCUSDT_1d.csv", index=False)

    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "H-010_macd_strategy.md").write_text(
        "# H-010\n\n## STEP 5 — Scientific Evaluation\n\nPending real data.\n\n## STEP 6 — x\n")
    (kb / "INDEX.md").write_text(
        "| id | name | class | status | verdict | robustness | generalization | link |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| **H-010** | MACD | trend | Registered; STEP 1 pending | Not Yet Tested "
        "| pending | pending | — |\n")

    req = EvalRequest("H-010", "csv", ["BTC/USDT"], "1d", limit=n)
    rep = evaluate(req, provider_kwargs={"root": str(tmp_path)})
    assert rep.is_real is True
    # Seed a meta table row so the meta-updater has something to refresh.
    (kb / "INDEX.md").write_text(
        (kb / "INDEX.md").read_text()
        + "\n| **M-001** | Lagging trend | H-001, H-010 | **Open — 0 tested** |\n")

    res = apply_report(rep, kb_root=str(kb))
    assert res["dossier_written"] and res["index_written"] and res["meta_written"]

    dossier = (kb / "H-010_macd_strategy.md").read_text()
    assert "Mechanical verdict" in dossier and "Pending real data." not in dossier
    index = (kb / "INDEX.md").read_text()
    assert rep.verdict in index and "STEP 1 pending" not in index
    # Meta ledger written and INDEX meta row refreshed with a tally.
    import json as _json
    ledger = _json.load(open(kb / "meta_ledger.json"))
    assert ledger["H-010"]["meta"] == "M-001"
    assert "tested —" in index  # meta status cell was recomputed


def test_every_condition_row_has_same_schema():
    req = EvalRequest("H-002", "synthetic", ["BTC/USDT"], "1d", limit=800)
    rep = evaluate(req)
    keys = {"asset", "cost_bps", "slippage_bps", "oos_sharpe", "dsr", "grid_size",
            "max_drawdown", "num_trades", "n_bars"}
    for row in rep.condition_rows:
        assert keys <= set(row)
    # frozen ladder = 3 cost levels x 3 slippage levels
    assert len(rep.condition_rows) == 9


def test_h025_is_long_only_and_causal():
    """H-025 (Asset Class Trend Following) must be long-only (positions in {0,1}) and
    look-ahead free (truncating the future doesn't change past positions)."""
    df = _synth_df(600)
    s = get_strategy("H-025")
    assert s.direction == "long_only"
    full = s.positions(df).to_numpy()
    assert set(np.unique(full)) <= {0.0, 1.0}                 # never shorts
    trunc = s.positions(df.iloc[:400].reset_index(drop=True)).to_numpy()
    np.testing.assert_array_equal(full[:400], trunc[:400])


def test_h026_monthly_long_only_causal_low_turnover():
    """H-026 (monthly Faber): long-only {0,1}, look-ahead free (truncation-invariant,
    since it uses only completed months), and MUCH lower turnover than a daily rule
    (position changes at most about once per month)."""
    df = _synth_df(600)                                        # ~20 months of daily bars
    s = get_strategy("H-026")
    assert s.direction == "long_only"
    full = s.positions(df).to_numpy()
    assert set(np.unique(full)) <= {0.0, 1.0}
    trunc = s.positions(df.iloc[:400].reset_index(drop=True)).to_numpy()
    np.testing.assert_array_equal(full[:400], trunc[:400])    # causal: no look-ahead
    changes = int(np.sum(np.abs(np.diff(full)) > 0))
    assert changes <= 24                                       # ~monthly cadence over ~20 months


def test_h027_connors_rsi2_long_short_and_causal():
    """H-027 (Connors RSI-2): positions in {-1,0,1} and look-ahead free (truncating the
    future does not change past positions). Built by a causal state machine."""
    df = _synth_df(800)
    s = get_strategy("H-027")
    assert s.direction == "long_short"
    full = s.positions(df).to_numpy()
    assert set(np.unique(full)) <= {-1.0, 0.0, 1.0}
    trunc = s.positions(df.iloc[:500].reset_index(drop=True)).to_numpy()
    np.testing.assert_array_equal(full[:500], trunc[:500])
