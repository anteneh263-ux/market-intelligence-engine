import json

import pandas as pd
import pytest

from app.discovery.experiment import run, validate


def contract(tmp_path):
    times = pd.date_range("2020-01-01", periods=12, tz="UTC")
    pd.DataFrame({"timestamp": [int(t.timestamp()) for t in times],
                  "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1}).to_csv(
                      tmp_path / "BTCUSD_1d.csv", index=False)
    return {"assets": ["BTC/USD"], "csv_root": str(tmp_path), "timeframe": "1d",
            "development": ["2020-01-01", "2020-01-06"],
            "holdout": ["2020-01-07", "2020-01-12"], "min_bars": 6,
            "matrix_version": "v2", "rounds": 2, "per_round": 2, "max_vote": 2,
            "control_seeds": [7, 19], "holdout_provenance_confirmed": True}


def test_isolated_equal_budget_holdout_never_feeds_selection(tmp_path):
    c = contract(tmp_path)
    calls = []

    def evaluate(recipe, window):
        calls.append(window)
        return {"is_real": True, "verdict": "Inconclusive" if window == "development" else "Supported",
                "headline": {"dsr": 0.99}}

    summary = run(c, tmp_path / "output", evaluate)
    assert calls == ["development"] * 12 + ["holdout"] * 12
    for arm in summary.values():
        assert arm["holdout"]["attempts"] == 4
        assert arm["holdout"]["supported_fraction"] == 1
        assert arm["development"]["attempts_to_first_supported"] is None
    events = [json.loads(line) for line in (tmp_path / "output/events.jsonl").read_text().splitlines()]
    selections = [e for e in events if e["event"] == "selection"]
    assert len(selections) == 12
    assert all(e["rationale"]["corpus_n"] == 0 for e in selections if e["arm"] == "v2")
    lock = json.loads((tmp_path / "output/lock.json").read_text())
    assert lock["datasets"]["holdout"]["BTC/USD"]["rows"] == 6
    with pytest.raises(FileExistsError):
        run(c, tmp_path / "output", evaluate)


def test_unconfirmed_or_overlapping_holdout_rejected(tmp_path):
    c = contract(tmp_path)
    c["holdout_provenance_confirmed"] = False
    with pytest.raises(ValueError, match="provenance"):
        validate(c)
    c["holdout_provenance_confirmed"] = True
    c["holdout"][0] = c["development"][1]
    with pytest.raises(ValueError, match="nonoverlapping"):
        validate(c)


def test_error_aborts_without_success_summary(tmp_path):
    c = contract(tmp_path)
    def evaluate(recipe, window):
        raise RuntimeError("fixture failure")
    with pytest.raises(RuntimeError, match="fixture failure"):
        run(c, tmp_path / "output", evaluate)
    assert not (tmp_path / "output/summary.json").exists()
    assert '"event": "error"' in (tmp_path / "output/events.jsonl").read_text()
