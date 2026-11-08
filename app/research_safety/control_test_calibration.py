"""Calibration of ``target-control-test-v1`` on synthetic data (spec sections 5-7).

    python -m app.research_safety.control_test_calibration --stage dev   --layer A
    python -m app.research_safety.control_test_calibration --stage dev   --layer B
    python -m app.research_safety.control_test_calibration --stage final --layer A --commit <git hash>

Everything is deterministic from the seeds. The final stage refuses to run twice into the same
output folder and records the commit hash and the SHA-256 of the code it ran.
Synthetic data only: no real strategy is evaluated.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import control_test as CT

# ---- locked synthetic asset model (spec section 5) ---- #
ASSETS = ["AAPL", "BTC", "ETH", "SPY", "XAU"]
ANNUAL_VOL = np.array([0.31, 0.65, 0.85, 0.19, 0.17])
CORR = np.array([[1.0000, 0.2333, 0.2545, 0.7607, 0.0490],
                 [0.2333, 1.0000, 0.8457, 0.3175, 0.1058],
                 [0.2545, 0.8457, 1.0000, 0.3425, 0.0935],
                 [0.7607, 0.3175, 0.3425, 1.0000, 0.0881],
                 [0.0490, 0.1058, 0.0935, 0.0881, 1.0000]])
NU = 5
GARCH_ALPHA, GARCH_BETA = 0.08, 0.90
PPY = 252.0
BURN_IN = 250
T_LAYER_A = 1710
T_LAYER_B = 2140
CONTROL_SHARPE = 0.30

DELTAS = [0.0, 0.25, 0.5, 1.0]
RHOS = [0.3, 0.5, 0.8]
LAYER_A_SCENARIOS = [{"delta": d, "rho": r} for d in DELTAS for r in RHOS]
LAYER_B_SCENARIOS = [{"candidate": "null"}, {"candidate": "edge"}]

REPS = {"dev": {"A": 200, "B": 100}, "final": {"A": 2000, "B": 500}}
SEED_BASE = {"dev": 10_000, "final": 900_000}
SCENARIO_STRIDE = 10_000
LONG_SIM_BARS = int(400 * PPY)          # Layer B truth runs: 400 synthetic years
LONG_SIM_SEED = 4_242                   # development seed for the truth runs
EDGE_TARGET, EDGE_TOL = 0.50, 0.02

OUT_ROOT = Path("knowledge_base/research_safety/calibration") / CT.VERSION
CODE_FILES = ["app/research_safety/control_test.py", "app/research_safety/control_test_calibration.py",
              "app/research_safety/target_control.py", "app/strategy_research/evaluation/walkforward.py",
              "app/strategy_research/evaluation/backtest.py"]


def _chol() -> np.ndarray:
    return np.linalg.cholesky(CORR)


def correlated_t(rng: np.random.Generator, n: int) -> np.ndarray:
    """(n, 5) multivariate Student-t shocks, unit variance, common mixing (tail dependence)."""
    g = rng.standard_normal((n, len(ASSETS))) @ _chol().T
    w = rng.chisquare(NU, size=(n, 1)) / NU
    return g / np.sqrt(w) * math.sqrt((NU - 2) / NU)


def garch_vol(z: np.ndarray) -> np.ndarray:
    """Daily conditional volatility (n, 5) of GARCH(1,1) driven by unit shocks z (after burn-in)."""
    var_u = (ANNUAL_VOL / math.sqrt(PPY)) ** 2
    omega = var_u * (1 - GARCH_ALPHA - GARCH_BETA)
    n = z.shape[0]
    s2 = np.empty_like(z)
    s2[0] = var_u
    for t in range(1, n):
        s2[t] = omega + GARCH_ALPHA * s2[t - 1] * z[t - 1] ** 2 + GARCH_BETA * s2[t - 1]
    return np.sqrt(s2)


LOG_RETURN_CAP = 0.5   # Layer B only: |daily log return| <= 0.5 (+65 % / -39 %); see spec amendment


def asset_returns(rng: np.random.Generator, n: int, cap: float = LOG_RETURN_CAP) -> np.ndarray:
    """(n, 5) zero-drift daily simple returns from the locked asset model.

    The GARCH draw is used as a log return and capped at +-cap, because Student-t shocks on a
    GARCH volatility path occasionally produce one-day moves above +100 %, which would make a short
    position lose more than its capital (real data: largest daily falls about -40 %)."""
    z = correlated_t(rng, n + BURN_IN)
    r = np.clip(garch_vol(z) * z, -cap, cap)
    return np.expm1(r[BURN_IN:])


# ---- Layer A ---- #
def layer_a_series(rng: np.random.Generator, delta: float, rho: float, n: int = T_LAYER_A):
    """Paired (candidate, control) daily net returns (n, 5) with true dSR = delta, corr = rho."""
    zk = correlated_t(rng, n + BURN_IN)
    u = correlated_t(rng, n + BURN_IN)
    zc = rho * zk + math.sqrt(1 - rho ** 2) * u
    vol = garch_vol(zk)
    sd_ann = ANNUAL_VOL
    mu_k = CONTROL_SHARPE * sd_ann / PPY
    mu_c = (CONTROL_SHARPE + delta) * sd_ann / PPY
    ctrl = mu_k + vol * zk
    cand = mu_c + vol * zc
    return cand[BURN_IN:], ctrl[BURN_IN:]


# ---- Layer B ---- #
def synthetic_frames(rng: np.random.Generator, n: int) -> Dict[str, pd.DataFrame]:
    r = asset_returns(rng, n)
    days = pd.bdate_range("2000-01-03", periods=n)
    t = days.as_unit("ms").asi8.astype("int64")          # epoch milliseconds, as the providers use
    out = {}
    for j, a in enumerate(ASSETS):
        close = 100.0 * np.cumprod(1.0 + r[:, j])
        out[a] = pd.DataFrame({"open_time": t, "open": close, "high": close, "low": close,
                               "close": close, "volume": 1.0})
    return out


def _control_positions(close: pd.Series) -> np.ndarray:
    from .target_control import TargetOnlyControl
    df = pd.DataFrame({"close": close})
    return TargetOnlyControl().positions(df).to_numpy()


def make_layer_b_strategies(q: float, oracle_seed: int):
    """Null candidate (lagged control, turnover-matched, no information) and edge candidate
    (control position replaced on a fraction q of bars by the sign of the next bar's return)."""
    from app.strategy_research.strategies.base import Strategy

    class NullCandidate(Strategy):
        strategy_id = "CAL-NULL-LAG20"
        name = "Calibration null: control position lagged 20 bars"
        strategy_class = "calibration"
        direction = "long_short"
        default_params: Dict = {}
        declared_inputs = {"close"}

        def positions(self, df, params=None):
            p = _control_positions(df["close"])
            out = np.zeros_like(p)
            out[20:] = p[:-20]
            return pd.Series(out, index=df.index)

        def param_grid(self):
            return [{}]

    class EdgeCandidate(Strategy):
        strategy_id = "CAL-EDGE-ORACLE"
        name = "Calibration edge: control with next-bar sign on a fraction q of bars"
        strategy_class = "calibration"
        direction = "long_short"
        default_params: Dict = {}
        declared_inputs = {"close"}

        def positions(self, df, params=None):
            p = _control_positions(df["close"]).astype(float)
            close = df["close"].to_numpy(dtype=float)
            nxt = np.zeros_like(close)
            nxt[:-1] = np.sign(close[1:] / close[:-1] - 1.0)
            # Which bars get the oracle sign: independent of returns, fixed per synthetic path.
            key = int(hashlib.sha256(close[:5].tobytes()).hexdigest()[:12], 16)
            pick = np.random.default_rng([oracle_seed, key, len(df)]).random(len(df)) < q
            p[pick] = nxt[pick]
            return pd.Series(p, index=df.index)

        def param_grid(self):
            return [{}]

    from app.strategy_research.strategies import register_strategy
    from .target_control import ensure_controls_registered
    ensure_controls_registered()
    register_strategy(NullCandidate())
    register_strategy(EdgeCandidate())
    return NullCandidate.strategy_id, EdgeCandidate.strategy_id


def true_layer_b_delta(candidate_id: str, frames) -> float:
    """dSR of a candidate on one very long run (point value, no bootstrap)."""
    from app.strategy_research import MATRICES
    from app.strategy_research.evaluation.walkforward import walk_forward
    from app.strategy_research.strategies import get_strategy
    m = MATRICES["srp-matrix-v4"]
    C, K = [], []
    for a, df in frames.items():
        wc = walk_forward(df, get_strategy(candidate_id), "1d", m.headline_cost_bps, m.headline_slippage_bps,
                          m.n_splits, m.min_train)
        wk = walk_forward(df, get_strategy("CTRL-TARGET-ONLY"), "1d", m.headline_cost_bps,
                          m.headline_slippage_bps, m.n_splits, m.min_train)
        C.append(wc.oos_net), K.append(wk.oos_net)
    return CT.delta_sr(np.column_stack(C), np.column_stack(K), PPY)[0]


def calibrate_q(log: List[str]) -> Dict:
    """Bisection on q so the edge candidate's long-run dSR is 0.50 +- 0.02 (development seed)."""
    frames = synthetic_frames(np.random.default_rng(LONG_SIM_SEED), LONG_SIM_BARS)
    lo, hi = 0.0, 0.2
    q, d = None, None
    for _ in range(30):
        q = (lo + hi) / 2
        _, edge = make_layer_b_strategies(q, LONG_SIM_SEED)
        d = true_layer_b_delta(edge, frames)
        log.append(f"q={q:.6f} long-run dSR={d:.4f}")
        if abs(d - EDGE_TARGET) <= EDGE_TOL / 4:
            break
        lo, hi = (q, hi) if d < EDGE_TARGET else (lo, q)
    null_id, _ = make_layer_b_strategies(q, LONG_SIM_SEED)
    d_null = true_layer_b_delta(null_id, frames)
    return {"q": q, "edge_true_delta": d, "null_true_delta": d_null, "bars_per_asset": LONG_SIM_BARS,
            "seed": LONG_SIM_SEED}


def wilson(k: int, n: int, z: float = 1.959964) -> List[float]:
    if n == 0:
        return [float("nan"), float("nan")]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 4), round(c + h, 4)]


def _summ(verdicts: List[str], deltas: List[float]) -> Dict:
    n = len(verdicts)
    k = sum(v == "candidate_wins" for v in verdicts)
    return {"n": n, "approved": k, "approval_rate": round(k / n, 4), "wilson95": wilson(k, n),
            "control_wins": sum(v == "control_wins" for v in verdicts),
            "inconclusive": sum(v == "inconclusive" for v in verdicts),
            "mean_point_delta": round(float(np.mean(deltas)), 4),
            "sd_point_delta": round(float(np.std(deltas, ddof=1)), 4) if n > 1 else None}


def run_layer_a(stage: str, reps: Optional[int] = None, log=print) -> Dict:
    reps = reps or REPS[stage]["A"]
    out = []
    for i, sc in enumerate(LAYER_A_SCENARIOS):
        verdicts, deltas = [], []
        for r in range(reps):
            seed = SEED_BASE[stage] + SCENARIO_STRIDE * i + r
            cand, ctrl = layer_a_series(np.random.default_rng([seed, 1]), sc["delta"], sc["rho"])
            res = CT.paired_test(cand, ctrl, seed=seed * 2 + 1)
            verdicts.append(res["verdict"]), deltas.append(res["delta_sharpe_annual"])
        s = {**sc, **_summ(verdicts, deltas)}
        out.append(s)
        log(f"A delta={sc['delta']} rho={sc['rho']}: approved {s['approval_rate']:.1%} {s['wilson95']}")
    return {"layer": "A", "stage": stage, "reps_per_scenario": reps, "bars": T_LAYER_A, "scenarios": out}


def run_layer_b(stage: str, reps: Optional[int] = None, truth: Optional[Dict] = None, log=print) -> Dict:
    reps = reps or REPS[stage]["B"]
    notes: List[str] = []
    truth = truth or calibrate_q(notes)
    null_id, edge_id = make_layer_b_strategies(truth["q"], LONG_SIM_SEED)
    out = []
    for i, sc in enumerate(LAYER_B_SCENARIOS):
        cid = null_id if sc["candidate"] == "null" else edge_id
        verdicts, deltas = [], []
        for r in range(reps):
            seed = SEED_BASE[stage] + SCENARIO_STRIDE * (100 + i) + r
            frames = synthetic_frames(np.random.default_rng([seed, 1]), T_LAYER_B)
            res = CT.compare_calibrated(cid, "CTRL-TARGET-ONLY", ASSETS, frames=frames, seed=seed * 2 + 1)
            verdicts.append(res["verdict"]), deltas.append(res["delta_sharpe_annual"])
        s = {**sc, **_summ(verdicts, deltas)}
        out.append(s)
        log(f"B {sc['candidate']}: approved {s['approval_rate']:.1%} {s['wilson95']}")
    return {"layer": "B", "stage": stage, "reps_per_scenario": reps, "price_bars": T_LAYER_B,
            "truth": truth, "truth_log": notes, "scenarios": out}


def acceptance(a: Dict, b: Optional[Dict]) -> Dict:
    """Spec section 7, evaluated on whatever stage is passed in."""
    nulls = [s for s in a["scenarios"] if s["delta"] == 0.0]
    false_ok = all(s["approval_rate"] <= 0.05 and s["wilson95"][1] <= 0.06 for s in nulls)
    prim = next(s for s in a["scenarios"] if s["delta"] == 0.5 and s["rho"] == 0.5)
    detect_ok = prim["approval_rate"] >= 0.80
    res = {"false_approvals_ok": false_ok, "detection_primary_ok": detect_ok,
           "false_approval_rates": {str(s["rho"]): s["approval_rate"] for s in nulls},
           "detection_primary": prim["approval_rate"], "detection_primary_wilson95": prim["wilson95"]}
    if b is not None:
        bn = next(s for s in b["scenarios"] if s["candidate"] == "null")
        res["layer_b_null_ok"] = (bn["approval_rate"] <= 0.05) if b["truth"]["null_true_delta"] <= 0 else None
        res["layer_b_null_rate"] = bn["approval_rate"]
    res["accepted"] = bool(false_ok and detect_ok and res.get("layer_b_null_ok", True) is not False)
    return res


def code_hashes(root: Path = Path(".")) -> Dict[str, str]:
    return {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in CODE_FILES}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["dev", "final"], required=True)
    ap.add_argument("--layer", choices=["A", "B"], required=True)
    ap.add_argument("--commit", help="git commit of the code (required for --stage final)")
    ap.add_argument("--truth", help="Layer B truth JSON from the dev run (re-used, not re-derived)")
    ap.add_argument("--out", default=str(OUT_ROOT))
    args = ap.parse_args(argv)
    if args.stage == "final" and not args.commit:
        ap.error("--commit is required for the final stage")
    out_dir = Path(args.out) / args.stage
    out_file = out_dir / f"layer_{args.layer}.json"
    if args.stage == "final" and out_file.exists():
        ap.error(f"{out_file} exists: final seeds are used once")
    out_dir.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    truth = json.loads(Path(args.truth).read_text())["truth"] if args.truth else None
    res = run_layer_a(args.stage) if args.layer == "A" else run_layer_b(args.stage, truth=truth)
    res.update({"version": CT.VERSION, "spec": CT.SPEC, "started_utc": started,
                "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "commit": args.commit, "code_sha256": code_hashes(),
                "test_parameters": {"block_length": CT.BLOCK_LENGTH, "n_boot": CT.N_BOOT, "alpha": CT.ALPHA},
                "seed_base": SEED_BASE[args.stage], "synthetic_only": True})
    out_file.write_text(json.dumps(res, indent=1))
    print(f"wrote {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
