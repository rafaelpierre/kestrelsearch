"""Structured local diagnostics for Kestrel Search."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def log_event(event: str, **attributes: Any) -> None:
    """Append a diagnostic event without making search failures worse."""
    now = datetime.now(UTC)
    target = Path.home() / ".kestrel" / "logs" / now.date().isoformat() / "events.jsonl"
    record = {"timestamp": now.isoformat(), "event": event, **attributes}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, default=str) + "\n")
    except OSError:
        # Diagnostics must never prevent a search result from being returned.
        return
