"""Research Core — orchestration brain of the Market Intelligence Engine.

Owns the scientific-method flow, the frozen-matrix versions, and the registry of
research modules (Strategy Evaluation is one such module).
"""
from . import dossier_generator, module_registry, versioning
from .orchestrator import Orchestrator, RunOutcome

__all__ = ["Orchestrator", "RunOutcome", "versioning", "module_registry", "dossier_generator"]
