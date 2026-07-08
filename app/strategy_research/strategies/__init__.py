"""Strategy implementations behind one Strategy interface."""
from .base import Strategy
from .registry import available_strategies, get_strategy, register_strategy

__all__ = ["Strategy", "get_strategy", "available_strategies", "register_strategy"]
