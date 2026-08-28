from kestrelsearch.ranking import rank_results, rank_results_by_query, tokenize


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


def test_rank_results_by_query_ranks_groups_and_interleaves_them():
    results = [
        {"title": "Alpha best", "query": "alpha", "content": "alpha alpha exact"},
        {"title": "Alpha low", "query": "alpha", "content": "alpha"},
        {"title": "Beta best", "query": "beta", "content": "beta beta exact"},
        {"title": "Beta low", "query": "beta", "content": "beta"},
    ]

    ranked = rank_results_by_query(results, ["alpha", "beta"])

    assert [item["query"] for item in ranked[:2]] == ["alpha", "beta"]
    assert {item["title"] for item in ranked} == {
        "Alpha best",
        "Alpha low",
        "Beta best",
        "Beta low",
    }


def test_rank_results_by_query_retains_unassigned_results_and_duplicate_queries():
    results = [
        {"title": "Alpha", "query": "alpha", "content": "alpha"},
        {"title": "Other", "content": "alpha"},
    ]

    ranked = rank_results_by_query(results, ["alpha", "alpha"])

    assert [item["title"] for item in ranked] == ["Alpha", "Other"]
