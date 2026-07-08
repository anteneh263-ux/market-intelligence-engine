"""Strategy Discovery Engine — generative layer of the Market Intelligence Engine.

Generates trading-strategy hypotheses by composing indicator building blocks under
learned priors, prioritises them via meta-learning, avoids repeatedly-failed traits,
and feeds the most promising (frozen, pre-registered, auditable) into the Research
Core evaluation pipeline — recording them in a separate discovery namespace.
"""
from .engine import Proposal, StrategyDiscoveryEngine
from .genome import GeneratedStrategy, Recipe
from .ledger import DiscoveryLedger
from .policy import DISCOVERY_POLICY_VERSION, policy_descriptor, policy_hash

__all__ = [
    "StrategyDiscoveryEngine", "Proposal", "Recipe", "GeneratedStrategy",
    "DiscoveryLedger", "DISCOVERY_POLICY_VERSION", "policy_hash", "policy_descriptor",
]
