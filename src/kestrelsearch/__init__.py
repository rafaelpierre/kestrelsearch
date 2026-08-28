"""Kestrel Search — web search, page extraction, and relevance ranking for AI agents."""

from .search import search
from .fetcher import fetch_all
from .ranking import rank_results

__all__ = ["search", "fetch_all", "rank_results"]
