#!/usr/bin/env python3
"""Replay a frozen retrieval payload through the Responses API for token measurement."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--content-limit", type=int, default=8000)
    args = parser.parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        parser.error("OPENAI_API_KEY is required for replay")
    artifact = json.loads(args.artifact.read_text())
    payload = "\n\n".join(
        f"Source: {result['url']}\n{(result.get('content') or result['snippet'])[:args.content_limit]}"
        for result in artifact["results"]
    )
    request = {
        "model": args.model,
        "input": f"{args.prompt}\n\nRetrieved material:\n{payload}",
        "metadata": {"benchmark_run_id": artifact["run_id"], "benchmark_arm": "kestrel_replay"},
    }
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json=request,
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    print(json.dumps({"usage": body.get("usage"), "output": body.get("output")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
