"""Paper-trading engine (paper-trading-v1) -- SAFE, research-only.

NO real orders, NO API keys, NO real money. Fetches only COMPLETED public candles,
computes each strategy's signal at candle close, and fills a VIRTUAL trade at the
NEXT candle's OPEN with fee + slippage. Long-only strategies never short. Exposure is
capped at 1x AT EXECUTION (sizing time); later price moves can push gross exposure above
1x -- this is surfaced as a warning in `status`, never silently. A candle is never
processed twice (watermark). State is fully separate from the knowledge base.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.strategy_research.providers import DataRequest, get_provider
from app.strategy_research.strategies import available_strategies, get_strategy

from . import ledger
from .schema import (BUY_AND_HOLD_ID, SCHEMA_VERSION, drop_incomplete,
                     event_id, iso_utc, resolve_target, tf_ms)


class PaperDataError(RuntimeError):
    """Raised to SAFELY STOP an update on missing / duplicate / mis-sorted candles.
    The engine never mutates state when this is raised."""


DEFAULT_MARKETS = ["BTC/USD", "ETH/USD"]


# Documented, REJECTED hypotheses (Graveyard) recorded for evidence only. They are NOT
# active strategies: never paper-traded and never connected to a broker/live execution.
NON_ACTIVE_STRATEGIES = {"H-028"}   # Hull MA Slope Trend (Graveyard)

# Pre-registered hypotheses that are evaluation-only: excluded from the legacy paper
# universe until a verdict and holdout explicitly authorise any forward stage.
EVALUATION_ONLY_STRATEGIES = {"H-030"}   # docs/H-030_PREREGISTRATION.md (srp-matrix-v5)


def strategy_ids() -> List[str]:
    return [s for s in available_strategies()
            if s.startswith("H-") and s not in NON_ACTIVE_STRATEGIES
            and s not in EVALUATION_ONLY_STRATEGIES]


class PaperEngine:
    def __init__(self, root: str = "paper_trading", provider: str = "coindesk",
                 provider_kwargs: Optional[Dict] = None, timeframe: str = "1d",
                 data_source=None):
        self.root = root
        self.log_dir = os.path.join(root, "log")
        self.config_path = os.path.join(root, "config.json")
        self.state_path = os.path.join(root, "state.json")
        self.provider_name = provider
        self.provider_kwargs = provider_kwargs or {}
        self.timeframe = timeframe
        self._injected = data_source          # dependency injection for offline tests

    # ---------------- provider ---------------- #
    def _source(self):
        return self._injected or get_provider(self.provider_name, **self.provider_kwargs)

    def _fetch_completed(self, market: str, limit: int) -> pd.DataFrame:
        """Fetch a trailing window and DROP the still-forming current candle so only
        COMPLETED candles are used. Uses wall-clock only for the completeness cutoff
        (never in the accounting core)."""
        src = self._source()
        frame = src.get(DataRequest(asset=market, timeframe=self.timeframe, limit=limit))
        df = frame.df.copy()
        if df.empty:
            return df
        step = tf_ms(self.timeframe)
        now_ms = int(time.time() * 1000)
        # a candle is complete once its close time (open_time + step) is at/in the past
        return drop_incomplete(df, step, now_ms)

    # ---------------- config / state I/O ---------------- #
    def _load_config(self) -> Dict:
        with open(self.config_path) as f:
            return json.load(f)

    def _atomic_write(self, path: str, obj: Dict) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)

    def _load_state(self, config: Dict) -> Dict:
        """Load state.json; if missing, RECONSTRUCT every account from its append-only
        JSONL log (the log is the source of truth)."""
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                return json.load(f)
        accounts: Dict[str, Dict] = {}
        for market in config["markets"]:
            for sid in strategy_ids() + [BUY_AND_HOLD_ID]:
                rec = ledger.reconstruct_account(self.log_dir, sid, market)
                if rec is not None:
                    accounts[ledger.account_key(sid, market)] = {
                        "cash": rec["cash"], "total_units": rec["total_units"],
                        "position": rec["position"], "pending_signal": rec["pending_signal"],
                        "pending_signal_time_ms": rec["pending_signal_time_ms"],
                        "watermark_open_ms": rec["watermark_open_ms"],
                        "last_close": rec["last_close"], "warmup_done": True,
                    }
        return {"schema_version": SCHEMA_VERSION, "accounts": accounts,
                "reconstructed_from_log": True}

    def _save_state(self, state: Dict) -> None:
        self._atomic_write(self.state_path, state)

    # ---------------- validation (safe stop) ---------------- #
    def _new_candles(self, df: pd.DataFrame, watermark_ms: Optional[int]) -> pd.DataFrame:
        """Return the candles to process (open_time > watermark), after validating the
        WHOLE frame is strictly ascending & unique and the new run is CONTIGUOUS with
        no gaps. Raises PaperDataError (safe stop) on any anomaly."""
        if df.empty:
            return df
        ot = df["open_time"].astype("int64").to_numpy()
        # global: strictly ascending & unique (mis-sorted / duplicate -> safe stop)
        d = np.diff(ot)
        if np.any(d <= 0):
            raise PaperDataError("candles are duplicated or mis-sorted (open_time not strictly ascending)")
        step = tf_ms(self.timeframe)
        if watermark_ms is None:
            return df                                   # init warm-up: no trading yet
        new = df[df["open_time"].astype("int64") > int(watermark_ms)].reset_index(drop=True)
        if new.empty:
            return new
        nt = new["open_time"].astype("int64").to_numpy()
        # first new candle must be exactly one step after the watermark (no missing candle)
        if nt[0] != int(watermark_ms) + step:
            raise PaperDataError(
                f"missing candle(s): watermark={iso_utc(watermark_ms)} "
                f"next available={iso_utc(int(nt[0]))} (gap != one {self.timeframe} step)")
        # contiguity within the new run
        if np.any(np.diff(nt) != step):
            raise PaperDataError("missing candle(s): gap inside the new candle run")
        return new

    # ---------------- core accounting ---------------- #
    def _fill_and_record(self, state_acc, direction, hist, idx, signals, market,
                         sid, source_name, cfg) -> Dict:
        """Process ONE candle for ONE account: fill the pending signal at this candle's
        OPEN, mark at CLOSE, compute the next pending signal, append a ledger event,
        and return the updated account dict."""
        step = tf_ms(self.timeframe)
        fee_rate = cfg["fee_bps"] / 10_000.0
        slip_rate = cfg["slippage_bps"] / 10_000.0
        row = hist.iloc[idx]
        open_ms = int(row["open_time"]); raw_open = float(row["open"]); close = float(row["close"])

        prev_pos = int(state_acc["position"])
        units = float(state_acc["total_units"])
        cash = float(state_acc["cash"])
        pend = int(state_acc["pending_signal"])
        pend_time = state_acc["pending_signal_time_ms"]
        equity_pre = cash + units * raw_open           # pre-trade equity (1x sizing base)

        traded = False; fill_price = None; traded_units = 0.0; notional = 0.0
        fee = 0.0; slippage_cost = 0.0; new_pos = prev_pos

        # ---- fill pending signal at THIS candle's open (only if target changes) ----
        if pend != prev_pos:
            units_before = units
            direction_sign = 1.0 if pend > prev_pos else -1.0          # buy vs sell
            fill_price = raw_open * (1.0 + direction_sign * slip_rate)  # adverse slippage
            equity_at_open = cash + units_before * raw_open             # 1x sizing base
            raw_target = pend * equity_at_open / fill_price
            # reserve the fee so gross exposure stays <= 1x of pre-trade equity.
            fee_est = abs(raw_target - units_before) * fill_price * fee_rate
            target_units = pend * (equity_at_open - fee_est) / fill_price
            traded_units = target_units - units_before
            fee = abs(traded_units) * fill_price * fee_rate
            cash_new = cash - traded_units * fill_price - fee
            # HARD GUARD: never let cash go negative (=> gross exposure strictly <= 1x
            # equity, on reversals too). Trim the target toward flat by the overshoot.
            if cash_new < 0.0:
                trim = (-cash_new) / fill_price
                target_units = (target_units - trim) if target_units > 0 else (target_units + trim)
                traded_units = target_units - units_before
                fee = abs(traded_units) * fill_price * fee_rate
                cash_new = cash - traded_units * fill_price - fee
            notional = abs(traded_units) * fill_price
            slippage_cost = abs(traded_units) * raw_open * slip_rate
            cash = cash_new
            units = target_units
            new_pos = pend
            traded = True

        # ---- mark-to-market at close ----
        equity = cash + units * close

        # ---- next pending signal computed at THIS candle's close ----
        raw_sig = 1.0 if sid == BUY_AND_HOLD_ID else float(signals[idx])
        next_pending = resolve_target(direction, raw_sig)
        next_pending_time = open_ms + step                             # close time (UTC)

        ev = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id(sid, market, open_ms),
            "strategy_id": sid, "market": market, "data_source": source_name,
            "timeframe": self.timeframe,
            "candle_open_time_ms": open_ms, "candle_open_time_utc": iso_utc(open_ms),
            "signal_time_ms": pend_time,
            "signal_time_utc": iso_utc(pend_time) if pend_time is not None else None,
            "execution_time_ms": open_ms, "execution_time_utc": iso_utc(open_ms),
            "traded": traded,
            "raw_open": raw_open, "fill_price": fill_price,
            "prev_position": prev_pos, "new_position": new_pos,
            "traded_units": traded_units, "total_units": units,
            "notional": notional, "fee": fee, "slippage": slippage_cost,
            "cash": cash, "equity": equity, "equity_pre": equity_pre, "close": close,
            "pending_signal": next_pending, "pending_signal_time_ms": next_pending_time,
            "pending_signal_time_utc": iso_utc(next_pending_time),
        }
        ledger.append_event(self.log_dir, sid, market, ev)

        state_acc.update({
            "cash": cash, "total_units": units, "position": new_pos,
            "pending_signal": next_pending, "pending_signal_time_ms": next_pending_time,
            "watermark_open_ms": open_ms, "last_close": close, "warmup_done": True,
        })
        return state_acc

    # ---------------- public commands ---------------- #
    def init(self, markets=None, capital: float = 10_000.0, fee_bps: float = 10.0,
             slippage_bps: float = 5.0, warmup: int = 300) -> Dict:
        """Create accounts and WARM UP indicators only -- NO historical trades. Sets each
        account's watermark to the last completed candle and seeds a pending signal to be
        filled on the next NEW candle's open."""
        markets = markets or DEFAULT_MARKETS
        if os.path.exists(self.state_path) or os.path.exists(self.config_path):
            raise RuntimeError("paper state already exists; remove paper_trading/ to re-init")
        cfg = {"schema_version": SCHEMA_VERSION, "markets": list(markets),
               "capital": float(capital), "fee_bps": float(fee_bps),
               "slippage_bps": float(slippage_bps), "timeframe": self.timeframe,
               "provider": self.provider_name, "running": True}
        state = {"schema_version": SCHEMA_VERSION, "accounts": {}}
        for market in markets:
            hist = self._fetch_completed(market, limit=max(warmup, 260))
            if hist.empty or len(hist) < 2:
                raise PaperDataError(f"no completed candles for {market} (provider returned too little data)")
            self._new_candles(hist, None)                    # validates ordering
            last_open = int(hist.iloc[-1]["open_time"])
            last_close = float(hist.iloc[-1]["close"])
            step = tf_ms(self.timeframe)
            src_name = getattr(self._source(), "name", self.provider_name)
            for sid in strategy_ids():
                strat = get_strategy(sid)
                sig = float(strat.positions(hist).iloc[-1])
                pend = resolve_target(strat.direction, sig)
                state["accounts"][ledger.account_key(sid, market)] = {
                    "cash": float(capital), "total_units": 0.0, "position": 0,
                    "pending_signal": pend, "pending_signal_time_ms": last_open + step,
                    "watermark_open_ms": last_open, "last_close": last_close,
                    "warmup_done": True, "direction": strat.direction, "data_source": src_name,
                }
            # buy-and-hold: same capital, seeded +1, filled on the same first new candle
            state["accounts"][ledger.account_key(BUY_AND_HOLD_ID, market)] = {
                "cash": float(capital), "total_units": 0.0, "position": 0,
                "pending_signal": 1, "pending_signal_time_ms": last_open + step,
                "watermark_open_ms": last_open, "last_close": last_close,
                "warmup_done": True, "direction": "long_only", "data_source": src_name,
            }
        # all markets built successfully (any missing/short market already raised above,
        # BEFORE anything was persisted -> a market is never silently dropped, and a
        # failed init leaves no config.json / state.json / partial accounts). Roll back
        # if either final write fails, so the two files are always consistent.
        src_name = getattr(self._source(), "name", self.provider_name)
        try:
            self._atomic_write(self.config_path, cfg)
            self._save_state(state)
        except Exception:
            for pth in (self.state_path, self.config_path):
                try:
                    if os.path.exists(pth):
                        os.remove(pth)
                except OSError:
                    pass
            raise
        return {"markets": list(markets), "data_source": src_name,
                "strategy_accounts": len(markets) * len(strategy_ids()),
                "benchmark_accounts": len(markets),
                "capital": float(capital),
                "note": "warm-up only; first trades occur on the next NEW candle"}

    def update(self) -> Dict:
        """Fetch completed candles and process any NEW ones (open_time > watermark).
        Idempotent (a candle is never processed twice). Safe-stops on missing /
        duplicate / mis-sorted candles WITHOUT mutating state."""
        cfg = self._load_config()
        if not cfg.get("running", True):
            return {"stopped": True, "note": "paper trading is stopped; run 'paper init' to restart a fresh session"}
        state = self._load_state(cfg)
        step = tf_ms(self.timeframe)
        summary = {"markets": {}, "processed": 0}
        # fetch + validate ALL markets first; if any market is anomalous, stop before mutating.
        plans = {}
        for market in cfg["markets"]:
            hist = self._fetch_completed(market, limit=max(cfg.get("warmup", 400), 400))
            if hist.empty:
                summary["markets"][market] = {"new": 0, "note": "no completed candles (missing data) -> safe no-op"}
                continue
            # watermark = min across this market's accounts (they advance together)
            wms = None
            for sid in strategy_ids() + [BUY_AND_HOLD_ID]:
                a = state["accounts"].get(ledger.account_key(sid, market))
                if a is not None:
                    wms = a["watermark_open_ms"] if wms is None else min(wms, a["watermark_open_ms"])
            new = self._new_candles(hist, wms)              # raises PaperDataError on anomaly
            plans[market] = (hist, new)

        for market, (hist, new) in plans.items():
            if new.empty:
                summary["markets"][market] = {"new": 0, "note": "up to date"}
                continue
            # map new-candle open_times to indices in the full history frame
            ot = hist["open_time"].astype("int64").to_numpy()
            first_idx = int(np.searchsorted(ot, int(new.iloc[0]["open_time"])))
            src_name = getattr(self._source(), "name", self.provider_name)
            for sid in strategy_ids() + [BUY_AND_HOLD_ID]:
                key = ledger.account_key(sid, market)
                acc = state["accounts"][key]
                direction = acc.get("direction", "long_short")
                signals = (np.ones(len(hist)) if sid == BUY_AND_HOLD_ID
                           else get_strategy(sid).positions(hist).to_numpy())
                for i in range(first_idx, len(hist)):
                    acc = self._fill_and_record(acc, direction, hist, i, signals,
                                                market, sid, src_name, cfg)
                state["accounts"][key] = acc
            summary["markets"][market] = {"new": int(len(new)),
                                          "from_utc": iso_utc(int(new.iloc[0]["open_time"])),
                                          "to_utc": iso_utc(int(new.iloc[-1]["open_time"]))}
            summary["processed"] += int(len(new))
        state["last_update_processed"] = summary["processed"]
        self._save_state(state)
        return summary

    def stop(self) -> Dict:
        cfg = self._load_config()
        cfg["running"] = False
        self._atomic_write(self.config_path, cfg)
        return {"running": False, "note": "paper trading stopped; 'paper update' will no-op until re-init"}

    def status(self) -> Dict:
        cfg = self._load_config()
        state = self._load_state(cfg)
        out = {"running": cfg.get("running", True), "data_source": cfg.get("provider"),
               "strategy_accounts": 0, "benchmark_accounts": 0,
               "exposure_warnings": [], "markets": {}}
        for market in cfg["markets"]:
            bh = state["accounts"].get(ledger.account_key(BUY_AND_HOLD_ID, market), {})
            rows = []
            for sid in strategy_ids():
                a = state["accounts"].get(ledger.account_key(sid, market))
                if not a:
                    continue
                px = a["last_close"]
                eq = a["cash"] + a["total_units"] * px
                gross = abs(a["total_units"]) * px
                lev = (gross / eq) if eq > 1e-9 else 0.0
                warn = lev > 1.0 + 1e-6          # gross exposure drifted above 1x AFTER execution
                rows.append({"strategy_id": sid, "position": a["position"],
                             "pending_signal": a["pending_signal"],
                             "cash": round(a["cash"], 2), "equity": round(eq, 2),
                             "gross_exposure": round(gross, 2),
                             "leverage_at_mark": round(lev, 4),
                             "exposure_warning": warn,
                             "last_candle_utc": iso_utc(a["watermark_open_ms"])})
                out["strategy_accounts"] += 1
                if warn:
                    out["exposure_warnings"].append({
                        "strategy_id": sid, "market": market,
                        "leverage_at_mark": round(lev, 4),
                        "note": "gross exposure > 1x equity from price drift AFTER execution; "
                                "no leverage was added at trade time"})
            if bh:
                out["benchmark_accounts"] += 1
            bh_eq = (bh.get("cash", cfg["capital"]) + bh.get("total_units", 0.0) * bh.get("last_close", 0.0)) if bh else cfg["capital"]
            out["markets"][market] = {"buy_and_hold_equity": round(bh_eq, 2), "accounts": rows}
        return out

    def leaderboard(self) -> Dict:
        st = self.status()
        board = {"markets": {}}
        for market, data in st["markets"].items():
            bh = data["buy_and_hold_equity"]
            ranked = sorted(data["accounts"], key=lambda r: r["equity"], reverse=True)
            board["markets"][market] = {
                "buy_and_hold_equity": bh,
                "ranking": [{"rank": i + 1, "strategy_id": r["strategy_id"],
                             "equity": r["equity"],
                             "vs_buy_hold": round(r["equity"] - bh, 2),
                             "beats_buy_hold": r["equity"] > bh}
                            for i, r in enumerate(ranked)],
            }
        return board
