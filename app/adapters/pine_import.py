"""Pine Script importer — a SMALL adapter (deliberately not a full parser).

Maps a TradingView Pine strategy onto the platform's existing block vocabulary by
keyword heuristics, producing (a) a structured spec for the deterministic dossier
generator and (b) a runnable Recipe/Strategy. Anything it cannot recognise is
surfaced as an explicit WARNING/TODO — it never guesses silently.

MVP scope: recognises the common built-in indicators and AND/OR entry logic. For
unusual scripts it returns warnings and asks for a manual spec, rather than
pretending to understand. This is an adapter, not a compiler.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.discovery.blocks import BLOCKS
from app.discovery.genome import Recipe

# indicator keyword → block name (directional) ; adx handled as a gate
_INDICATOR_MAP = [
    (r"\bta\.rsi|\brsi\b", "rsi_rev", "RSI"),
    (r"bollinger|\bbb\b|ta\.stdev|basis\s*\+|upper\s*=.*stdev", "bb_rev", "Bollinger Bands"),
    (r"keltner", "keltner", "Keltner Channels"),
    (r"ta\.macd|\bmacd\b", "macd", "MACD"),
    (r"supertrend|super_trend", "supertrend", "Supertrend"),
    (r"ta\.sar|parabolic", "supertrend", "Parabolic SAR (mapped to trend block)"),
    (r"ta\.stoch|stochastic", "stoch_rev", "Stochastic"),
    (r"ta\.atr|\batr\b", "atr_expand", "ATR / volatility"),
    (r"highest\(|lowest\(|donchian|channel", "donchian", "Donchian channel"),
    (r"ta\.mom|momentum", "momentum", "Momentum"),
    (r"ta\.ema.*ta\.ema|fast.*slow|crossover\(.*ema", "ma_cross", "MA cross"),
    (r"ta\.sma|ta\.ema", "price_ma", "Moving average"),
]
_GATE_RE = r"ta\.adx|\badx\b|ta\.dmi"


@dataclass
class ImportedStrategy:
    name: str
    pine_hash: str
    detected: List[str] = field(default_factory=list)
    recipe: Optional[Recipe] = None
    family: str = "unknown"
    spec: Dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.recipe is not None


def _detect_params(text: str) -> Dict:
    p = {}
    for m in re.finditer(r"input(?:\.\w+)?\(\s*(\d+(?:\.\d+)?)[^)]*?['\"]([^'\"]+)['\"]", text):
        p[m.group(2).strip()] = m.group(1)
    for m in re.finditer(r"input(?:\.\w+)?\(\s*(\d+(?:\.\d+)?)\s*,\s*['\"]([^'\"]+)['\"]", text):
        p[m.group(2).strip()] = m.group(1)
    return p


def import_pine(pine_text: str, name: str = "") -> ImportedStrategy:
    text = pine_text.lower()
    h = hashlib.sha256(pine_text.encode()).hexdigest()[:8]
    if not name:
        m = re.search(r"strategy\(\s*['\"]([^'\"]+)['\"]", pine_text)
        name = m.group(1) if m else f"pine-{h}"

    detected, blocks, seen = [], [], set()
    for pat, block, label in _INDICATOR_MAP:
        if re.search(pat, text) and block not in seen:
            seen.add(block); blocks.append(block); detected.append(label)
    has_gate = bool(re.search(_GATE_RE, text))

    warnings: List[str] = []
    recipe: Optional[Recipe] = None
    if not blocks:
        warnings.append("TODO: no recognised indicator — supply a manual spec/recipe.")
    else:
        # AND (both conditions required) → FILTER (confirmation); OR → VOTE.
        connective = "and" if re.search(r"\band\b", text) and not re.search(r"\bor\b", text) else \
                     ("or" if re.search(r"\bor\b", text) else "single")
        if has_gate and len(blocks) >= 1:
            recipe = Recipe("FILTER", (blocks[0],), gate="adx_gate")
            detected.append("ADX regime filter")
        elif len(blocks) >= 2 and connective == "and":
            recipe = Recipe("FILTER", (blocks[0],), gate=blocks[1])   # both must agree
        elif len(blocks) >= 2:
            recipe = Recipe("VOTE", tuple(blocks[:3]))
        else:
            recipe = Recipe("VOTE", (blocks[0],))
        if len(blocks) > 3:
            warnings.append(f"NOTE: {len(blocks)} indicators detected; mapped the first 3.")
        warnings.append("NOTE: entry/exit mapped heuristically to block signals; verify against source.")

    family = "unknown"
    if recipe:
        fams = [BLOCKS[b].family for b in recipe.blocks]
        family = max(set(fams), key=fams.count)

    spec = {
        "source": "user-pine", "indicators": detected or ["(none recognised)"],
        "entry_long": _describe(recipe, "long"), "entry_short": _describe(recipe, "short"),
        "exit": "implicit; long/short, reverses on the opposite signal (as mapped)",
        "filters": "ADX regime gate" if has_gate else "none",
        "risk": "as specified in the Pine script (not modelled here)",
        "sizing": "fixed full-notional (evaluation assumption)",
        "assumptions": _assumptions(family),
        "lookahead": "mapped block signals use closed bars; engine shifts positions forward one bar",
    }
    return ImportedStrategy(name=name, pine_hash=h, detected=detected, recipe=recipe,
                            family=family, spec=spec, warnings=warnings)


def _describe(recipe: Optional[Recipe], side: str) -> str:
    if not recipe:
        return "(unmapped)"
    names = " + ".join(recipe.blocks) + (f" gated by {recipe.gate}" if recipe.gate else "")
    return f"{side} when the composed signal ({recipe.rule}: {names}) is {side}"


def _assumptions(family: str) -> List[str]:
    return {
        "mean-reversion": ["price mean-reverts from extremes", "the market is ranging when signals fire"],
        "trend": ["trends persist long enough to overcome signal lag"],
        "breakout": ["breakouts precede persistent moves", "enough trending episodes to pay for whipsaw"],
        "volatility": ["a volatility-expansion move precedes continuation"],
    }.get(family, ["the composed signal carries predictive information net of costs"])
