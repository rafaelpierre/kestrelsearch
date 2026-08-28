#!/usr/bin/env python3
"""Capture deterministic Kestrel retrieval artifacts outside the agent loop."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

from run import DEFAULT_RESULTS_DIR, load_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    artifact_dir = DEFAULT_RESULTS_DIR / "artifacts"
    for task in load_tasks(args.tasks):
        query = task.get("search_query", task["prompt"])
        run_id = uuid.uuid4().hex
        env = os.environ | {
            "KESTRELSEARCH_BENCHMARK_ARTIFACT_DIR": str(artifact_dir),
            "KESTRELSEARCH_BENCHMARK_RUN_ID": run_id,
            "KESTRELSEARCH_OTEL_ENDPOINT": "http://127.0.0.1:4317",
        }
        command = [
            str(Path(sys.executable).parent / "kestrelsearch"),
            "search",
            query,
            "--top-k",
            str(args.top_k),
            "--output",
            "json",
        ]
        subprocess.run(  # noqa: S603
            command,
            check=True,
            env=env,
        )
        print(f"{task['id']}: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
