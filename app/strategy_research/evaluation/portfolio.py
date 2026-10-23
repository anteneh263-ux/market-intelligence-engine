"""srp-matrix-v5 — portfolio-level evaluation (explicit opt-in; v1–v4 unchanged).

Why: matrices v2–v4 require every asset to clear the deflated-Sharpe bar on its
own and average per-asset statistics, so evidence across assets is never pooled.
A calibration with planted edges (2026-10-23) found that a strategy with true net
annual Sharpe 0.5–1.0 was "Supported" in 0–10% of runs, and adding assets lowered
power. v5 evaluates ONE equal-weight portfolio of the strategy across all assets.

Method (everything not listed is identical to v4: cost/slippage ladder, thresholds,
walk-forward split rule, trade-matched random-timing method, mechanical verdict):

* Calendar: per-asset net returns from the unchanged backtest are placed on the
  UTC-date union calendar of all assets; a day without a bar for an asset counts
  as a 0 return for that asset. Portfolio return = mean over assets whose data
  has started and not ended on that date (equal weight, daily rebalanced).
* Parameters: ONE grid configuration is shared by all assets and selected per
  walk-forward fold on in-sample PORTFOLIO Sharpe. The data-snooping budget is
  therefore the grid size, exactly as in v1–v4.
* Execution: the selected positions are stitched per asset (flat before the first
  out-of-sample fold) and backtested ONCE, continuously, so a parameter switch at a
  fold boundary is executed and costed like any other position change. (v1–v4 splice
  per-configuration net returns at fold boundaries; that is unchanged for them.)
* Deflated Sharpe, drawdown gate, robustness (share of the 9 cost/slippage
  conditions with positive OOS Sharpe and DSR >= threshold) are computed on the
  portfolio OOS series.
* Buy-and-hold: equal-weight long portfolio of the same assets on the same OOS dates.
* Random timing: per seed, every asset gets an independent trade-matched random
  path (same trade count as the strategy on that asset); the 95th percentile of
  the resulting portfolio Sharpe across seeds is the bar.
* Only timeframe "1d" is supported.

Limits: equal weight is not risk parity; Sharpe annualisation keeps MIE's
365-periods convention on the union calendar; sampling uncertainty is still not
estimated. A "Supported" verdict is evidence about a historical backtest, never
proof of profitability or prospective validity.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..providers import DataRequest, get_provider
from ..strategies import get_strategy
from .backtest import PERIODS_PER_YEAR, _max_drawdown, run_backtest
from .benchmarks import TRADE_MATCHED_METHOD, _trade_matched_positions
from .frozen import EvalRequest, FrozenMatrix
from .robustness import confidence_score, generalization_score, robustness_score, stability_score
from .stats import deflated_sharpe, sharpe_per_period
from .verdict import decide

PORTFOLIO_VERSION = "srp-matrix-v5"


def _dates(df: pd.DataFrame) -> np.ndarray:
    return pd.to_datetime(df["open_time"].to_numpy(), unit="ms", utc=True).floor("D").to_numpy()


def _on_calendar(values: np.ndarray, dates: np.ndarray, cal_pos: Dict, T: int) -> np.ndarray:
    out = np.zeros(T)
    idx = np.fromiter((cal_pos[d] for d in dates), dtype=int, count=len(dates))
    out[idx] = values
    return out


def _entries(pos: np.ndarray) -> int:
    """Executed entries into a non-zero position (same rule as pipeline._count_trades)."""
    if pos.size == 0:
        return 0
    exec_pos = np.concatenate([[0.0], pos[:-1]])
    prev = np.concatenate([[0.0], exec_pos[:-1]])
    return int(np.sum((exec_pos != prev) & (exec_pos != 0.0)))


def _metrics(net: np.ndarray, tf: str) -> Dict:
    ppy = PERIODS_PER_YEAR.get(tf, 365.0)
    eq = np.cumprod(1.0 + net) if net.size else np.array([1.0])
    sr = sharpe_per_period(net)
    return {"oos_sharpe": sr, "oos_sharpe_annual": sr * np.sqrt(ppy),
            "total_return": float(eq[-1] - 1.0), "max_drawdown": _max_drawdown(eq),
            "n_bars": int(net.size)}


def evaluate_portfolio(request: EvalRequest, matrix: FrozenMatrix,
                       provider_kwargs: Dict | None = None):
    from .pipeline import EvaluationReport  # local import avoids a cycle

    if matrix.version != PORTFOLIO_VERSION or request.matrix_version != PORTFOLIO_VERSION:
        raise ValueError("evaluate_portfolio requires request and matrix srp-matrix-v5")
    tf = request.timeframe
    if tf != "1d":
        raise ValueError("srp-matrix-v5 supports timeframe '1d' only")
    strategy = get_strategy(request.strategy_id)
    source = get_provider(request.provider, **(provider_kwargs or {}))
    grid = strategy.param_grid()
    notes: List[str] = []

    frames, assets = [], []
    for asset in request.assets:
        frame = source.get(DataRequest(asset, tf, request.start, request.end, request.limit))
        if frame.n < max(matrix.min_train + 50, 200):
            notes.append(f"{asset}: only {frame.n} bars from '{request.provider}' — skipped "
                         f"(need >= {max(matrix.min_train + 50, 200)}).")
            continue
        frames.append(frame.df)
        assets.append(asset)

    def _empty_report(reason: str):
        return EvaluationReport(
            strategy_id=strategy.strategy_id, strategy_name=strategy.name,
            strategy_class=strategy.strategy_class, provider=request.provider,
            is_real=source.is_real, request_hash=request.hash(), matrix_hash=matrix.hash(),
            matrix_version=matrix.version, assets_evaluated=[], verdict="Inconclusive",
            verdict_reasons=[reason], notes=notes)

    if not frames:
        return _empty_report("no asset had enough real data")

    asset_dates = [_dates(df) for df in frames]
    cal = np.array(sorted(set(np.concatenate(asset_dates))))
    T = cal.size
    cal_pos = {d: i for i, d in enumerate(cal)}
    alive = np.zeros((len(frames), T), dtype=bool)
    for a, d in enumerate(asset_dates):
        alive[a, cal_pos[d[0]]:cal_pos[d[-1]] + 1] = True
    n_alive = np.maximum(alive.sum(axis=0), 1)

    # Positions per grid config and asset are cost-independent: compute once.
    positions = [[strategy.positions(df, p).reindex(df.index).fillna(0.0) for df in frames]
                 for p in grid]

    def _portfolio(rows: List[np.ndarray]) -> np.ndarray:
        return np.vstack(rows).sum(axis=0) / n_alive

    def _walk(cost: float, slip: float) -> Dict:
        asset_net = [[_on_calendar(run_backtest(df, positions[j][a], tf, cost, slip).net_returns,
                                   asset_dates[a], cal_pos, T)
                      for a, df in enumerate(frames)] for j in range(len(grid))]
        port = [_portfolio(rows) for rows in asset_net]
        edges = np.linspace(0, T, matrix.n_splits + 1, dtype=int)
        oos_idx, chosen, is_sr = [], [], []
        for k in range(1, matrix.n_splits + 1):          # same split rule as walkforward.py
            train_end = edges[k]
            start, stop = edges[k], (edges[k + 1] if k + 1 < len(edges) else T)
            if stop <= start:
                continue
            idx = np.arange(start, stop)
            if train_end < matrix.min_train:
                oos_idx.append(idx); chosen.append(None)
                continue
            srs = [sharpe_per_period(p[:train_end]) for p in port]
            j = int(np.argmax(srs))
            is_sr.append(srs[j]); oos_idx.append(idx); chosen.append(j)
        all_idx = np.concatenate(oos_idx) if oos_idx else np.array([], dtype=int)
        # Continuous execution: stitch the SELECTED positions per asset (flat before the
        # first OOS fold and in folds without a selection) and run ONE backtest per asset,
        # so every parameter switch at a fold boundary is executed and costed like any
        # other position change. (v1–v4 stitch per-config net returns instead; unchanged.)
        fold_of = np.full(T, -1, dtype=int)
        for f, (i, j) in enumerate(zip(oos_idx, chosen)):
            fold_of[i] = j if j is not None else -1
        stitched_pos, asset_cal_net = [], []
        for a, df in enumerate(frames):
            sel = fold_of[np.fromiter((cal_pos[d] for d in asset_dates[a]), dtype=int,
                                      count=len(df))]
            pos = np.zeros(len(df))
            for j in set(sel.tolist()) - {-1}:
                m = sel == j
                pos[m] = positions[j][a].to_numpy()[m]
            stitched_pos.append(pos)
            net = run_backtest(df, pd.Series(pos, index=df.index), tf, cost, slip).net_returns
            asset_cal_net.append(_on_calendar(net, asset_dates[a], cal_pos, T))
        oos = _portfolio(asset_cal_net)[all_idx] if all_idx.size else np.array([])
        per_asset = [n[all_idx] for n in asset_cal_net]
        trials = [sharpe_per_period(p[all_idx]) for p in port] if all_idx.size else []
        return {"oos": oos, "idx": all_idx, "folds": list(zip(oos_idx, chosen)),
                "per_asset": per_asset, "trials": trials, "positions": stitched_pos,
                "is_sr": float(np.mean(is_sr)) if is_sr else 0.0}

    condition_rows: List[Dict] = []
    head = None
    for cost in matrix.cost_bps_levels:
        for slip in matrix.slippage_bps_levels:
            w = _walk(cost, slip)
            m = _metrics(w["oos"], tf)
            dsr = deflated_sharpe(w["oos"], w["trials"], len(grid))
            condition_rows.append({
                "asset": "PORTFOLIO", "cost_bps": cost, "slippage_bps": slip,
                "oos_sharpe": m["oos_sharpe"], "oos_sharpe_annual": m["oos_sharpe_annual"],
                "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
                "n_bars": m["n_bars"], "dsr": dsr["dsr"], "grid_size": len(grid),
                "in_sample_sharpe": w["is_sr"]})
            if cost == matrix.headline_cost_bps and slip == matrix.headline_slippage_bps:
                head, head_m, head_dsr = w, m, dsr

    # OOS trades per asset, counted on each asset's own bars for the selected configs.
    oos_set = set(cal[head["idx"]].tolist())
    trades_per_asset, asset_oos_frames = [], []
    for a, df in enumerate(frames):
        mask = np.array([d in oos_set for d in asset_dates[a].tolist()])
        # Same stitched positions that produced the headline returns (flat before OOS).
        trades_per_asset.append(_entries(head["positions"][a][mask]))
        asset_oos_frames.append((df[mask].reset_index(drop=True), asset_dates[a][mask]))
    n_trades = int(sum(trades_per_asset))

    hc, hs = matrix.headline_cost_bps, matrix.headline_slippage_bps
    oos_idx = head["idx"]

    def _port_oos(nets: List[np.ndarray]) -> np.ndarray:
        rows = [_on_calendar(n, d, cal_pos, T) for n, (_, d) in zip(nets, asset_oos_frames)]
        return (np.vstack(rows).sum(axis=0) / n_alive)[oos_idx]

    bh_nets = [run_backtest(f, pd.Series(1.0, index=f.index), tf, hc, hs).net_returns
               for f, _ in asset_oos_frames]
    bh_sr = sharpe_per_period(_port_oos(bh_nets))

    rand_srs, matching = [], []
    for k in range(matrix.random_seeds):
        nets = []
        for a, (f, _) in enumerate(asset_oos_frames):
            n = len(f)
            target = trades_per_asset[a]
            maximum = (n - 1) if strategy.direction == "long_short" else n // 2
            if n < 3 or target > maximum:
                raise ValueError(f"trade-matched benchmark impossible for asset {assets[a]}")
            rng = np.random.Generator(np.random.PCG64(
                np.random.SeedSequence([matrix.random_base_seed + k, a])))
            pos = _trade_matched_positions(n, target, strategy.direction, rng) if target else np.zeros(n)
            nets.append(run_backtest(f, pd.Series(pos, index=f.index), tf, hc, hs).net_returns)
        rand_srs.append(sharpe_per_period(_port_oos(nets)))
    rand_p95 = float(np.percentile(rand_srs, 95))

    per_asset_sr = {assets[a]: sharpe_per_period(head["per_asset"][a][alive[a, oos_idx]])
                    for a in range(len(frames))}
    asset_srs = list(per_asset_sr.values())
    headline = {
        **head_m, "num_trades": n_trades,
        "dsr": head_dsr["dsr"], "expected_max_sr0": head_dsr["expected_max_sr0"],
        "buy_hold_sharpe": bh_sr, "random_timing_sharpe_p95": rand_p95,
        "beats_buy_hold": head_m["oos_sharpe"] > bh_sr,
        "beats_random": head_m["oos_sharpe"] > rand_p95,
        "cost_bps": hc, "slippage_bps": hs, "pooling": "portfolio",
        "sharpe_unit": "per_bar", "bar_frequency": tf,
        "periods_per_year": PERIODS_PER_YEAR.get(tf, 365.0),
        "return_aggregation": "equal_weight_portfolio_union_calendar",
        "sharpe_asset_range": [min(asset_srs), max(asset_srs)],
        "sharpe_asset_count": len(asset_srs),
        "sharpe_uncertainty": {
            "status": "not_estimated", "interval": None, "confidence_level": None,
            "method": None,
            "reason": "No sampling uncertainty estimator is specified for the portfolio Sharpe."},
        "walk_forward": {"execution": "continuous_stitched_positions_v1", "folds": [
            {"start": str(pd.Timestamp(cal[i[0]]).date()), "end": str(pd.Timestamp(cal[i[-1]]).date()),
             "params": dict(grid[j]) if j is not None else None} for i, j in head["folds"]]},
        "random_timing": {"method": TRADE_MATCHED_METHOD, "matched_metric": "num_trades",
                          "level": "portfolio", "per_asset_trades": dict(zip(assets, trades_per_asset)),
                          "seeds": matrix.random_seeds},
    }
    rob = robustness_score(condition_rows, matrix.dsr_threshold)
    stab = stability_score([r["oos_sharpe"] for r in condition_rows])
    gen = generalization_score(per_asset_sr, head["is_sr"], head_m["oos_sharpe"])
    conf = confidence_score(head_dsr["dsr"], rob, gen, head_m["n_bars"])
    v = decide({"headline": headline, "robustness_score": rob}, matrix)
    notes.append("v5 portfolio evaluation: one shared parameter per fold, equal-weight portfolio "
                 "on the UTC-date union calendar; per-asset Sharpes are descriptive only.")
    if not source.is_real:
        notes.insert(0, "ILLUSTRATIVE ONLY — synthetic data (is_real=False). "
                        "This report demonstrates the pipeline; it is NOT evidence "
                        "about the real strategy and must not enter a dossier as a verdict.")
    return EvaluationReport(
        strategy_id=strategy.strategy_id, strategy_name=strategy.name,
        strategy_class=strategy.strategy_class, provider=request.provider,
        is_real=source.is_real, request_hash=request.hash(), matrix_hash=matrix.hash(),
        matrix_version=matrix.version, assets_evaluated=assets,
        condition_rows=condition_rows, per_asset_oos_sharpe=per_asset_sr,
        headline=headline, robustness_score=rob, stability_score=stab,
        generalization_score=gen, confidence_score=conf,
        verdict=v.label, verdict_reasons=list(v.reasons),
        cause_of_death=getattr(v, "cause_of_death", None), notes=notes,
        family_dsr_inputs=[{"asset": "PORTFOLIO", "returns": head["oos"].tolist(),
                            "sr_trials": list(head["trials"])}],
    )
