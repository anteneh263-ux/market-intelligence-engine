"""Synthetic regression tests; no corpus or prospective evidence writes."""
from dataclasses import replace
from itertools import combinations, product
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.strategy_research.evaluation import benchmarks


def frame(n):
    t = np.arange(n)
    return pd.DataFrame({"open_time": t, "close": 100 + .001*t + np.sin(t/7)})


@pytest.mark.parametrize("direction", ["long_short", "long_only"])
def test_every_seed_matches_engine_trade_count(monkeypatch, direction):
    actual = []
    engine = benchmarks.run_backtest
    def capture(*args, **kwargs):
        result = engine(*args, **kwargs)
        actual.append(result.metrics["num_trades"])
        return result
    monkeypatch.setattr(benchmarks, "run_backtest", capture)
    benchmarks.random_timing_trade_matched(frame(10000), "1d", 5, 5, 100, direction,
                             n_seeds=100, base_seed=12345)
    assert len(actual) == 100
    counts = actual
    assert counts == [100]*100, f"mean={np.mean(counts)}, min={min(counts)}, max={max(counts)}"


@pytest.mark.parametrize("direction,expected", [("long_short", 50.95), ("long_only", 25.47)])
def test_legacy_mismatch_is_reproducible(monkeypatch, direction, expected):
    counts = []
    engine = benchmarks.run_backtest
    def capture(*args, **kwargs):
        result = engine(*args, **kwargs)
        counts.append(result.metrics["num_trades"])
        return result
    monkeypatch.setattr(benchmarks, "run_backtest", capture)
    benchmarks.random_timing(frame(10000), "1d", 5, 5, 100, direction, 100, 12345)
    # The legacy implementation makes an extra backtest call for annualisation.
    assert len(counts) == 101
    assert np.mean(counts[:100]) == expected
    print(f"legacy {direction}: mean={np.mean(counts[:100])}, "
          f"min={min(counts[:100])}, max={max(counts[:100])}")


@pytest.mark.parametrize("direction", ["long_only", "long_short"])
@pytest.mark.parametrize("n", [1, 2, 3, 4, 11, 30])
def test_feasible_edges_and_zero_trades(direction, n):
    maximum = n - 1 if direction == "long_short" else n // 2
    for target in {0, maximum, min(1, maximum)}:
        r = benchmarks.random_timing_trade_matched(frame(n), "1d", 5, 5, target, direction, 4)
        audit = r["matching"]
        assert audit["num_trades"] == [target]*4
        assert audit["seeds"] == list(range(12345, 12349))
        assert audit["executable_slots"] == n-1
        assert audit["exposure_denominator"] == "executable_slots"
        assert audit["exposure_fraction"] == [x/(n-1) if n>1 else 0 for x in audit["exposed_bars"]]
        if target == 0:
            assert r["sharpe_p95"] == r["sharpe_std"] == r["total_return"] == 0
            assert r["sharpe_samples"] == [0]*4
            assert audit["total_turnover"] == audit["exposed_bars"] == [0]*4
        elif direction == "long_short":
            assert audit["total_turnover"] == [2*target-1]*4
            assert audit["exposed_bars"] == [n-1]*4
        else:
            assert set(audit["total_turnover"]) <= {2*target, 2*target-1}
    with pytest.raises(ValueError, match="exceeds"):
        benchmarks.random_timing_trade_matched(frame(n), "1d", 0, 0, maximum+1, direction)


@pytest.mark.parametrize("changes", [
    {"target_trades": -1}, {"target_trades": 1.5}, {"target_trades": True},
    {"target_trades": np.bool_(True)}, {"target_trades": "1"},
    {"n_seeds": 0}, {"n_seeds": -1}, {"n_seeds": 2.0}, {"n_seeds": True},
    {"base_seed": -1}, {"base_seed": 1.5}, {"base_seed": False},
    {"direction": "long_cash"}, {"direction": "short_only"},
])
def test_invalid_inputs_fail_closed(changes):
    args = dict(df=frame(20), timeframe="1d", cost_bps=5, slippage_bps=5,
                target_trades=2, direction="long_short", n_seeds=3, base_seed=7)
    args.update(changes)
    with pytest.raises(ValueError):
        benchmarks.random_timing_trade_matched(**args)


def test_empty_frame_refused():
    with pytest.raises(ValueError, match="at least one bar"):
        benchmarks.random_timing_trade_matched(frame(0), "1d", 0, 0, 0, "long_only")


def test_numpy_integers_accepted_and_repeatable():
    args = (frame(30), "1d", 5, 5, np.int64(4), "long_only", np.int64(5), np.int64(7))
    assert benchmarks.random_timing_trade_matched(*args) == benchmarks.random_timing_trade_matched(*args)


@pytest.mark.parametrize("direction", ["long_only", "long_short"])
def test_small_path_family_is_bijective(direction):
    # Exhaust every RNG choice for short paths and compare against ALL possible
    # state sequences filtered by the actual engine's trade definition.
    for m in range(1, 6):
        maximum = m if direction == "long_short" else (m+1)//2
        states = [-1., 1.] if direction == "long_short" else [0., 1.]
        df = frame(m+1)
        for target in range(1, maximum+1):
            expected = set()
            for path in product(states, repeat=m):
                result = benchmarks.run_backtest(df, pd.Series([*path, path[-1]]), "1d", 0, 0)
                if result.metrics["num_trades"] == target:
                    expected.add(path)
            actual = []
            signs = [-1., 1.] if direction == "long_short" else [None]
            pool = range(m-1) if direction == "long_short" else range(m+1)
            size = target-1 if direction == "long_short" else 2*target
            for sign in signs:
                for cuts in combinations(pool, size):
                    class Choices:
                        def choice(self, options, size=None, replace=None):
                            return sign if size is None else np.array(cuts, dtype=int)
                    pos = benchmarks._trade_matched_positions(m+1, target, direction, Choices())
                    actual.append(tuple(pos[:-1]))
                    assert pos[-1] == pos[-2]
            assert len(actual) == len(set(actual))
            assert set(actual) == expected


@pytest.mark.parametrize("direction,expected,turnover", [
    ("long_short", [1, 1, 1, 1, -1, 1, 1, 1], 5),
    ("long_only", [0, 0, 1, 0, 1, 0, 1, 1], 5),
])
def test_fixed_small_example_and_costs(direction, expected, turnover):
    pos = benchmarks._trade_matched_positions(8, 3, direction, np.random.Generator(np.random.PCG64(7)))
    np.testing.assert_array_equal(pos, expected)
    df = frame(8); df["close"] = 100.
    result = benchmarks.run_backtest(df, pd.Series(pos), "1d", 5, 5)
    assert result.metrics["num_trades"] == 3
    assert result.metrics["total_turnover"] == turnover
    assert sum(result.net_returns) == pytest.approx(-turnover*.001)
    # The last signal never executes. Changing it cannot add a trade/cost.
    pos[-1] = -1 if pos[-1] >= 0 else 1
    changed = benchmarks.run_backtest(df, pd.Series(pos), "1d", 5, 5)
    np.testing.assert_array_equal(changed.net_returns, result.net_returns)
    assert changed.metrics["num_trades"] == 3


def test_paths_do_not_depend_on_prices_and_keep_index(monkeypatch):
    seen = []
    engine = benchmarks.run_backtest
    def capture(df, pos, *args):
        pd.testing.assert_index_equal(df.index, pos.index)
        seen.append(pos.to_numpy().copy())
        return engine(df, pos, *args)
    monkeypatch.setattr(benchmarks, "run_backtest", capture)
    df = frame(40); df.index = pd.date_range("2020-01-01", periods=40)
    benchmarks.random_timing_trade_matched(df, "1d", 5, 5, 5, "long_only", 3, 7)
    df["close"] = np.arange(40)+100
    benchmarks.random_timing_trade_matched(df, "1d", 5, 5, 5, "long_only", 3, 7)
    for a, b in zip(seen[:3], seen[3:]):
        np.testing.assert_array_equal(a, b)
    assert any(not np.array_equal(seen[0], p) for p in seen[1:3])


def test_engine_disagreement_stops_report(monkeypatch):
    engine = benchmarks.run_backtest
    def wrong_count(*args, **kwargs):
        result = engine(*args, **kwargs)
        result.metrics["num_trades"] += 1
        return result
    monkeypatch.setattr(benchmarks, "run_backtest", wrong_count)
    with pytest.raises(RuntimeError, match="disagrees"):
        benchmarks.random_timing_trade_matched(frame(30), "1d", 0, 0, 3, "long_only")


from app.strategy_research import MATRICES
from app.strategy_research.evaluation import pipeline
from app.strategy_research.evaluation.frozen import DEFAULT_MATRIX, MATRIX_V1, MATRIX_V2, MATRIX_V3, EvalRequest
from app.strategy_research.strategies.base import Strategy
from app.research_core import versioning, Orchestrator
from app.knowledge_base import KnowledgeBase
from app.strategy_research import dossier


class Fixture(Strategy):
    strategy_id = "TEST-BENCHMARK"
    name = "Synthetic trade-matching fixture"
    strategy_class = "test"

    def positions(self, df, params=None):
        active = (np.arange(len(df))//11)%2
        if self.direction == "flat":
            return pd.Series(0., index=df.index)
        other = -1. if self.direction == "long_short" else 0.
        return pd.Series(np.where(active, other, 1.), index=df.index)


def small(matrix):
    return replace(matrix, cost_bps_levels=(5.,), slippage_bps_levels=(5.,),
                   random_seeds=3, bootstrap_B=50)


def request(matrix):
    return EvalRequest("TEST-BENCHMARK", "synthetic", ["TEST-A", "TEST-B"], "1d",
                       limit=260, matrix_version=matrix.version)


def test_legacy_hashes_defaults_and_version_resolution():
    assert MATRIX_V1.hash() == "a740f3acbf6572d6"
    assert MATRIX_V2.hash() == "4c2aa61eccfd276c"
    assert MATRIX_V3.hash() not in (MATRIX_V1.hash(), MATRIX_V2.hash())
    assert MATRIX_V3.pooling == MATRIX_V2.pooling == "per_asset"
    assert DEFAULT_MATRIX is MATRIX_V1
    assert versioning.resolve("v2") is versioning.resolve("srp-matrix-v2") is MATRIX_V2
    assert versioning.resolve("v3") is versioning.resolve("srp-matrix-v3") is MATRIX_V3
    assert MATRICES["v3"] is MATRICES["srp-matrix-v3"] is MATRIX_V3
    assert MATRIX_V1.random_timing_method == MATRIX_V2.random_timing_method == "bernoulli-redraw-v1"


def test_legacy_reports_match_prechange_fixture(monkeypatch):
    from app.strategy_research.providers import synthetic_source
    # The legacy provider uses Python's process-salted hash. Fix its seed hook
    # in this test only so the stored prechange fixture is portable.
    monkeypatch.setattr(synthetic_source, "hash",
                        lambda key: {"TEST-A": 101, "TEST-B": 202}[key[0]], raising=False)
    monkeypatch.setattr(pipeline, "get_strategy", lambda _: Fixture())
    baseline = json.loads((Path(__file__).parent / "fixtures/random_timing_legacy_reports.json").read_text())
    for matrix in (MATRIX_V1, MATRIX_V2):
        got = pipeline.evaluate(request(matrix), small(matrix)).to_dict()
        # Exact on this runtime; tolerant comparison only for numeric rounding
        # across supported numpy versions, never for keys/labels or integer counts.
        def compare(actual, expected):
            if isinstance(expected, dict):
                assert actual.keys() == expected.keys()
                for key in expected:
                    compare(actual[key], expected[key])
            elif isinstance(expected, list):
                assert len(actual) == len(expected)
                for a, b in zip(actual, expected):
                    compare(a, b)
            elif isinstance(expected, float):
                assert actual == pytest.approx(expected, rel=1e-12, abs=1e-14)
            else:
                assert actual == expected
        compare(got, baseline[matrix.version])


@pytest.mark.parametrize("direction", ["long_short", "long_only", "flat"])
def test_actual_pipeline_uses_exact_matching_and_zero_target(monkeypatch, direction):
    strategy = Fixture()
    if direction == "flat":
        strategy.direction = "long_only"
        strategy.positions = lambda df, params=None: pd.Series(0., index=df.index)
    else:
        strategy.direction = direction
    monkeypatch.setattr(pipeline, "get_strategy", lambda _: strategy)
    def forbidden(*args, **kwargs):
        raise AssertionError("v3 used legacy benchmark")
    monkeypatch.setattr(pipeline, "random_timing", forbidden)
    rep = pipeline.evaluate(request(MATRIX_V3), small(MATRIX_V3))
    assert rep.matrix_version == "srp-matrix-v3"
    assert rep.headline["pooling"] == "per_asset"
    audit = rep.headline["random_timing"]
    assert audit["method"] == "executed-runs-v1"
    for item in audit["per_asset"]:
        row = next(row for row in rep.condition_rows if row["asset"] == item["asset"])
        assert item["target_trades"] == row["num_trades"]
        assert item["num_trades"] == [row["num_trades"]]*3
        if direction == "flat":
            assert item["target_trades"] == 0
            assert rep.headline["random_timing_sharpe_p95"] == 0
    assert sum(item["target_trades"] for item in audit["per_asset"]) == rep.headline["num_trades"]
    assert rep.is_real is False


@pytest.mark.parametrize("request_matrix,selected_matrix", [
    (MATRIX_V2, MATRIX_V3), (MATRIX_V3, MATRIX_V1), (MATRIX_V3, MATRIX_V2),
])
def test_request_version_mismatch_refused_before_fetch(monkeypatch, request_matrix, selected_matrix):
    def forbidden(*args):
        raise AssertionError("must refuse before loading strategy or data")
    monkeypatch.setattr(pipeline, "get_strategy", forbidden)
    with pytest.raises(ValueError, match="request.matrix_version"):
        pipeline.evaluate(request(request_matrix), selected_matrix)


def test_orchestrator_to_separate_versioned_results(tmp_path, monkeypatch):
    # The test owns this temporary KB. No source checkout or H-025 artifacts.
    monkeypatch.setattr(pipeline, "get_strategy", lambda _: Fixture())
    kb = tmp_path / "kb"; kb.mkdir()
    rdir = kb / "results"; rdir.mkdir()
    prior = {}
    for ver in ("v1", "v2"):
        path = rdir / f"TEST-BENCHMARK.srp-matrix-{ver}.json"
        prior[path] = f'{{"preserved": "{ver}"}}'.encode()
        path.write_bytes(prior[path])
    doc = kb / "TEST-BENCHMARK.md"
    old_sections = ("# Synthetic fixture\n## STEP 5 — Evaluation\nOriginal v1 text\n"
                    "## STEP 5 · srp-matrix-v2 — Scientific Evaluation\nOriginal v2 text\n")
    doc.write_text(old_sections + "## STEP 6 — Conclusion\n")
    outcome = Orchestrator(KnowledgeBase(root=str(kb))).run(
        "TEST-BENCHMARK", ["TEST-A", "TEST-B"], matrix_version="v3",
        provider="synthetic", limit=260, write=True)
    assert outcome.recorded["results_written"]
    assert outcome.recorded["dossier_written"]
    assert outcome.matrix_hash == MATRIX_V3.hash()
    for path, content in prior.items():
        assert path.read_bytes() == content
    assert doc.read_text().startswith(old_sections)
    assert "srp-matrix-v3" in doc.read_text()
    assert "executed-runs-v1" in doc.read_text()
    versioned = json.loads((rdir / "TEST-BENCHMARK.srp-matrix-v3.json").read_text())
    latest = json.loads((rdir / "TEST-BENCHMARK.json").read_text())
    assert latest == versioned
    assert versioned["random_timing"] == outcome.report["headline"]["random_timing"]
    assert versioned["is_real"] is False


def test_v3_index_label_and_cli_dispatch(tmp_path, monkeypatch, capsys):
    from app.strategy_research.run import main
    monkeypatch.setattr(pipeline, "get_strategy", lambda _: Fixture())
    rep = pipeline.evaluate(request(MATRIX_V3), small(MATRIX_V3))
    index = tmp_path / "INDEX.md"
    index.write_text("| TEST-BENCHMARK | Fixture | test | Pending | Untested | 0 | 0 | link |\n")
    dossier.update_index(str(index), replace(rep, is_real=True))
    assert "(v3)" in index.read_text() and "(v1)" not in index.read_text()
    assert main(["--strategy", "TEST-BENCHMARK", "--provider", "synthetic", "--assets", "TEST-A",
                 "--limit", "260", "--matrix", "v3", "--json"]) == 0
    output = capsys.readouterr().out
    assert "executed-runs-v1" in output and "srp-matrix-v3" in output
    with pytest.raises(SystemExit) as err:
        main(["--matrix", "v999"])
    assert err.value.code == 2


def test_v3_request_cannot_silently_use_default_v1(monkeypatch):
    def forbidden(*args):
        raise AssertionError("must refuse before loading strategy or data")
    monkeypatch.setattr(pipeline, "get_strategy", forbidden)
    with pytest.raises(ValueError, match="request.matrix_version"):
        pipeline.evaluate(request(MATRIX_V3))
