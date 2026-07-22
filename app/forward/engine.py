"""Forward-test lifecycle engine.

Drives a locked forward test through its states:

    DRAFT ── activate ──▶ ACTIVE ── run ──▶ (observations) ── evaluate ──▶ PASSED│FAILED│EXTEND
      │                     │
      └── consistency fail  └── integrity drift ──▶ INVALIDATED   (history preserved)

Key guarantees enforced here:
  * A forward test can only START if the research promotion gate returned PROMOTE.
  * The SAME causal simulator (app.forward.simulate) drives both the locked backtest
    reference and the forward run, so backtest and forward can never silently diverge.
  * Before activation a historical-tail consistency + causality check must pass; a
    look-ahead / repainting strategy is blocked (stays DRAFT).
  * Before EVERY forward run the locked code/param hashes are re-checked; any drift
    invalidates the test (status INVALIDATED) without touching the original manifest,
    events or observations.
  * Forward observations are physically separated from training/backtest data (only
    bars on/after the locked start date, tagged data_partition="forward").

Research only: no live broker, no real orders, no private keys. This is simulation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np

from app.strategy_research.strategies import get_strategy

from .manifest import build_manifest
from .simulate import (
    EQUITY0,
    executed_positions,
    metrics_from_observations,
    simulate,
    simulate_forward,
    window_metrics,
)
from .store import ForwardStore

# statuses from which a fresh forward run may collect observations
_RUNNABLE = {"ACTIVE", "EXTEND"}
_TERMINAL = {"PASSED", "FAILED", "INVALIDATED", "CANCELLED"}


class ForwardError(RuntimeError):
    """Raised for lifecycle violations (e.g. start without PROMOTE, run a DRAFT test)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_to_ms(date_str: str) -> int:
    dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _causal_ok(strategy, df, k: int) -> bool:
    """A strategy is causal/non-repainting iff positions on the prefix df[:k] equal the
    first k positions computed on the full frame (no look-ahead, no repaint)."""
    if k < 2 or k > len(df):
        return True
    full = strategy.positions(df).to_numpy()
    trunc = strategy.positions(df.iloc[:k].reset_index(drop=True)).to_numpy()
    return bool(np.array_equal(np.nan_to_num(full[:k]), np.nan_to_num(trunc[:k])))


class ForwardEngine:
    def __init__(self, root: Optional[str] = None, provider: str = "csv",
                 provider_kwargs: Optional[Dict] = None) -> None:
        self.store = ForwardStore(root=root)
        self.root = root
        self.provider = provider
        self.provider_kwargs = provider_kwargs or {}

    # ------------------------------------------------------------------ data --
    def _load_full_df(self, asset: str, timeframe: str):
        from app.strategy_research.providers import DataRequest, get_provider
        return get_provider(self.provider, **self.provider_kwargs).get(
            DataRequest(asset=asset, timeframe=timeframe)).df

    # ----------------------------------------------------------------- checks --
    def _consistency_check(self, strategy, df, start_ms: int, cost_bps: float,
                           slippage_bps: float, timeframe: str, tail: int) -> Dict:
        """Historical-tail consistency + causality check performed before activation.

        1. Causality: positions on truncated prefixes match the full-frame positions
           (a repainting / look-ahead strategy fails here).
        2. Engine equivalence: the backtest simulator and the forward simulator produce
           identical executed positions on the overlapping historical tail (proves the
           forward run uses the SAME strategy logic as the backtest)."""
        n = len(df)
        cut_points = sorted({p for p in (max(n - tail, 5), max(n // 2, 5), n - 1)
                             if 2 <= p <= n})
        causal = all(_causal_ok(strategy, df, k) for k in cut_points)

        ot = df["open_time"].to_numpy("int64") if "open_time" in df.columns \
            else np.arange(n, dtype="int64")
        tail_start_idx = max(n - tail, 1)
        tail_start_ms = int(ot[tail_start_idx]) if tail_start_idx < n else int(ot[-1])
        bt_rows, _ = simulate(strategy, df, cost_bps, slippage_bps, timeframe)
        fw_rows, _ = simulate_forward(strategy, df, tail_start_ms, cost_bps,
                                      slippage_bps, timeframe)
        bt_map = {r["candle_open_time_ms"]: r["executed_position"] for r in bt_rows}
        engine_equiv = len(fw_rows) > 0 and all(
            bt_map.get(r["candle_open_time_ms"]) == r["executed_position"] for r in fw_rows)

        return {"ok": bool(causal and engine_equiv), "causal": bool(causal),
                "engine_equivalent": bool(engine_equiv),
                "cut_points": cut_points, "tail_bars_checked": len(fw_rows)}

    # ------------------------------------------------------------------ start --
    def start(self, strategy_id: str, assets: Sequence[str], *, start_date: str,
              timeframe: str = "1d", cost_bps: float = 5.0, slippage_bps: float = 5.0,
              control_id: Optional[str] = None, thresholds: Optional[Dict] = None,
              promotion_report: Optional[Dict] = None, df=None, tail: int = 250,
              ts: Optional[str] = None) -> Dict:
        """Lock a forward test. Refuses unless the promotion gate returns PROMOTE. Builds
        the deterministic manifest, computes the locked backtest reference on the training
        slice (bars strictly before start_date), then runs the historical-tail consistency
        check and activates (ACTIVE) only if it passes."""
        ts = ts or _now_iso()
        assets = list(assets)
        strategy = get_strategy(strategy_id)

        # 1) promotion gate must say PROMOTE (never start a rejected/held strategy)
        if promotion_report is None:
            from app.research_safety import evaluate_promotion
            promotion_report = evaluate_promotion(
                strategy_id, assets, control_id=control_id, timeframe=timeframe,
                matrix="srp-matrix-v2", provider=self.provider,
                provider_kwargs=self.provider_kwargs)
        decision = promotion_report.get("decision")
        if decision != "PROMOTE":
            raise ForwardError(
                f"promotion gate decision is {decision!r}, not 'PROMOTE'; "
                f"a forward test can only start for a PROMOTE strategy")

        params = dict(getattr(strategy, "default_params", {}) or {})
        declared = getattr(strategy, "declared_inputs", None)

        if df is None:
            df = self._load_full_df(assets[0], timeframe)
        start_ms = _date_to_ms(start_date)
        train_df = df[df["open_time"] < start_ms].reset_index(drop=True) \
            if "open_time" in df.columns else df

        # locked backtest reference — computed with the SAME simulator used for forward
        _, ref_metrics = simulate(strategy, train_df, cost_bps, slippage_bps, timeframe)
        oos_ref = None
        if len(train_df) > 20:                       # a short OOS tail of the training data
            k = max(len(train_df) - max(len(train_df) // 5, 10), 1)
            _, oos_ref = simulate(strategy, train_df.iloc[k:].reset_index(drop=True),
                                  cost_bps, slippage_bps, timeframe)

        promo_ref = "research_safety/promotions/%s.json" % strategy_id
        manifest = build_manifest(
            strategy=strategy, params=params, assets=assets, timeframe=timeframe,
            start_date=start_date, cost_bps=cost_bps, slippage_bps=slippage_bps,
            declared_inputs=declared, promotion_ref=promo_ref,
            backtest_ref={"reference_metrics": ref_metrics, "n_train_bars": int(len(train_df))},
            oos_ref=oos_ref, thresholds=thresholds, repo_root=self.root or ".")
        manifest["promotion_decision"] = decision
        manifest["reference_metrics"] = ref_metrics

        ftid = manifest["forward_test_id"]
        if self.store.exists(ftid):
            raise ForwardError(
                f"forward test {ftid} already exists; a changed strategy/params yields a "
                f"new id — never overwrite a prior test")

        self.store.write_manifest(manifest)
        self.store.append_event(ftid, None, "DRAFT",
                                "forward test locked (promotion gate PROMOTE)", ts,
                                {"assets": assets, "start_date": start_date,
                                 "n_train_bars": int(len(train_df))})

        # 2) consistency + causality on the historical tail (blocks look-ahead strategies)
        check = self._consistency_check(strategy, df, start_ms, cost_bps, slippage_bps,
                                        timeframe, tail)
        if not check["ok"]:
            self.store.set_status(
                manifest, "DRAFT",
                "activation BLOCKED: forward/backtest signal deviation or repaint", ts, check)
            manifest["activation"] = {"activated": False, "check": check}
            return manifest

        self.store.set_status(manifest, "ACTIVE",
                              "historical-tail consistency check passed; activated", ts, check)
        manifest["activation"] = {"activated": True, "check": check}
        return manifest

    # -------------------------------------------------------------------- run --
    def run(self, ftid: str, *, df=None, strategy=None, ts: Optional[str] = None) -> Dict:
        """Integrity-check then collect NEW forward observations (bars on/after the locked
        start date that have not yet been observed). Any locked-hash drift invalidates the
        test instead of running it."""
        ts = ts or _now_iso()
        manifest = self.store.load_manifest(ftid)
        strat = strategy if strategy is not None else get_strategy(manifest["strategy_id"])
        status = manifest.get("status")
        if status in _TERMINAL:
            raise ForwardError(f"forward test {ftid} is {status}; it cannot be run")

        # integrity FIRST — never run a test whose locked code/params changed
        integ = self.store.integrity_check(manifest, strat)
        if not integ["ok"]:
            self.store.set_status(
                manifest, "INVALIDATED",
                "integrity check failed: locked code/params changed — start a new "
                "strategy version + new forward-test id", ts, integ)
            return {"forward_test_id": ftid, "status": "INVALIDATED", "ran": False,
                    "integrity": integ}

        if status == "DRAFT":
            raise ForwardError(
                f"forward test {ftid} is DRAFT (activation was blocked); it cannot collect "
                f"forward observations")

        if df is None:
            df = self._load_full_df(manifest["assets"][0], manifest["timeframe"])
        start_ms = _date_to_ms(manifest["start_date"])
        prev = self.store.read_observations(ftid)
        after_ms = max((int(o["candle_open_time_ms"]) for o in prev), default=None)

        rows, metrics = simulate_forward(
            strat, df, start_ms, manifest["cost_bps"], manifest["slippage_bps"],
            manifest["timeframe"], after_ms=after_ms)
        for r in rows:
            self.store.append_observation(ftid, r)

        return {"forward_test_id": ftid, "status": manifest["status"], "ran": True,
                "new_observations": len(rows),
                "total_observations": len(prev) + len(rows),
                "window_metrics": metrics, "integrity": integ}

    # ----------------------------------------------------------------- verify --
    def verify(self, ftid: str, *, strategy=None, enforce: bool = False,
               ts: Optional[str] = None) -> Dict:
        """Read-only integrity report (recompute the locked hashes). With enforce=True a
        detected drift moves the test to INVALIDATED."""
        manifest = self.store.load_manifest(ftid)
        strat = strategy if strategy is not None else get_strategy(manifest["strategy_id"])
        integ = self.store.integrity_check(manifest, strat)
        out = {"forward_test_id": ftid, "status": manifest["status"], "integrity": integ}
        if enforce and not integ["ok"] and manifest["status"] not in _TERMINAL:
            ts = ts or _now_iso()
            self.store.set_status(manifest, "INVALIDATED",
                                  "integrity check failed (verify --enforce)", ts, integ)
            out["status"] = "INVALIDATED"
        return out

    # ---------------------------------------------------------------- evaluate --
    def evaluate(self, ftid: str, *, ts: Optional[str] = None) -> Dict:
        """Evaluate the forward observations against the locked reference + thresholds and
        record the terminal decision (PASSED | FAILED | EXTEND)."""
        from .evaluate import evaluate_forward
        ts = ts or _now_iso()
        manifest = self.store.load_manifest(ftid)
        obs = self.store.read_observations(ftid)
        metrics = metrics_from_observations(obs, manifest["timeframe"])
        ref = manifest.get("reference_metrics") \
            or (manifest.get("backtest_reference") or {}).get("reference_metrics")
        report = evaluate_forward(manifest, metrics, ref)
        self.store.write_evaluation(ftid, report)
        if manifest["status"] not in _TERMINAL:
            self.store.set_status(manifest, report["decision"],
                                  "forward evaluation: " + report["decision"], ts,
                                  {"reasons": report["reasons"]})
        return report

    # ------------------------------------------------------- status / list / etc --
    def status(self, ftid: str) -> Dict:
        manifest = self.store.load_manifest(ftid)
        events = self.store.read_events(ftid)
        obs = self.store.read_observations(ftid)
        last_obs = obs[-1] if obs else None
        return {
            "forward_test_id": ftid, "strategy_id": manifest["strategy_id"],
            "strategy_version": manifest["strategy_version"], "status": manifest["status"],
            "start_date": manifest["start_date"],
            "earliest_eval_date": manifest["earliest_eval_date"],
            "assets": manifest["assets"], "timeframe": manifest["timeframe"],
            "observations": len(obs),
            "last_observation_utc": last_obs["datetime_utc"] if last_obs else None,
            "events": len(events), "thresholds": manifest["thresholds"],
        }

    def list_tests(self) -> List[Dict]:
        return self.store.list_tests()

    def complete(self, ftid: str, *, ts: Optional[str] = None) -> Dict:
        ts = ts or _now_iso()
        manifest = self.store.load_manifest(ftid)
        if manifest["status"] in _TERMINAL:
            return manifest
        return self.store.set_status(manifest, "COMPLETED",
                                     "forward data collection stopped", ts)

    def cancel(self, ftid: str, *, reason: str = "cancelled by user",
               ts: Optional[str] = None) -> Dict:
        ts = ts or _now_iso()
        manifest = self.store.load_manifest(ftid)
        if manifest["status"] in _TERMINAL:
            return manifest
        return self.store.set_status(manifest, "CANCELLED", reason, ts)
