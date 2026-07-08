"""SyntheticDataSource — deterministic, labelled NON-REAL data.

`is_real = False`. This exists ONLY to (a) unit-test the pipeline and (b) run
methodology demonstrations. The engine will refuse to write a scientific verdict
into a dossier from synthetic data — it can compute the numbers, but they are
marked ILLUSTRATIVE and never treated as evidence about a real strategy.

The generator embeds explicit regimes (trend -> range -> crisis -> recovery) so
the pipeline's regime-conditional machinery has real structure to exercise.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import CANONICAL_COLUMNS, DataRequest, DataSource

_TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class SyntheticDataSource(DataSource):
    name = "synthetic"
    is_real = False

    def __init__(self, seed: int = 0, anchor_ms: int = 1_600_000_000_000):
        self.seed = seed
        self.anchor_ms = anchor_ms  # fixed anchor => no wall-clock => reproducible

    def _fetch(self, req: DataRequest) -> pd.DataFrame:
        step = _TF_MS.get(req.timeframe, _TF_MS["1d"])
        n = int(req.limit or 1500)
        # Seed is deterministic in (asset, timeframe, seed): no Math.random, no clock.
        s = (abs(hash((req.asset, req.timeframe))) % (2**31)) ^ (self.seed * 2654435761 & 0x7FFFFFFF)
        rng = np.random.default_rng(s)
        base = 60000.0 if req.asset.upper().startswith("BTC") else 100.0

        # Four contiguous regimes of equal length: trend, range, crisis, recovery.
        seg = max(1, n // 4)
        mus, sigmas = [], []
        specs = [(0.0010, 0.010), (0.0000, 0.008), (-0.0025, 0.035), (0.0008, 0.012)]
        for k in range(n):
            mu, sig = specs[min(k // seg, 3)]
            mus.append(mu)
            sigmas.append(sig)
        rets = rng.normal(np.array(mus), np.array(sigmas))
        close = base * np.cumprod(1.0 + rets)
        open_ = np.empty(n)
        open_[0] = base
        open_[1:] = close[:-1]
        hi_noise = np.abs(rng.normal(0, np.array(sigmas))) * 0.5
        lo_noise = np.abs(rng.normal(0, np.array(sigmas))) * 0.5
        high = np.maximum(open_, close) * (1 + hi_noise)
        low = np.minimum(open_, close) * (1 - lo_noise)
        vol = (1000 if base > 1000 else 1e6) * (1 + np.abs(rets) / np.array(sigmas))
        times = self.anchor_ms + np.arange(n) * step
        df = pd.DataFrame(
            {"open_time": times.astype("int64"), "open": open_, "high": high,
             "low": low, "close": close, "volume": vol}
        )
        return df[CANONICAL_COLUMNS]
