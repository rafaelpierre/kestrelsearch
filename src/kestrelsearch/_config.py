"""Manages ~/.kestrelsearch/config.toml — tracks skill installation paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import tomlkit

CONFIG_PATH = Path.home() / ".kestrelsearch" / "config.toml"


def _load() -> tomlkit.TOMLDocument:
    if CONFIG_PATH.exists():
        return tomlkit.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    doc: tomlkit.TOMLDocument = tomlkit.document()
    doc.add("skill", tomlkit.table())
    skill_table: Any = doc["skill"]
    skill_table.add("installations", tomlkit.array())
    return doc


def _save(doc: tomlkit.TOMLDocument) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(tomlkit.dumps(doc), encoding="utf-8")


def get_installations() -> list[Path]:
    """Return all recorded skill installation paths."""
    doc = _load()
    skill_table = cast(dict[str, Any], doc.get("skill", {}))
    raw = cast(list[str], skill_table.get("installations", []))
    return [Path(p) for p in raw]


def record_installation(path: Path) -> None:
    """Add *path* to the tracked installations (idempotent)."""
    doc = _load()
    if "skill" not in doc:
        doc.add("skill", tomlkit.table())
    skill_table: Any = doc["skill"]
    if "installations" not in skill_table:
        skill_table.add("installations", tomlkit.array())
    installations: list[str] = skill_table["installations"]
    absolute = str(path.resolve())
    if absolute not in [str(Path(p).resolve()) for p in installations]:
        installations.append(absolute)
    _save(doc)


def remove_installation(path: Path) -> None:
    """Remove *path* from tracked installations."""
    doc = _load()
    try:
        skill_table: Any = doc["skill"]
        installations: list[str] = skill_table["installations"]
    except KeyError:
        return
    target = str(path.resolve())
    updated = [p for p in installations if str(Path(p).resolve()) != target]
    skill_table["installations"] = tomlkit.array()
    for p in updated:
        skill_table["installations"].append(p)
    _save(doc)
