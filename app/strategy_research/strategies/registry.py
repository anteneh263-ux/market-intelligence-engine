"""Strategy registry — resolve a Strategy by its dossier id (H-0xx)."""
from __future__ import annotations

from typing import Dict, List, Type

from .base import Strategy
from .implementations import (
    BarUpDnStrategy,
    ChannelBreakOutStrategy,
    MACDStrategy,
    RSIStrategy,
)
from .implementations_ext import EXT_STRATEGIES

_REGISTRY: Dict[str, Type[Strategy]] = {
    s.strategy_id: s
    for s in (BarUpDnStrategy, ChannelBreakOutStrategy, MACDStrategy, RSIStrategy,
              *EXT_STRATEGIES)
}


def available_strategies() -> List[str]:
    return sorted(_REGISTRY)


def get_strategy(strategy_id: str) -> Strategy:
    key = strategy_id.upper()
    if key not in _REGISTRY:
        raise KeyError(f"Unknown strategy '{strategy_id}'. Have: {available_strategies()}")
    val = _REGISTRY[key]
    # A registry entry is either a Strategy subclass (instantiate) or an already
    # built Strategy instance (generated strategies carry a recipe → registered live).
    return val() if isinstance(val, type) else val


def register_strategy(obj) -> None:
    """Register a Strategy subclass OR a pre-built Strategy instance (used by the
    Discovery Engine for generated G-#### strategies)."""
    _REGISTRY[obj.strategy_id] = obj
