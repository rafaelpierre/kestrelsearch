# Kestrel Search benchmarks

This scaffold compares two Codex configurations on the same task fixtures:

- `native`: Codex with native live web search enabled.
- `kestrel`: Codex without native web search, instructed to use the Kestrel Search skill.

Each task/trial is written as a JSON object in a JSONL results file. The runner
records wall-clock latency, the Codex token usage reported in the final event,
the final answer, and a `benchmark.run_id` that is also attached to the Phoenix
trace.

For Kestrel runs, every CLI invocation also writes a compact retrieval artifact
under `benchmarks/results/artifacts/`. It records returned URLs, BM25 scores,
content lengths and hashes, but not page bodies. Phoenix receives `kestrel.search`,
`kestrel.fetch`, and `kestrel.rank` spans. Use the shared `benchmark.run_id` to
join Codex and Kestrel activity.

The runner marks a Kestrel trial as invalid if no artifact is captured. This
prevents an answer based only on model knowledge from being counted as a search
comparison.

## Run

Start Phoenix in another terminal:

```bash
uv run phoenix serve
```

Run a smoke benchmark first:

```bash
uv run python benchmarks/run.py --arm native --trials 1
uv run python benchmarks/run.py --arm kestrel --trials 1
uv run python benchmarks/report.py benchmarks/results/native-*.jsonl benchmarks/results/kestrel-*.jsonl
```

When a semantic review JSONL has been recorded according to
[SKILLS.md](../SKILLS.md), include it in the report:

```bash
uv run python benchmarks/report.py \
  --evaluations benchmarks/results/web-semantic-evaluation-20260828.jsonl \
  benchmarks/results/web-native-3trials-20260828.jsonl \
  benchmarks/results/web-kestrel-3trials-20260828.jsonl
```

Use the same task file, model, reasoning effort, and trial count for both arms.
The generated results are ignored by Git and are stored in `benchmarks/results/`.
Open <http://127.0.0.1:6006> to inspect the correlated Codex traces.

## Task format

`tasks/smoke.jsonl` is intentionally only a wiring check. Create a separate
versioned task file for a real evaluation. Every line must have:

```json
{
  "id": "unique-task-id",
  "prompt": "The task handed to Codex.",
  "tags": ["documentation", "current"],
  "expected": {
    "must_include": ["optional check"],
    "reference_urls": ["https://example.com"]
  }
}
```

Keep task answers and grading rules separate from the prompt. For an initial
coding-focused set, mix current library/API questions, release-note questions,
and documentation-debugging tasks. Run each task at least three times and
compare success rate plus p50/p95 latency rather than a single run.

## Useful commands

```bash
# Show generated commands without contacting Codex.
uv run python benchmarks/run.py --arm kestrel --dry-run

# Run the varied web-retrieval suite with five trials per task.
uv run python benchmarks/run.py \
  --tasks benchmarks/tasks/web_retrieval.jsonl \
  --arm native \
  --trials 5
```

For payload-level measurement, capture Kestrel independently (this guarantees
retrieval happened), then replay its frozen content with a fixed API model:

```bash
uv run python benchmarks/capture_kestrel.py --tasks benchmarks/tasks/smoke.jsonl
uv run python benchmarks/capture_native.py --tasks benchmarks/tasks/smoke.jsonl --model <your-model>
uv run python benchmarks/replay.py --artifact benchmarks/results/artifacts/<run-id>-*.json \
  --prompt "..." --model <your-model>
```

`capture_native.py` and `replay.py` intentionally require an explicit model
and `OPENAI_API_KEY`. They measure the controlled API path, not a Codex app
session. Native capture preserves the API response and source metadata; Kestrel
capture preserves the retrieved text used for replay.

The runner does not score answer quality yet. That is intentional: scoring
uses simple task-specific term and source checks. Treat this as a regression
gate, then add unit tests, exact answers, or human review for high-stakes tasks.

## Suite design

`web_retrieval.jsonl` covers eight retrieval patterns inspired by current
agentic-web evaluation suites: freshness, multi-hop retrieval, practical
information lookup, source reconciliation, standards lookup, security
workflows, technical investigation, and release engineering. It deliberately
excludes browser-automation tasks such as form submission and checkout flows:
those measure browser control, not Kestrel's retrieval quality.

Semantic answer review is defined in [SKILLS.md](../SKILLS.md). Use it after a run
to distinguish incorrect retrieval, incorrect synthesis, and stale benchmark
fixtures; do not treat the lightweight keyword checks as the final quality
score.

For a minimally robust comparison, use this suite with at least five trials
per task and arm (40 runs per arm). Report per-task results as well as the
aggregate median, and retain the frozen Kestrel artifacts for later review.

## DeepSearchQA sampling

The runner can also sample `benchmarks/data/DSQA-full.csv` reproducibly. The
sample uses Python's seeded sampler, maps each problem to a retrieval task, and
uses the dataset answer for an exact-answer quality check.

```bash
uv run python benchmarks/run.py \
  --tasks benchmarks/data/DSQA-full.csv \
  --sample-size 20 \
  --seed 42 \
  --arm native \
  --trials 3
```

Use the identical `--sample-size` and `--seed` for both arms. The CSV requires
`--sample-size`, ensuring a full dataset is never run accidentally.
