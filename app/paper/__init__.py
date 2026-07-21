"""Paper-trading module (paper-trading-v1).

A SAFE, research-only simulator: it places NO real orders, uses NO API keys and
NO real money. It fetches only COMPLETED public candles, computes each strategy's
signal at candle close, and fills a *virtual* trade at the NEXT candle's open with
fees + slippage. State is fully separate from the knowledge base and its verdicts.

Public surface:
    PaperEngine  -- init / update / status / leaderboard / stop
"""
from .engine import PaperEngine
from .schema import SCHEMA_VERSION

__all__ = ["PaperEngine", "SCHEMA_VERSION"]
