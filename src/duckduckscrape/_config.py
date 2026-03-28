"""Manages ~/.duckduckscrape/config.toml — tracks skill installation paths."""
from __future__ import annotations

from pathlib import Path

import tomlkit

CONFIG_PATH = Path.home() / ".duckduckscrape" / "config.toml"


def _load() -> tomlkit.TOMLDocument:
    if CONFIG_PATH.exists():
        return tomlkit.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    doc: tomlkit.TOMLDocument = tomlkit.document()
    doc.add("skill", tomlkit.table())
    doc["skill"].add("installations", tomlkit.array())  # type: ignore[index]
    return doc


def _save(doc: tomlkit.TOMLDocument) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(tomlkit.dumps(doc), encoding="utf-8")


def get_installations() -> list[Path]:
    """Return all recorded skill installation paths."""
    doc = _load()
    raw: list[str] = doc.get("skill", {}).get("installations", [])  # type: ignore[assignment]
    return [Path(p) for p in raw]


def record_installation(path: Path) -> None:
    """Add *path* to the tracked installations (idempotent)."""
    doc = _load()
    if "skill" not in doc:
        doc.add("skill", tomlkit.table())
    skill_table = doc["skill"]  # type: ignore[index]
    if "installations" not in skill_table:
        skill_table.add("installations", tomlkit.array())
    installations: list = skill_table["installations"]  # type: ignore[assignment]
    absolute = str(path.resolve())
    if absolute not in [str(Path(p).resolve()) for p in installations]:
        installations.append(absolute)
    _save(doc)


def remove_installation(path: Path) -> None:
    """Remove *path* from tracked installations."""
    doc = _load()
    try:
        installations: list = doc["skill"]["installations"]  # type: ignore[index]
    except KeyError:
        return
    target = str(path.resolve())
    updated = [p for p in installations if str(Path(p).resolve()) != target]
    doc["skill"]["installations"] = tomlkit.array()  # type: ignore[index]
    for p in updated:
        doc["skill"]["installations"].append(p)  # type: ignore[index]
    _save(doc)
