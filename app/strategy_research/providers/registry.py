"""Provider registry — resolve a provider by name.

This is the single seam the rest of the engine uses to obtain data. Swapping
providers is a one-word change (or one env var); no evaluation code changes.
"""
from __future__ import annotations

from typing import Callable, Dict

from .base import DataSource
from .csv_source import CSVDataSource
from .http_sources import BinanceDataSource, CoinDeskDataSource, FMPDataSource
from .synthetic_source import SyntheticDataSource

_FACTORIES: Dict[str, Callable[..., DataSource]] = {
    "csv": CSVDataSource,
    "binance": BinanceDataSource,
    "fmp": FMPDataSource,
    "coindesk": CoinDeskDataSource,
    "synthetic": SyntheticDataSource,
}


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def get_provider(name: str, **kwargs) -> DataSource:
    key = (name or "csv").lower()
    if key not in _FACTORIES:
        raise KeyError(f"Unknown provider '{name}'. Available: {available_providers()}")
    return _FACTORIES[key](**kwargs)


def register_provider(name: str, factory: Callable[..., DataSource]) -> None:
    """Extension hook: register a new vendor without editing this file."""
    _FACTORIES[name.lower()] = factory
