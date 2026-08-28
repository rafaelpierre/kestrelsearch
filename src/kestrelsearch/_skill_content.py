"""Dynamically generates SKILL.md from the live Click command tree.

The SKILL.md follows the Agent Skills open standard (agentskills.io) and works
with Claude Code, Codex, and GitHub Copilot in VS Code.

Call `generate_skill_md(main)` — where `main` is the root Click group — to get
an up-to-date SKILL.md that always reflects the current CLI options.
"""

from __future__ import annotations

import inspect
from typing import Any

import click
from jinja2 import Environment, StrictUndefined

# ---------------------------------------------------------------------------
# Jinja2 template — structure is fixed; all CLI-specific content is injected
# ---------------------------------------------------------------------------
_SKILL_TEMPLATE = """\
---
name: kestrelsearch
description: >
  Lightweight multi-engine web search with BM25 relevance ranking.
  Use when asked to search the web, look something up, find recent information,
  research a topic, or browse the internet.
  Trigger phrases: search the web, look up, find information about, google,
  browse the web, web search, find recent, what is the latest on.
argument-hint: "<search query>"
---

# Kestrel Search

{{ main_help }}

## Installation

```bash
# Install system-wide with uv (recommended)
uv tool install kestrelsearch

# Or with pip
pip install kestrelsearch
```

{% for name, cmd in commands.items() %}
## `{{ name }}` subcommand

{{ cmd.help }}
{% if cmd.params %}
### Options

| Option | Default | Choices | Description |
|--------|---------|---------|-------------|
{% for p in cmd.params %}| `{{ p.display_name }}` | {{ p.default }} | {{ p.choices }} | {{ p.help }} |
{% endfor %}
{% endif %}
{% if cmd.examples %}
### Examples

```bash
{% for ex in cmd.examples %}{{ ex }}
{% endfor %}```
{% endif %}
{% endfor %}
## JSON output schema

Each element in the returned array contains:

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Page title |
| `url` | string | Full canonical URL |
| `display_url` | string | Shortened URL shown by the search engine |
| `snippet` | string | Search-result snippet |
| `content` | string or null | Extracted main-body text prefixed with `Source: <url>` (null when `--no-fetch`) |
| `bm25_score` | number | BM25 relevance score (only present when ranking is active) |
| `engine` | string | Engine that supplied the retained result |
| `query` | string | Query that supplied the retained result |
| `engine_rank` | number | Original position within that engine/query response |
| `sources` | array | Every engine/query occurrence merged into this URL |

## Notes

- This `SKILL.md` is compatible with Claude Code, Codex, and GitHub Copilot in VS Code.
- Supply a query either as the positional `QUERY` or with repeatable `-q/--query` options. Both forms can be combined.
- Progress logs go to **stderr**; clean JSON goes to **stdout**.
  Pipe stdout for programmatic use: `kestrelsearch search "..." --output json 2>/dev/null`
- PDFs are automatically skipped during content fetching.
- Page bodies are streamed up to `--max-response-bytes`; network and parsing concurrency are controlled separately.
- By default, at most three times `--top-k` candidates are fetched before BM25 ranking.
- BM25 filtering removes results with zero relevance to the query.
- Use `--no-fetch` for a fast, low-cost keyword search without content extraction.
"""


def _get_examples(callback: Any) -> list[str]:
    """Extract example lines from the `Examples:` verbatim block in a Click docstring.

    Click uses the backspace character (\\b, chr 8) to mark a verbatim block.
    Only lines under an explicit `Examples:` header are returned.
    """
    doc = inspect.getdoc(callback) or ""
    if "\b" not in doc:
        return []
    after = doc.split("\b", 1)[1]
    lines = after.splitlines()
    # Find the "Examples:" header; only capture lines after it
    start = None
    for i, line in enumerate(lines):
        if line.strip().rstrip(":").lower() == "examples":
            start = i + 1
            break
    if start is None:
        return []
    return [line.strip() for line in lines[start:] if line.strip()]


def _param_info(param: click.Option) -> dict[str, Any]:
    """Extract display metadata from a Click option for the options table."""
    display_name = "/".join(param.opts)
    default = "" if param.default is None else str(param.default)
    choices = (
        ", ".join(param.type.choices) if isinstance(param.type, click.Choice) else ""
    )
    # Escape pipe characters so they don't break Markdown table cells
    help_text = (param.help or "").replace("|", "\\|")
    return {
        "display_name": display_name,
        "default": default,
        "choices": choices,
        "help": help_text,
    }


def generate_skill_md(main_cmd: click.Group) -> str:
    """Render SKILL.md by introspecting the live Click command tree.

    Pass the root Click group (e.g. `main` from cli.py).  The returned string
    is always consistent with the current options, defaults, and docstrings.
    """
    env = Environment(  # noqa: S701 -- renders trusted Markdown, not HTML.
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    template = env.from_string(_SKILL_TEMPLATE)

    commands: dict[str, Any] = {}
    for cmd_name, cmd in main_cmd.commands.items():
        # Skip nested groups (e.g. `skill`) — they are setup plumbing, not agent-facing
        if isinstance(cmd, click.Group):
            continue
        raw_doc = inspect.getdoc(cmd.callback) or ""
        # Text before the \b verbatim block is the summary paragraph
        summary = raw_doc.split("\b")[0].strip()
        params = [_param_info(p) for p in cmd.params if isinstance(p, click.Option)]
        commands[cmd_name] = {
            "help": summary,
            "params": params,
            "examples": _get_examples(cmd.callback),
        }

    return template.render(
        main_help=inspect.getdoc(main_cmd.callback) or "",
        commands=commands,
    )
