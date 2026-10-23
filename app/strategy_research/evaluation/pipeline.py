"""The frozen evaluation pipeline — one path every strategy passes through.

evaluate(request) -> EvaluationReport:
  for each asset:
    fetch real OHLCV via the pluggable provider
    label regimes ex-ante
    for each (cost, slippage) in the frozen ladder:
      walk-forward OOS -> stitched OOS returns
      deflated Sharpe (prices in the parameter grid = data-snooping budget)
      benchmarks on the OOS span: buy-and-hold + matched random-timing
      -> one condition row
  aggregate -> headline (pooled OOS at the frozen headline cost/slippage)
  robustness / stability / generalization / confidence scores
  mechanical verdict

Every number is derived from the same code for every strategy, so results are
directly comparable and the whole run is reproducible from (request hash, matrix
hash, provider). Synthetic data (is_real=False) is evaluated identically but the
report is flagged illustrative and never written to a dossier as evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from ..providers import DataRequest, get_provider
from ..strategies import get_strategy
from .backtest import PERIODS_PER_YEAR, _max_drawdown, _trade_returns
from .benchmarks import (TRADE_MATCHED_METHOD, buy_and_hold, random_timing,
                         random_timing_trade_matched)
from .frozen import DEFAULT_MATRIX, EvalRequest, FrozenMatrix
from .portfolio import PORTFOLIO_VERSION, evaluate_portfolio
from .regimes import label_regimes
from .robustness import (
    confidence_score,
    generalization_score,
    robustness_score,
    stability_score,
)
from .stats import (
    benjamini_hochberg,
    bootstrap_sharpe_ci,
    deflated_sharpe,
    sharpe_per_period,
)
from .verdict import decide
from .walkforward import walk_forward


def _count_trades(pos: np.ndarray) -> int:
    if pos.size == 0:
        return 0
    exec_pos = np.concatenate([[0.0], pos[:-1]])
    changes = np.abs(np.diff(np.concatenate([[0.0], exec_pos]))) > 0
    # A "trade" is an entry into a non-zero position.
    entries = 0
    prev = 0.0
    for p in exec_pos:
        if p != prev and p != 0.0:
            entries += 1
        prev = p
    return entries


def _oos_metrics(oos_net: np.ndarray, oos_pos: np.ndarray, timeframe: str) -> Dict:
    ppy = PERIODS_PER_YEAR.get(timeframe, 365.0)
    equity = np.cumprod(1.0 + oos_net) if oos_net.size else np.array([1.0])
    sr = sharpe_per_period(oos_net)
    trades = _trade_returns(np.concatenate([[0.0], oos_pos[:-1]]) if oos_pos.size else np.array([]), oos_net)
    tarr = np.array(trades, dtype=float)
    return {
        "oos_sharpe": sr,
        "oos_sharpe_annual": sr * np.sqrt(ppy),
        "total_return": float(equity[-1] - 1.0),
        "max_drawdown": _max_drawdown(equity),
        "num_trades": _count_trades(oos_pos),
        "win_rate": float((tarr > 0).mean()) if tarr.size else 0.0,
        "n_bars": int(oos_net.size),
    }


@dataclass
class EvaluationReport:
    strategy_id: str
    strategy_name: str
    strategy_class: str
    provider: str
    is_real: bool
    request_hash: str
    matrix_hash: str
    matrix_version: str
    assets_evaluated: List[str]
    condition_rows: List[Dict] = field(default_factory=list)
    per_asset_oos_sharpe: Dict[str, float] = field(default_factory=dict)
    headline: Dict = field(default_factory=dict)
    robustness_score: float = 0.0
    stability_score: float = 0.0
    generalization_score: float = 0.0
    confidence_score: float = 0.0
    verdict: str = "Not Yet Tested"
    verdict_reasons: List[str] = field(default_factory=list)
    cause_of_death: str | None = None
    notes: List[str] = field(default_factory=list)
    family_dsr_inputs: List[Dict] = field(default_factory=list, repr=False)

    def to_dict(self) -> Dict:
        out = asdict(self)
        out.pop("family_dsr_inputs", None)
        return out


def evaluate(request: EvalRequest, matrix: FrozenMatrix = DEFAULT_MATRIX,
             provider_kwargs: Dict | None = None) -> EvaluationReport:
    if PORTFOLIO_VERSION in (matrix.version, request.matrix_version):
        return evaluate_portfolio(request, matrix, provider_kwargs)
    exact_matching = matrix.random_timing_method == TRADE_MATCHED_METHOD
    versioned_request = request.matrix_version in ("srp-matrix-v3", "srp-matrix-v4")
    if (exact_matching or versioned_request) and request.matrix_version != matrix.version:
        raise ValueError("request.matrix_version must match the selected v3/v4 matrix")
    strategy = get_strategy(request.strategy_id)
    source = get_provider(request.provider, **(provider_kwargs or {}))
    tf = request.timeframe

    condition_rows: List[Dict] = []
    per_asset_oos_sharpe: Dict[str, float] = {}
    pooled_oos: List[np.ndarray] = []
    pooled_pos: List[np.ndarray] = []
    per_asset_headline: List[Dict] = []
    headline_bh: List[float] = []
    headline_rand_p95: List[float] = []
    benchmark_matching: List[Dict] = []
    assets_done: List[str] = []
    notes: List[str] = []
    family_dsr_inputs: List[Dict] = []

    for asset in request.assets:
        frame = source.get(DataRequest(asset, tf, request.start, request.end, request.limit))
        if frame.n < max(matrix.min_train + 50, 200):
            notes.append(f"{asset}: only {frame.n} bars from '{request.provider}' — skipped "
                         f"(need >= {max(matrix.min_train + 50, 200)}).")
            continue
        df = frame.df
        assets_done.append(asset)

        for cost in matrix.cost_bps_levels:
            for slip in matrix.slippage_bps_levels:
                wf = walk_forward(df, strategy, tf, cost, slip,
                                  matrix.n_splits, matrix.min_train)
                m = _oos_metrics(wf.oos_net, wf.oos_pos, tf)
                dsr = deflated_sharpe(wf.oos_net, wf.sr_trials, wf.grid_size)
                row = {
                    "asset": asset, "cost_bps": cost, "slippage_bps": slip,
                    "oos_sharpe": m["oos_sharpe"],
                    "oos_sharpe_annual": m["oos_sharpe_annual"],
                    "total_return": m["total_return"],
                    "max_drawdown": m["max_drawdown"],
                    "num_trades": m["num_trades"], "n_bars": m["n_bars"],
                    "dsr": dsr["dsr"], "grid_size": wf.grid_size,
                    "in_sample_sharpe": wf.in_sample_sharpe,
                }
                condition_rows.append(row)

                if cost == matrix.headline_cost_bps and slip == matrix.headline_slippage_bps:
                    per_asset_oos_sharpe[asset] = m["oos_sharpe"]
                    pooled_oos.append(wf.oos_net)
                    pooled_pos.append(wf.oos_pos)
                    df_oos = df.iloc[wf.oos_index].reset_index(drop=True)
                    bh = buy_and_hold(df_oos, tf, cost, slip)
                    benchmark = random_timing_trade_matched if exact_matching else random_timing
                    target_trades = m["num_trades"] if exact_matching else max(m["num_trades"], 1)
                    rnd = benchmark(df_oos, tf, cost, slip,
                                        target_trades=target_trades,
                                        direction=strategy.direction,
                                        n_seeds=matrix.random_seeds,
                                        base_seed=matrix.random_base_seed)
                    if exact_matching:
                        benchmark_matching.append({"asset": asset, **rnd["matching"]})
                    headline_bh.append(bh["sharpe_per_period"])
                    headline_rand_p95.append(rnd["sharpe_p95"])
                    per_asset_headline.append({
                        "asset": asset, "oos_sharpe": m["oos_sharpe"],
                        "oos_sharpe_annual": m["oos_sharpe_annual"],
                        "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
                        "num_trades": m["num_trades"], "n_bars": m["n_bars"],
                        "dsr": dsr["dsr"], "bh_sharpe": bh["sharpe_per_period"],
                        "rand_p95": rnd["sharpe_p95"],
                    })
                    family_dsr_inputs.append({
                        "asset": asset,
                        "returns": wf.oos_net.tolist(),
                        "sr_trials": list(wf.sr_trials),
                    })

    # ----- headline aggregate at the frozen headline cost/slippage ----- #
    # Two pooling methods, selected by the matrix version (see FrozenMatrix.pooling):
    #   v1 "concatenation" — stitch every asset's OOS returns into one series (the
    #       drawdown of that concatenation is an artifact, not a real portfolio DD).
    #   v2 "per_asset"     — compute metrics PER ASSET, then equal-weight aggregate;
    #       max drawdown is the mean of per-asset OOS drawdowns (economically sound).
    mean_bh = float(np.mean(headline_bh)) if headline_bh else 0.0
    rand_bar = float(np.mean(headline_rand_p95)) if headline_rand_p95 else 0.0
    grid_n = max(len(strategy.param_grid()), 1)

    if matrix.pooling == "per_asset" and per_asset_headline:
        def _amean(k):
            return float(np.mean([r[k] for r in per_asset_headline]))
        srs = [r["oos_sharpe"] for r in per_asset_headline]
        hm = {
            "oos_sharpe": float(np.mean(srs)),
            "oos_sharpe_annual": _amean("oos_sharpe_annual"),
            "total_return": _amean("total_return"),
            "max_drawdown": _amean("max_drawdown"),        # mean per-asset OOS DD
            "num_trades": int(sum(r["num_trades"] for r in per_asset_headline)),
            "n_bars": int(sum(r["n_bars"] for r in per_asset_headline)),
        }
        hdsr = {"dsr": _amean("dsr"), "expected_max_sr0": 0.0}  # mean per-asset DSR
        sr_lo, sr_hi = (min(srs), max(srs))                     # dispersion band across assets
    else:
        pooled = np.concatenate(pooled_oos) if pooled_oos else np.array([])
        pooled_p = np.concatenate(pooled_pos) if pooled_pos else np.array([])
        hm = _oos_metrics(pooled, pooled_p, tf)
        hdsr = deflated_sharpe(
            pooled,
            [r["oos_sharpe"] for r in condition_rows
             if r["cost_bps"] == matrix.headline_cost_bps
             and r["slippage_bps"] == matrix.headline_slippage_bps],
            grid_n,
        )
        sr_lo, sr_hi = bootstrap_sharpe_ci(pooled, matrix.bootstrap_B, matrix.bootstrap_seed)

    headline = {
        **hm,
        "dsr": hdsr["dsr"], "expected_max_sr0": hdsr.get("expected_max_sr0", 0.0),
        # Keep frozen v1-v3 serialized fields/order. The v4 schema separates meanings.
        **({"sharpe_ci95": [sr_lo, sr_hi]} if matrix.version != "srp-matrix-v4" else {}),
        "buy_hold_sharpe": mean_bh,
        "random_timing_sharpe_p95": rand_bar,
        "beats_buy_hold": hm["oos_sharpe"] > mean_bh,
        "beats_random": hm["oos_sharpe"] > rand_bar,
        "cost_bps": matrix.headline_cost_bps,
        "slippage_bps": matrix.headline_slippage_bps,
        "pooling": matrix.pooling,
        "sharpe_unit": "per_bar",
        "bar_frequency": tf,
        "periods_per_year": PERIODS_PER_YEAR.get(tf, 365.0),
        "return_aggregation": ("mean_per_asset_cumulative_return"
                               if matrix.pooling == "per_asset" else "pooled_cumulative_return"),
    }

    if matrix.version == "srp-matrix-v4":
        asset_sharpes = [r["oos_sharpe"] for r in per_asset_headline]
        headline["sharpe_asset_range"] = ([min(asset_sharpes), max(asset_sharpes)]
                                          if asset_sharpes else None)
        headline["sharpe_asset_count"] = len(asset_sharpes)
        headline["sharpe_uncertainty"] = {
            "status": "not_estimated", "interval": None,
            "confidence_level": None, "method": None,
            "reason": "No sampling uncertainty estimator is specified for the "
                      "equal-weight mean of per-asset Sharpe. Asset min/max is descriptive.",
        }

    if exact_matching:
        headline["random_timing"] = {
            "method": TRADE_MATCHED_METHOD, "matched_metric": "num_trades",
            "per_asset": benchmark_matching,
        }
        notes.append("Random timing matches reported OOS trade count per asset and seed; "
                     "it does not match exposure, turnover, holding durations or total cost. "
                     "The existing walk-forward fold-boundary accounting is unchanged.")

    rob = robustness_score(condition_rows, matrix.dsr_threshold)
    stab = stability_score([r["oos_sharpe"] for r in condition_rows])
    gen = generalization_score(per_asset_oos_sharpe, hm["oos_sharpe"] or 0.0,
                               hm["oos_sharpe"])
    # generalization uses in-sample vs oos retention; approximate in-sample via mean
    is_sr = float(np.mean([r["in_sample_sharpe"] for r in condition_rows])) if condition_rows else 0.0
    gen = generalization_score(per_asset_oos_sharpe, is_sr, hm["oos_sharpe"])
    conf = confidence_score(hdsr["dsr"], rob, gen, hm["n_bars"])

    summary = {"headline": headline, "robustness_score": rob}
    v = decide(summary, matrix) if assets_done else \
        type("V", (), {"label": "Inconclusive",
                       "reasons": ["no asset had enough real data"],
                       "cause_of_death": None})()

    if not source.is_real:
        notes.insert(0, "ILLUSTRATIVE ONLY — synthetic data (is_real=False). "
                        "This report demonstrates the pipeline; it is NOT evidence "
                        "about the real strategy and must not enter a dossier as a verdict.")

    return EvaluationReport(
        strategy_id=strategy.strategy_id, strategy_name=strategy.name,
        strategy_class=strategy.strategy_class, provider=request.provider,
        is_real=source.is_real,
        request_hash=request.hash(), matrix_hash=matrix.hash(),
        matrix_version=matrix.version, assets_evaluated=assets_done,
        condition_rows=condition_rows, per_asset_oos_sharpe=per_asset_oos_sharpe,
        headline=headline, robustness_score=rob, stability_score=stab,
        generalization_score=gen, confidence_score=conf,
        verdict=v.label, verdict_reasons=list(v.reasons),
        cause_of_death=getattr(v, "cause_of_death", None), notes=notes,
        family_dsr_inputs=family_dsr_inputs,
    )
