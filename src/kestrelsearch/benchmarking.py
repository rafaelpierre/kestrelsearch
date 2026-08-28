"""Optional benchmark telemetry and retrieval-artifact capture."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from opentelemetry import trace

_configured = False


def configure_telemetry() -> None:
    """Configure OTLP only when benchmark mode supplies an endpoint."""
    global _configured
    endpoint = os.getenv("KESTRELSEARCH_OTEL_ENDPOINT")
    if _configured or not endpoint:
        return
    # OTLP pulls in the SDK, protobuf, and gRPC stacks. Keep those imports off
    # normal CLI startup; benchmark mode is the only path that needs them.
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": "kestrelsearch"})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True


@contextmanager
def span(name: str, attributes: dict[str, str | int | float]) -> Iterator[None]:
    """Create a no-op span outside benchmark mode and an OTLP span inside it."""
    configure_telemetry()
    run_id = os.getenv("KESTRELSEARCH_BENCHMARK_RUN_ID")
    if run_id:
        attributes = attributes | {"benchmark.run_id": run_id}
    with trace.get_tracer("kestrelsearch").start_as_current_span(name) as active_span:
        active_span.set_attributes(attributes)
        yield


def write_artifact(
    query: str,
    results: list[dict],
    timings_ms: dict[str, int],
    *,
    queries: list[str] | None = None,
    engines: list[str] | None = None,
    mode: str | None = None,
) -> None:
    """Write one compact retrieval artifact when a benchmark artifact directory is set."""
    artifact_dir = os.getenv("KESTRELSEARCH_BENCHMARK_ARTIFACT_DIR")
    run_id = os.getenv("KESTRELSEARCH_BENCHMARK_RUN_ID")
    if not artifact_dir or not run_id:
        return
    rendered_results = [
        {
            "rank": index,
            "url": result["url"],
            "title": result["title"],
            "snippet": result["snippet"],
            "bm25_score": result.get("bm25_score"),
            "content": result.get("content"),
            "content_chars": len(result.get("content") or ""),
            "content_sha256": hashlib.sha256(
                (result.get("content") or "").encode()
            ).hexdigest(),
            "engine": result.get("engine"),
            "query": result.get("query"),
            "engine_rank": result.get("engine_rank"),
            "sources": result.get("sources"),
        }
        for index, result in enumerate(results, start=1)
    ]
    artifact = {
        "run_id": run_id,
        "query": query,
        "queries": queries or [query],
        "engines": engines or [],
        "mode": mode or "fallback",
        "results": rendered_results,
        "returned_chars": sum(len(result.get("content") or "") for result in results),
        "timings_ms": timings_ms,
    }
    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{run_id}-{uuid.uuid4().hex}.json"
    target.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
