#!/usr/bin/env python3
"""Capture native OpenAI web-search output as frozen benchmark artifacts."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

import httpx
from run import DEFAULT_RESULTS_DIR, load_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        parser.error("OPENAI_API_KEY is required for native capture")
    artifact_dir = DEFAULT_RESULTS_DIR / "native-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for task in load_tasks(args.tasks):
        run_id = uuid.uuid4().hex
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": args.model,
                "input": task["prompt"],
                "tools": [{"type": "web_search"}],
                "include": ["web_search_call.action.sources"],
                "metadata": {"benchmark_run_id": run_id, "benchmark_arm": "native"},
            },
            timeout=120,
        )
        response.raise_for_status()
        target = artifact_dir / f"{run_id}.json"
        target.write_text(json.dumps(response.json(), indent=2), encoding="utf-8")
        print(f"{task['id']}: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
