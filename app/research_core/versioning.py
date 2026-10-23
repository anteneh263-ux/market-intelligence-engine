"""Research Core — frozen-matrix version management.

Central authority for evaluation-matrix versions. Each version is immutable and
content-addressed; results are tagged with the version that produced them. Older
versions are never edited — a new method is a new version.

  srp-matrix-v1  concatenation pooling (historical; first four strategies)
  srp-matrix-v2  per-asset-then-aggregate pooling (explicit compatibility selection)
  srp-matrix-v3  per-asset pooling + exact trade-count benchmark (explicit opt-in)
  srp-matrix-v4  v3 + separate asset range and sampling uncertainty (default for new Core evaluations)
  srp-matrix-v5  v4 thresholds evaluated as one equal-weight portfolio (explicit opt-in, evaluation only)
"""
from __future__ import annotations

from typing import Dict, List

from app.strategy_research.evaluation.frozen import MATRIX_V1, MATRIX_V2, MATRIX_V3, MATRIX_V4, MATRIX_V5, FrozenMatrix

_MATRICES: Dict[str, FrozenMatrix] = {
    MATRIX_V1.version: MATRIX_V1,
    MATRIX_V2.version: MATRIX_V2,
    MATRIX_V3.version: MATRIX_V3,
    MATRIX_V4.version: MATRIX_V4,
    MATRIX_V5.version: MATRIX_V5,
}
_ALIASES = {"v1": MATRIX_V1.version, "v2": MATRIX_V2.version, "v3": MATRIX_V3.version, "v4": MATRIX_V4.version, "v5": MATRIX_V5.version, "latest": MATRIX_V4.version}
ACTIVE_VERSION = MATRIX_V4.version


def resolve(version: str | None) -> FrozenMatrix:
    if not version:
        return _MATRICES[ACTIVE_VERSION]
    key = _ALIASES.get(version, version)
    if key not in _MATRICES:
        raise KeyError(f"Unknown matrix '{version}'. Known: {list(_MATRICES)} + aliases {list(_ALIASES)}")
    return _MATRICES[key]


def list_versions() -> List[Dict]:
    return [{"version": m.version, "hash": m.hash(), "pooling": m.pooling,
             "active": m.version == ACTIVE_VERSION} for m in _MATRICES.values()]


def register_matrix(matrix: FrozenMatrix, make_active: bool = False) -> None:
    """Add a new frozen matrix version (e.g. a future v3). Never mutates existing."""
    global ACTIVE_VERSION
    _MATRICES[matrix.version] = matrix
    if make_active:
        ACTIVE_VERSION = matrix.version
