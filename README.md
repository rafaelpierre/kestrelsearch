# duckduckscrape

Lightweight DuckDuckGo web search for AI agents. Fetches results concurrently, extracts clean page content, and re-ranks by relevance using BM25 — all from the command line.

## Features

- **DuckDuckGo search** — scrapes `html.duckduckgo.com` with region and recency filters
- **Concurrent page fetching** — async HTTP/2 with connection pooling; configurable concurrency
- **Intelligent content extraction** — removes navigation, ads, and boilerplate; weights headings and body text using SEO heuristics; deduplicates lines
- **BM25 re-ranking** — indexes full page text and re-ranks results by relevance to the original query
- **Agent-friendly output** — clean JSON on stdout, progress logs on stderr; pipe-safe for agent tool calls
- **Skill management** — installs a `SKILL.md` for Claude Code and GitHub Copilot in VS Code so agents discover and invoke the tool automatically; tracks installations and supports clean uninstall

## Installation

```bash
# System-wide with uv (recommended)
uv tool install duckduckscrape

# Or with pip
pip install duckduckscrape
```

## Usage

```bash
# Basic search — top 5 results ranked by BM25
duckduckscrape search "python dataclasses"

# Clean JSON for agent/programmatic use (progress on stderr)
duckduckscrape search "rust ownership" --output json

# Limit results
duckduckscrape search "climate change" -k 3

# Skip full-page fetching for a fast keyword-only search
duckduckscrape search "openai news" --no-fetch

# Filter by recency: d (day), w (week), m (month), y (year)
duckduckscrape search "breaking news" --time-filter d

# Filter by region
duckduckscrape search "local elections" --region us-en

# Tune performance
duckduckscrape search "machine learning" --concurrency 8 --timeout 15 --content-limit 3000
```

### Output format

Each result in `--output json` is an object:

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Page title |
| `url` | string | Full URL |
| `display_url` | string | Shortened display URL |
| `snippet` | string | DuckDuckGo snippet |
| `content` | string \| null | Extracted page text prefixed with `Source: <url>` |
| `bm25_score` | number | BM25 relevance score (omitted when `--no-rank`) |

## Agent skill

duckduckscrape can install a `SKILL.md` so that Claude Code and GitHub Copilot in VS Code automatically know when and how to invoke it.

```bash
# Install (prompts for agent and scope)
duckduckscrape skill install

# Install non-interactively
duckduckscrape skill install --agent both --scope global

# Uninstall (interactive, reads from ~/.duckduckscrape/config.toml)
duckduckscrape skill uninstall
```

Installation paths are recorded in `~/.duckduckscrape/config.toml`. The `uninstall` command reads this file to present a selection dialogue — no manual path hunting required.

### Skill locations

| Agent | Scope | Path |
|-------|-------|------|
| Claude Code | project | `.claude/skills/duckduckscrape/SKILL.md` |
| Claude Code | global | `~/.claude/skills/duckduckscrape/SKILL.md` |
| VS Code Copilot | project | `.github/skills/duckduckscrape/SKILL.md` |
| VS Code Copilot | global | `~/.copilot/skills/duckduckscrape/SKILL.md` |

`~/.claude/skills/` is recognised by both Claude Code and VS Code Copilot, so a single global Claude install covers both agents.

The `SKILL.md` is generated dynamically from the live CLI — options, defaults, and examples stay in sync with the installed version automatically.

## Use cases

- **Agent web search** — drop into any Claude Code or Copilot workflow; the agent calls `duckduckscrape search "..." --output json` and gets structured, ranked results it can reason over
- **Research pipelines** — pipe JSON output into `jq`, Python scripts, or other tools for further processing
- **Content monitoring** — combine `--time-filter d` with cron to watch a topic for recent developments
- **Fast keyword lookup** — `--no-fetch` returns DDG snippets in under a second, useful when full content isn't needed

## How it works

1. POST to `https://html.duckduckgo.com/html/` and parse `div.result` elements with BeautifulSoup + lxml
2. Fetch each result URL concurrently with a shared `httpx.AsyncClient` (HTTP/2, bounded semaphore)
3. Extract main content: strip nav/header/footer/ads, narrow to `<main>`/`<article>`, weight headings, deduplicate lines
4. Build a `BM25Okapi` index over fetched content and re-rank; discard zero-score results
5. Return top-k results as JSON

## Development

```bash
git clone https://github.com/your-username/duckduckscrape
cd duckduckscrape
uv sync
uv run duckduckscrape search "test"
```
