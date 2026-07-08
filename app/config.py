"""Application configuration loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./market_intelligence.db"
    data_source: str = "binance"  # binance | ccxt | synthetic
    binance_base_url: str = "https://api.binance.com"

    # Strategy Research module — pluggable data providers (STEP 5).
    sr_provider: str = "csv"      # csv | binance | fmp | coindesk | synthetic
    sr_csv_root: str = "data_csv"
    fmp_api_key: str = ""         # set in env to enable the FMP provider
    coindesk_api_key: str = ""    # set in env to enable the CoinDesk provider

    fee_pct: float = 0.001
    position_size: float = 1.0

    frontend_origin: str = "http://localhost:5173"

    # Rejection thresholds for the Strategy Graveyard
    reject_sharpe_below: float = 0.5
    reject_drawdown_above: float = 0.25
    reject_min_trades: int = 30
    reject_profit_factor_below: float = 1.1


settings = Settings()

# Assets and timeframes supported by the MVP
ASSETS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAMES = ["1h", "4h"]

DISCLAIMER = (
    "This platform is for research only. It does not provide financial "
    "advice and does not execute trades."
)
