import re

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
    for result, score in zip(results, scores):
        result["bm25_score"] = float(score)
        if score > 0:
            ranked.append(result)

    ranked.sort(key=lambda x: x["bm25_score"], reverse=True)
    return ranked
