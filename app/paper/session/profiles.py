"""Frozen, per-instrument execution profiles for Phase-4 paper trading.

Crypto spot, equities and gold do NOT share execution assumptions, so each instrument has
its own frozen profile: fee model, spread, slippage, market-hours policy, tick / quantity
rounding, minimum order size, funding (perpetual crypto only) and overnight-gap expectation.
A profile is canonical-JSON hashable, so profile drift after a session starts is detectable
via ``execution_profile_hash``.

All performance is computed AFTER these costs (see broker.py). Nothing here connects to a
broker or venue — the numbers are simulation parameters only.
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

from app.forward.manifest import canonical_json, sha256_text

PROFILE_VERSION = "exec-profile-v1"

# instrument-type taxonomy (execution assumptions differ by type, never silently shared)
CRYPTO_SPOT = "crypto_spot"
CRYPTO_PERP = "crypto_perp"
EQUITY = "equity"
METAL_SPOT = "metal_spot"

# market-hours policies
MH_24x7 = "24x7"                 # continuous (crypto)
MH_EXCHANGE_WEEKDAY = "exchange_weekday"   # equities: Mon-Fri, overnight + weekend gaps
MH_METAL_24x5 = "metal_24x5"     # spot gold: ~24h Mon-Fri, weekend gap

# fee models
FEE_BPS = "bps_on_notional"        # crypto / metal: maker/taker bps on notional
FEE_PER_SHARE = "per_share_commission"  # equities: commission per share with a floor


def _finite_pos(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def _finite_nonneg(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


# --------------------------------------------------------------------------- #
# frozen default profiles (deliberately DIFFERENT across asset classes)       #
# --------------------------------------------------------------------------- #
_DEFAULTS: Dict[str, Dict] = {
    "BTC/USD": {
        "instrument": "BTC/USD",
        "instrument_type": CRYPTO_SPOT,
        "fee_model": FEE_BPS,
        "maker_fee_bps": 2.0,
        "taker_fee_bps": 10.0,
        "commission_per_unit": 0.0,
        "min_commission": 0.0,
        "half_spread_bps": 1.0,
        "slippage_bps": 5.0,
        "market_hours": MH_24x7,
        "tick_size": 0.01,
        "qty_step": 1e-6,
        "min_qty": 1e-5,
        "min_notional": 1.0,
        "funding": None,                 # spot: no funding
        "overnight_gaps_expected": False,
        "max_fill_qty": None,   # per-order fill cap (None = unlimited); models limited liquidity
    },
    "ETH/USD": {
        "instrument": "ETH/USD",
        "instrument_type": CRYPTO_SPOT,
        "fee_model": FEE_BPS,
        "maker_fee_bps": 2.0,
        "taker_fee_bps": 10.0,
        "commission_per_unit": 0.0,
        "min_commission": 0.0,
        "half_spread_bps": 1.5,
        "slippage_bps": 6.0,
        "market_hours": MH_24x7,
        "tick_size": 0.01,
        "qty_step": 1e-5,
        "min_qty": 1e-4,
        "min_notional": 1.0,
        "funding": None,
        "overnight_gaps_expected": False,
        "max_fill_qty": None,   # per-order fill cap (None = unlimited); models limited liquidity
    },
    "SPY": {
        "instrument": "SPY",
        "instrument_type": EQUITY,
        "fee_model": FEE_PER_SHARE,
        "maker_fee_bps": 0.0,
        "taker_fee_bps": 0.0,
        "commission_per_unit": 0.005,    # $/share
        "min_commission": 1.0,           # $ floor per order
        "half_spread_bps": 0.5,
        "slippage_bps": 2.0,
        "market_hours": MH_EXCHANGE_WEEKDAY,
        "tick_size": 0.01,
        "qty_step": 1.0,                 # whole shares
        "min_qty": 1.0,
        "min_notional": 0.0,
        "funding": None,
        "overnight_gaps_expected": True,
        "max_fill_qty": None,   # per-order fill cap (None = unlimited); models limited liquidity
    },
    "XAU/USD": {
        "instrument": "XAU/USD",
        "instrument_type": METAL_SPOT,
        "fee_model": FEE_BPS,
        "maker_fee_bps": 0.0,
        "taker_fee_bps": 0.0,
        "commission_per_unit": 0.0,
        "min_commission": 0.0,
        "half_spread_bps": 3.0,          # gold spread wider than large-cap equity
        "slippage_bps": 3.0,
        "market_hours": MH_METAL_24x5,
        "tick_size": 0.01,
        "qty_step": 0.01,                # ounces
        "min_qty": 0.01,
        "min_notional": 0.0,
        "funding": None,                 # NOT a perpetual; overnight financing NOT modelled
        "overnight_gaps_expected": True,
        "max_fill_qty": None,   # per-order fill cap (None = unlimited); models limited liquidity
    },
}

SUPPORTED_INSTRUMENTS: Tuple[str, ...] = tuple(_DEFAULTS.keys())

_REQUIRED_FIELDS: Tuple[str, ...] = (
    "instrument", "instrument_type", "fee_model", "maker_fee_bps", "taker_fee_bps",
    "commission_per_unit", "min_commission", "half_spread_bps", "slippage_bps",
    "market_hours", "tick_size", "qty_step", "min_qty", "min_notional",
    "overnight_gaps_expected",
)


def freeze_execution_profile(instrument: str, **overrides) -> Dict:
    """Return a frozen, validated execution profile for one instrument. Overrides are allowed
    at freeze time (e.g. a stress scenario) but the result is then immutable and hashed."""
    if instrument not in _DEFAULTS:
        raise ValueError(f"no execution profile for {instrument!r}; "
                         f"supported: {list(SUPPORTED_INSTRUMENTS)}")
    prof = dict(_DEFAULTS[instrument])
    prof.update(overrides)
    prof["profile_version"] = PROFILE_VERSION
    ok, why = validate_execution_profile(prof)
    if not ok:
        raise ValueError(f"invalid execution profile for {instrument!r}: {why}")
    return prof


def validate_execution_profile(profile) -> Tuple[bool, str]:
    """Structural validation of an execution profile. Returns (ok, reason)."""
    if not isinstance(profile, dict):
        return False, "profile is not a dict"
    if profile.get("profile_version") != PROFILE_VERSION:
        return False, "unsupported profile_version"
    for f in _REQUIRED_FIELDS:
        if f not in profile:
            return False, f"missing field {f!r}"
    if profile["instrument_type"] not in (CRYPTO_SPOT, CRYPTO_PERP, EQUITY, METAL_SPOT):
        return False, "unknown instrument_type"
    if profile["fee_model"] not in (FEE_BPS, FEE_PER_SHARE):
        return False, "unknown fee_model"
    if profile["market_hours"] not in (MH_24x7, MH_EXCHANGE_WEEKDAY, MH_METAL_24x5):
        return False, "unknown market_hours policy"
    for f in ("maker_fee_bps", "taker_fee_bps", "commission_per_unit", "min_commission",
              "half_spread_bps", "slippage_bps", "min_notional"):
        if not _finite_nonneg(profile[f]):
            return False, f"{f} must be a finite number >= 0"
    for f in ("tick_size", "qty_step", "min_qty"):
        if not _finite_pos(profile[f]):
            return False, f"{f} must be a finite number > 0"
    if not isinstance(profile["overnight_gaps_expected"], bool):
        return False, "overnight_gaps_expected must be a bool"
    mfq = profile.get("max_fill_qty")
    if mfq is not None and not _finite_pos(mfq):
        return False, "max_fill_qty must be None or a finite number > 0"
    funding = profile.get("funding")
    if funding is not None:
        # funding only makes sense for a perpetual; validate its shape if present
        if profile["instrument_type"] != CRYPTO_PERP:
            return False, "funding is only valid for a crypto_perp instrument"
        if not (isinstance(funding, dict) and _finite_nonneg(funding.get("funding_interval_ms"))
                and isinstance(funding.get("funding_rate_bps"), (int, float))):
            return False, "malformed funding block"
    return True, ""


def execution_profile_hash(profile: Dict) -> str:
    """Deterministic hash of the frozen execution profile (drift-detectable)."""
    return sha256_text(canonical_json(profile))


def is_trading_time(profile: Dict, open_time_ms: int) -> bool:
    """Whether a bar starting at ``open_time_ms`` (UTC epoch ms) falls in the instrument's
    trading calendar, at day granularity. Crypto is 24x7; equities trade Mon-Fri; spot gold
    trades ~24x5 (closed Saturday, and Sunday before the session open — modelled here simply
    as closed on Saturday/Sunday at daily granularity)."""
    mh = profile["market_hours"]
    if mh == MH_24x7:
        return True
    # UTC weekday: Monday=0 ... Sunday=6, computed from epoch-day without a wall clock.
    # 1970-01-01 (epoch day 0) was a Thursday (Python weekday 3), so weekday = (day + 3) % 7.
    epoch_day = open_time_ms // 86_400_000
    weekday = (epoch_day + 3) % 7
    if mh == MH_EXCHANGE_WEEKDAY:
        return weekday <= 4
    if mh == MH_METAL_24x5:
        return weekday <= 4  # Mon-Fri at daily granularity
    return False
