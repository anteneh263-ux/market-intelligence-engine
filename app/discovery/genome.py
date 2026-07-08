"""Genome — a generated strategy as a frozen, hashed recipe of building blocks.

A Recipe = (composition rule, blocks[, gate]). It is content-addressed: its hash
is computed BEFORE testing, so every generated hypothesis is pre-registered. A
Recipe materialises into `GeneratedStrategy`, which implements the standard
`Strategy` interface and therefore runs through the unchanged frozen pipeline.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from app.strategy_research.strategies.base import Strategy
from .blocks import BLOCKS


@dataclass(frozen=True)
class Recipe:
    rule: str                                  # "VOTE" | "FILTER"
    blocks: Tuple[str, ...]                     # directional block names
    gate: str = ""                             # optional filter/gate block (FILTER rule)
    params: Tuple = ()                          # reserved (defaults used for now)

    def hash(self) -> str:
        payload = json.dumps({"rule": self.rule, "blocks": sorted(self.blocks),
                              "gate": self.gate}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def label(self) -> str:
        core = "+".join(self.blocks)
        return f"{self.rule}({core}{'|' + self.gate if self.gate else ''})"

    def to_dict(self) -> Dict:
        return {"rule": self.rule, "blocks": list(self.blocks), "gate": self.gate,
                "hash": self.hash(), "label": self.label()}

    @staticmethod
    def from_dict(d: Dict) -> "Recipe":
        return Recipe(rule=d["rule"], blocks=tuple(d["blocks"]), gate=d.get("gate", ""))


class GeneratedStrategy(Strategy):
    strategy_class = "generated"
    direction = "long_short"

    def __init__(self, recipe: Recipe, strategy_id: str):
        self.recipe = recipe
        self.strategy_id = strategy_id
        self.name = recipe.label()
        self.default_params = {}

    def positions(self, df: pd.DataFrame, params: Dict | None = None) -> pd.Series:
        sigs = [BLOCKS[b].signal(df) for b in self.recipe.blocks]
        if self.recipe.rule == "VOTE":
            combined = np.sign(np.sum(sigs, axis=0))
            raw = pd.Series(combined, index=df.index)
        elif self.recipe.rule == "FILTER":
            primary = sigs[0]
            g = BLOCKS[self.recipe.gate]
            gate = g.signal(df)
            if g.is_gate:                       # ADX-style permit gate
                raw = primary.where(gate > 0, 0.0)
            else:                               # confirmer: keep primary only when it agrees
                raw = primary.where(np.sign(primary) == np.sign(gate), 0.0)
        else:
            raise ValueError(f"Unknown composition rule {self.recipe.rule}")
        # carry forward: hold last non-zero position (entries persist until reversal)
        return self._carry_forward(raw.replace(0.0, np.nan))

    def param_grid(self) -> List[Dict]:
        return [{}]                              # blocks use frozen defaults (small snoop budget)


def materialize(recipe: Recipe, strategy_id: str) -> GeneratedStrategy:
    return GeneratedStrategy(recipe, strategy_id)
