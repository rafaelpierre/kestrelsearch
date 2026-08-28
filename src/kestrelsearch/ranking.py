"""BM25 ranking and fair interleaving for single- and multi-query results."""

import re
from collections.abc import Sequence

from rank_bm25 import BM25Okapi


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer for BM25."""
    return re.findall(r"\b\w+\b", text.lower())


def rank_results(results: list[dict], query: str) -> list[dict]:
    """Re-rank results by BM25 relevance of their fetched content.

    Results whose content is None or scores zero are filtered out.

    Args:
        results: List of result dicts, each expected to have a "content" key.
        query: The original search query used for scoring.

    Returns:
        Filtered and sorted list of result dicts, with "bm25_score" added.
    """
    query_tokens = tokenize(query)
    corpus = [tokenize(r.get("content") or "") for r in results]

    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)

    ranked = []
    for result, score in zip(results, scores, strict=True):
        result["bm25_score"] = float(score)
        if score > 0:
            ranked.append(result)

    ranked.sort(key=lambda x: x["bm25_score"], reverse=True)
    return ranked


def rank_results_by_query(results: list[dict], queries: Sequence[str]) -> list[dict]:
    """Rank within originating-query groups and interleave them fairly.

    Results are assigned to buckets in one pass, keeping grouping at O(N + Q)
    rather than rescanning all N results for each query. Results without known
    provenance share a fallback bucket scored against all supplied queries.
    """
    query_order = tuple(dict.fromkeys(queries))
    by_query: dict[str, list[dict]] = {query: [] for query in query_order}
    unassigned: list[dict] = []
    for result in results:
        query = result.get("query")
        if isinstance(query, str) and query in by_query:
            by_query[query].append(result)
        else:
            unassigned.append(result)

    buckets = [
        _rank_or_retain(by_query[query], query)
        for query in query_order
        if by_query[query]
    ]
    if unassigned:
        buckets.append(_rank_or_retain(unassigned, " ".join(query_order)))

    return _interleave(buckets)


def _rank_or_retain(results: list[dict], query: str) -> list[dict]:
    """Keep a small corpus when BM25 produces no positive scores."""
    return rank_results(results, query) or results


def _interleave(buckets: Sequence[Sequence[dict]]) -> list[dict]:
    """Round-robin ordered buckets so one query cannot dominate early results."""
    ranked = []
    for index in range(max((len(bucket) for bucket in buckets), default=0)):
        ranked.extend(bucket[index] for bucket in buckets if index < len(bucket))
    return ranked
