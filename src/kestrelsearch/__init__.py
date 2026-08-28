"""Kestrel Search — web search, page extraction, and relevance ranking for AI agents."""

from .fetcher import fetch_all
from .ranking import rank_results
from .search import async_search, async_search_many, search

__all__ = ["search", "async_search", "async_search_many", "fetch_all", "rank_results"]
