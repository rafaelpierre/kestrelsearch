import json

from kestrelsearch import benchmarking


def test_span_is_a_noop_without_an_otlp_endpoint(monkeypatch):
    monkeypatch.delenv("KESTRELSEARCH_OTEL_ENDPOINT", raising=False)
    monkeypatch.setattr(benchmarking, "_configured", False)

    with benchmarking.span("kestrel.search", {"kestrel.query_length": 5}):
        pass


def test_write_artifact_records_compact_result_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("KESTRELSEARCH_BENCHMARK_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("KESTRELSEARCH_BENCHMARK_RUN_ID", "run-123")

    benchmarking.write_artifact(
        "example query",
        [
            {
                "title": "Example",
                "url": "https://example.test",
                "snippet": "Example snippet",
                "content": "Some page text",
                "bm25_score": 1.5,
            }
        ],
        {"search": 12},
    )

    artifact = json.loads(next(tmp_path.glob("run-123-*.json")).read_text())
    assert artifact["returned_chars"] == len("Some page text")
    assert artifact["results"][0]["content"] == "Some page text"
    assert artifact["results"][0]["content_sha256"]
    assert artifact["timings_ms"] == {"search": 12}
