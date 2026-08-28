#!/usr/bin/env python3
"""Summarize JSONL benchmark results by arm."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def percentile(values: list[int], proportion: float) -> int:
    if not values:
        return 0
    index = round((len(values) - 1) * proportion)
    return sorted(values)[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluations",
        action="append",
        type=Path,
        default=[],
        help="Semantic-evaluation JSONL files keyed by run_id.",
    )
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()
    evaluations: dict[str, dict] = {}
    for path in args.evaluations:
        for line in path.read_text().splitlines():
            if line.strip():
                evaluation = json.loads(line)
                evaluations[evaluation["run_id"]] = evaluation
    groups: dict[str, list[dict]] = defaultdict(list)
    for path in args.results:
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                groups[row["arm"]].append(row)

    for arm, rows in sorted(groups.items()):
        latencies = [row["elapsed_ms"] for row in rows]
        tokens = [
            row["usage"].get(
                "total_tokens",
                row["usage"].get("input_tokens", 0)
                + row["usage"].get("output_tokens", 0)
                + row["usage"].get("reasoning_output_tokens", 0),
            )
            for row in rows
        ]
        retrieval_tokens = [
            row.get("retrieval", {}).get("estimated_input_tokens", 0) for row in rows
        ]
        successes = sum(row["exit_code"] == 0 for row in rows)
        verified = sum(row.get("retrieval", {}).get("verified", True) for row in rows)
        quality = sum(row.get("quality", {}).get("passed", False) for row in rows)
        reviewed = [
            evaluations[row["run_id"]] for row in rows if row["run_id"] in evaluations
        ]
        print(f"{arm}: {len(rows)} trials, {successes}/{len(rows)} completed")
        print(f"  retrieval verified: {verified}/{len(rows)}")
        print(f"  task quality checks: {quality}/{len(rows)}")
        if reviewed:
            semantic_passed = sum(evaluation["pass"] for evaluation in reviewed)
            semantic_score = sum(evaluation["total"] for evaluation in reviewed) / len(
                reviewed
            )
            print(
                f"  semantic evaluation: {semantic_passed}/{len(reviewed)} passed, "
                f"mean={semantic_score:.2f}/8"
            )
        print(
            f"  latency ms: p50={median(latencies):.0f} p95={percentile(latencies, 0.95)}"
        )
        print(f"  total tokens: p50={median(tokens):.0f}")
        print(
            f"  retrieved-content estimate: p50={median(retrieval_tokens):.0f} tokens"
        )
        by_task: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_task[row["task_id"]].append(row)
        for task_id, task_rows in sorted(by_task.items()):
            passed = sum(
                row.get("quality", {}).get("passed", False) for row in task_rows
            )
            task_latency = median(row["elapsed_ms"] for row in task_rows)
            print(
                f"  {task_id}: quality={passed}/{len(task_rows)} p50={task_latency:.0f} ms"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
