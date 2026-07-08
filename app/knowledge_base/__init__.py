"""Knowledge Base layer — the corpus and its read/query API."""
from . import query
from .store import KnowledgeBase

__all__ = ["KnowledgeBase", "query"]
