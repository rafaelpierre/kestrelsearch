from kestrelsearch.ranking import rank_results, tokenize


def test_tokenize_normalizes_words():
    assert tokenize("Hello, WORLD! 123") == ["hello", "world", "123"]


def test_rank_results_orders_relevant_content_and_discards_zero_scores():
    results = [
        {"title": "Low", "content": "python"},
        {"title": "High", "content": "python python dataclasses"},
        {"title": "None", "content": None},
        {"title": "Other", "content": "unrelated text"},
    ]

    ranked = rank_results(results, "python dataclasses")

    assert [result["title"] for result in ranked] == ["High"]
    assert all(result["bm25_score"] > 0 for result in ranked)
