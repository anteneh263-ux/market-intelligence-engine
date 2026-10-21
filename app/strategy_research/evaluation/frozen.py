"""The frozen evaluation matrix — identical for every strategy.

This object defines the ONE pipeline every strategy passes through: the same
cost levels, the same slippage levels, the same walk-forward procedure, the same
benchmarks, the same statistical thresholds. Freezing it (and hashing it into
each result) is what makes conclusions comparable across strategies and auditable
after the fact.

Nothing here may be tuned per-strategy or after seeing results. Changing the
matrix changes its hash, which is recorded — so any alteration is visible in the
provenance trail rather than silent.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import List


@dataclass(frozen=True)
class FrozenMatrix:
    # Cost/slippage stress ladder (basis points per unit turnover), applied to ALL.
    cost_bps_levels: tuple = (0.0, 5.0, 10.0)
    slippage_bps_levels: tuple = (0.0, 5.0, 10.0)
    # Walk-forward.
    n_splits: int = 5
    min_train: int = 100
    # Regime labelling.
    regime_window: int = 20
    regime_crisis_q: float = 0.80
    # Random-timing benchmark.
    random_seeds: int = 25
    random_base_seed: int = 12345
    # Statistics.
    dsr_threshold: float = 0.95          # deflated-Sharpe bar for a credible edge
    bh_q: float = 0.05                   # BH-FDR level across the universe
    bootstrap_B: int = 2000
    bootstrap_seed: int = 7
    # Robustness gates (Graveyard).
    min_trades: int = 30
    max_drawdown_gate: float = 0.60      # OOS maxDD above this => fragility flag
    # The "headline" cost scenario used for the primary verdict (realistic, not free).
    headline_cost_bps: float = 5.0
    headline_slippage_bps: float = 5.0
    version: str = "srp-matrix-v1"

    @property
    def pooling(self) -> str:
        """Multi-asset headline aggregation method — DERIVED from `version`, so it
        is NOT part of the hashed field set and does not change v1's hash.
          v1 -> 'concatenation' (stitch all assets' OOS returns; drawdown is an artifact)
       v2/v3 -> 'per_asset'     (per-asset metrics, then equal-weight aggregate; the
                                  drawdown gate uses the mean per-asset OOS drawdown)"""
        return "per_asset" if (self.version.endswith("v2") or self.version == "srp-matrix-v3") else "concatenation"

    @property
    def random_timing_method(self) -> str:
        """Derived from version; adding v3 must not change legacy serialized fields."""
        return "executed-runs-v1" if self.version == "srp-matrix-v3" else "bernoulli-redraw-v1"

    def canonical_json(self) -> str:
        # asdict() includes only dataclass FIELDS (not the `pooling` property), so
        # adding v2 does not alter v1's canonical form or hash.
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()[:16]


# v1: original matrix used for the first four strategies (concatenation pooling).
DEFAULT_MATRIX = FrozenMatrix()
MATRIX_V1 = DEFAULT_MATRIX
# v2: identical matrix, per-asset-then-aggregate drawdown pooling (the only change).
MATRIX_V2 = FrozenMatrix(version="srp-matrix-v2")
# v3: v2 pooling, exact engine trade-count benchmark. Explicit opt-in; no migration.
MATRIX_V3 = FrozenMatrix(version="srp-matrix-v3")


@dataclass(frozen=True)
class EvalRequest:
    """A fully-specified evaluation: which strategy, which data. Hashable into
    provenance so a verdict can be reproduced byte-for-byte."""

    strategy_id: str
    provider: str
    assets: List[str]
    timeframe: str
    start: str | None = None
    end: str | None = None
    limit: int | None = None
    matrix_version: str = DEFAULT_MATRIX.version

    def hash(self) -> str:
        payload = json.dumps(
            {"s": self.strategy_id, "p": self.provider, "a": sorted(self.assets),
             "tf": self.timeframe, "start": self.start, "end": self.end,
             "limit": self.limit, "m": self.matrix_version},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
