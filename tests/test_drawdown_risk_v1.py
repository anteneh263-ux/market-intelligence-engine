"""Synthetic-only tests; no strategy selection, market runs or evidence writes."""
import json

import numpy as np
import pytest

from app.discovery.drawdown_risk_v1 import drawdown_report, render_report

PROVENANCE = {"strategy": "fixture", "matrix": "fixture-v1", "dataset": "synthetic",
              "costs": "fixture net returns, zero costs"}


def report(returns, **kwargs):
    options = dict(times=np.arange(len(returns)), block_lengths=[2],
                   provenance=PROVENANCE, simulations=40, seed=7)
    options.update(kwargs)
    return drawdown_report(returns, **options)


def test_whole_path_block_has_exact_drawdown_including_first_loss():
    r = report([-0.2, 0.25, -0.1], block_lengths=[3], drawdown_limit=0.15)
    assert r["observed_max_drawdown"] == pytest.approx(0.2)
    assert r["scenarios"][0]["p99_max_drawdown"] == pytest.approx(0.2)
    assert r["scenarios"][0]["exceedance_fraction"] == 1


@pytest.mark.parametrize("returns,expected", [([0, 0, 0], 0), ([0.1, 0.2, 0.1], 0),
                                             ([-0.1, -0.1, -0.1], 0.271),
                                             ([-1, 0.2, 0.1], 1)])
def test_known_equity_paths(returns, expected):
    r = report(returns, block_lengths=[3])
    assert r["observed_max_drawdown"] == pytest.approx(expected)
    assert r["scenarios"][0]["median_max_drawdown"] == pytest.approx(expected)


def test_synchronized_assets_preserve_hedge():
    x = np.array([0.1, -0.2, 0.03, -0.1, 0.02])
    r = report(np.column_stack([x, -x]), weights=[0.5, 0.5], horizon=20)
    assert r["observed_max_drawdown"] == 0
    assert r["scenarios"][0]["p99_max_drawdown"] == 0


def test_seeded_blocks_match_independent_reference():
    returns = np.array([0.12, -0.15, -0.1, 0.08, 0.04])
    rng = np.random.default_rng(np.random.SeedSequence([7, 2]))
    expected = []
    for _ in range(40):
        starts = rng.integers(0, 4, size=4)
        sample = np.concatenate([returns[s:s + 2] for s in starts])[:7]
        equity = np.r_[1.0, np.cumprod(1 + sample)]
        expected.append(np.max(1 - equity / np.maximum.accumulate(equity)))
    r = report(returns, horizon=7)
    for key, q in zip(("median_max_drawdown", "p95_max_drawdown", "p99_max_drawdown"), (.5, .95, .99)):
        assert r["scenarios"][0][key] == pytest.approx(np.quantile(expected, q))


def test_deterministic_json_and_no_global_rng_or_input_mutation():
    a = np.array([0.01, -0.03, 0.02, -0.01])
    before = a.copy()
    state = np.random.get_state()
    r = report(a, block_lengths=[1, 2, 4])
    assert render_report(r) == render_report(report(a, block_lengths=[1, 2, 4]))
    assert json.loads(render_report(r)) == r
    np.testing.assert_array_equal(before, a)
    after = np.random.get_state()
    assert state[0] == after[0] and state[2:] == after[2:]
    np.testing.assert_array_equal(state[1], after[1])
    assert r["status"] == "diagnostic_only"
    assert not any(k in r for k in ("verdict", "passed", "dsr"))
    assert r["input_sha256"] != report(a + .001)["input_sha256"]


@pytest.mark.parametrize("kwargs", [
    {"block_lengths": []}, {"block_lengths": [2, 2]}, {"block_lengths": [0]},
    {"block_lengths": [5]}, {"block_lengths": [1.5]}, {"block_lengths": [True]},
    {"times": [0, 1, 1, 3]}, {"times": [3, 2, 1, 0]}, {"times": [0, 1]},
    {"times": [0, 1, np.nan, 3]}, {"weights": [-1]}, {"weights": [0.5]},
    {"simulations": 0}, {"simulations": True}, {"seed": -1}, {"seed": 1.2},
    {"horizon": 1}, {"drawdown_limit": -0.1}, {"drawdown_limit": np.nan},
    {"provenance": {}},
])
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        report([0.1, -0.2, 0.1, 0.02], **kwargs)


@pytest.mark.parametrize("returns", [[], [.1], [.1, np.nan], [.1, np.inf], [.1, -1.01]])
def test_invalid_returns_rejected(returns):
    with pytest.raises(ValueError):
        report(returns)


def test_multiple_assets_require_explicit_weights():
    with pytest.raises(ValueError):
        report([[0.1, 0.2], [0.2, -0.1]])
