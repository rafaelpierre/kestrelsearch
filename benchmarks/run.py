#!/usr/bin/env python3
"""Run paired Codex web-search benchmark arms and persist JSONL measurements."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS = ROOT / "benchmarks" / "tasks" / "smoke.jsonl"
DEFAULT_RESULTS_DIR = ROOT / "benchmarks" / "results"


@dataclass(frozen=True)
class Measurement:
    run_id: str
    task_id: str
    arm: str
    trial: int
    started_at: str
    elapsed_ms: int
    exit_code: int
    usage: dict[str, int]
    final_answer: str | None
    tags: list[str]
    retrieval: dict[str, Any]
    quality: dict[str, bool]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--arm", choices=("native", "kestrel"), required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--codex-command",
        default="./scripts/codex-phoenix",
        help="Command used to launch Codex (default: %(default)s).",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    return args


def load_tasks(path: Path, sample_size: int | None = None, seed: int = 42) -> list[dict[str, Any]]:
    if path.suffix == ".csv":
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        if sample_size is None:
            raise ValueError("--sample-size is required when --tasks is a CSV dataset")
        if sample_size < 1 or sample_size > len(rows):
            raise ValueError(f"--sample-size must be between 1 and {len(rows)}")
        selected = random.Random(seed).sample(rows, sample_size)  # noqa: S311
        return [
            {
                "id": f"dsqa-{row['example_id']}",
                "search_query": row["problem"],
                "prompt": row["problem"],
                "tags": ["deepsearchqa", row["problem_category"]],
                "expected": {"must_include": [part.strip() for part in row["answer"].split(",")], "reference_urls": []},
            }
            for row in selected
        ]
    tasks: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        task = json.loads(line)
        if not isinstance(task.get("id"), str) or not isinstance(task.get("prompt"), str):
            msg = f"{path}:{line_number}: each task needs string id and prompt fields"
            raise ValueError(msg)
        tasks.append(task)
    if not tasks:
        raise ValueError(f"{path}: no tasks found")
    return tasks


def benchmark_prompt(task: dict[str, Any], arm: str, payload: str = "") -> str:
    tool_instruction = (
        "You must use Codex's native web search at least once before answering."
        if arm == "native"
        else "Use only the supplied Kestrel retrieval material. Do not use native web search."
    )
    return "\n\n".join(
        (
            "You are completing a controlled web-research benchmark.",
            tool_instruction,
            "Give a concise answer and cite the source URLs you relied on.",
            f"Task ID: {task['id']}",
            task["prompt"],
            payload,
        )
    )


def build_command(codex_command: str, task: dict[str, Any], arm: str, run_id: str, payload: str = "") -> list[str]:
    attributes = {
        "service.name": "codex-kestrel-benchmark",
        "benchmark.project": "kestrelsearch",
        "benchmark.run_id": run_id,
        "benchmark.task_id": task["id"],
        "benchmark.arm": arm,
    }
    toml_attributes = ", ".join(
        f"{json.dumps(key)} = {json.dumps(value)}" for key, value in attributes.items()
    )
    command = shlex.split(codex_command)
    command.extend(
        (
            "-c",
            f"otel.span_attributes={{ {toml_attributes} }}",
        )
    )
    if arm == "native":
        command.append("--search")
    command.extend(
        (
            "exec",
            "--ephemeral",
            "--json",
            "-s",
            "read-only",
            benchmark_prompt(task, arm, payload),
        )
    )
    return command


def parse_codex_events(stdout: str) -> tuple[dict[str, int], str | None]:
    usage: dict[str, int] = {}
    answer: str | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            answer = item.get("text")
    return usage, answer


def score_answer(task: dict[str, Any], answer: str | None) -> dict[str, bool]:
    answer = (answer or "").lower()
    expected = task.get("expected", {})
    terms = expected.get("must_include", [])
    sources = expected.get("reference_urls", [])
    terms_passed = all(term.lower() in answer for term in terms)
    sources_passed = not sources or any(source.lower() in answer for source in sources)
    return {"terms_passed": terms_passed, "sources_passed": sources_passed, "passed": terms_passed and sources_passed}


def capture_kestrel(task: dict[str, Any], run_id: str, artifact_dir: Path) -> tuple[dict[str, Any], str]:
    environment = os.environ | {
        "KESTRELSEARCH_BENCHMARK_ARTIFACT_DIR": str(artifact_dir),
        "KESTRELSEARCH_BENCHMARK_RUN_ID": run_id,
        "KESTRELSEARCH_OTEL_ENDPOINT": "http://127.0.0.1:4317",
    }
    started = time.perf_counter()
    completed = subprocess.run(  # noqa: S603
        [str(Path(sys.executable).parent / "kestrelsearch"), "search", task.get("search_query", task["prompt"]), "--output", "json"],
        check=True, env=environment, text=True, capture_output=True,
    )
    artifact_path = next(artifact_dir.glob(f"{run_id}-*.json"), None)
    if artifact_path is None:
        artifact = {
            "run_id": run_id,
            "query": task.get("search_query", task["prompt"]),
            "results": [],
            "returned_chars": 0,
            "timings_ms": {},
            "verified": False,
            "stdout": completed.stdout,
        }
    else:
        artifact = json.loads(artifact_path.read_text())
        artifact["verified"] = bool(artifact["results"])
    artifact["agent_elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    results = cast(list[dict[str, Any]], artifact["results"])
    payload = "\n\nRetrieved Kestrel material:\n" + "\n\n".join(
        f"Source: {item['url']}\n{item.get('content') or item['snippet']}" for item in results
    )
    return artifact, payload


def run_once(
    command: list[str], task: dict[str, Any], arm: str, trial: int, timeout: int, run_id: str,
    artifact_dir: Path, prefetched: dict[str, Any] | None = None,
) -> Measurement:
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    # The command is intentionally configurable so a benchmark can target a
    # local Codex launcher. It is executed without a shell.
    environment = os.environ | {
        "KESTRELSEARCH_BENCHMARK_ARTIFACT_DIR": str(artifact_dir),
        "KESTRELSEARCH_BENCHMARK_RUN_ID": run_id,
        "KESTRELSEARCH_OTEL_ENDPOINT": "http://127.0.0.1:4317",
    }
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=timeout,
    )
    agent_elapsed_ms = round((time.perf_counter() - started) * 1000)
    usage, answer = parse_codex_events(completed.stdout)
    artifacts = [prefetched] if prefetched else []
    retrieval = {
        "artifact_paths": [str(path) for path in artifact_dir.glob(f"{run_id}-*.json")],
        "invocations": len(artifacts),
        "returned_chars": sum(artifact["returned_chars"] for artifact in artifacts),
        "estimated_input_tokens": sum(artifact["returned_chars"] for artifact in artifacts) // 4,
        "verified": arm != "kestrel" or bool(artifacts and artifacts[0]["verified"]),
        "agent_elapsed_ms": agent_elapsed_ms,
        "retrieval_elapsed_ms": prefetched.get("agent_elapsed_ms", 0) if prefetched else 0,
    }
    return Measurement(
        run_id=run_id,
        task_id=task["id"],
        arm=arm,
        trial=trial,
        started_at=started_at,
        elapsed_ms=agent_elapsed_ms + (prefetched.get("agent_elapsed_ms", 0) if prefetched else 0),
        exit_code=completed.returncode,
        usage=usage,
        final_answer=answer,
        tags=task.get("tags", []),
        retrieval=retrieval,
        quality=score_answer(task, answer),
    )


def main() -> int:
    args = parse_args()
    tasks = load_tasks(args.tasks, args.sample_size, args.seed)
    output = args.output or DEFAULT_RESULTS_DIR / f"{args.arm}-{int(time.time())}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir = output.parent / "artifacts"

    for task in tasks:
        for trial in range(1, args.trials + 1):
            run_id = uuid.uuid4().hex
            prefetched, payload = (None, "")
            if not args.dry_run and args.arm == "kestrel":
                prefetched, payload = capture_kestrel(task, run_id, artifact_dir)
            command = build_command(args.codex_command, task, args.arm, run_id, payload)
            print(f"[{args.arm}] {task['id']} trial {trial}: {shlex.join(command)}")
            if args.dry_run:
                continue
            measurement = run_once(
                command, task, args.arm, trial, args.timeout, run_id, artifact_dir, prefetched
            )
            with output.open("a") as result_file:
                result_file.write(json.dumps(asdict(measurement)) + "\n")
            print(f"  -> {measurement.elapsed_ms} ms, exit {measurement.exit_code}")
            if not measurement.retrieval["verified"]:
                print("  -> invalid Kestrel trial: no retrieval artifact was captured")

    if not args.dry_run:
        print(f"Results: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
