"""Command-line orchestration for search, enrichment, ranking, and skill setup."""

import asyncio
import json
import sys
import time
from contextlib import suppress
from pathlib import Path

import click

from ._config import (
    CONFIG_PATH,
    get_installations,
    record_installation,
    remove_installation,
)
from ._skill_content import generate_skill_md
from .benchmarking import span, write_artifact
from .fetcher import DEFAULT_MAX_RESPONSE_BYTES, fetch_all
from .ranking import rank_results_by_query
from .search import ENGINE_REGISTRY, SearchError, async_search_many

_SKILL_NAME = "kestrelsearch"

# Paths where each agent looks for skills
_PROJECT_PATHS = {
    "claude": Path(".claude/skills"),
    "vscode": Path(".github/skills"),
    "codex": Path(".codex/skills"),
}
_GLOBAL_PATHS = {
    "claude": Path.home() / ".claude" / "skills",
    "vscode": Path.home() / ".copilot" / "skills",
    "codex": Path.home() / ".codex" / "skills",
}
_AGENT_CHOICES = ["claude", "vscode", "codex", "all", "both"]


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main():
    """Kestrel Search — web search, page extraction, and relevance ranking for AI agents."""


def _fetch_page_content(
    results: list[dict],
    concurrency: int,
    parse_concurrency: int,
    content_limit: int,
    max_response_bytes: int,
    timeout: float,
) -> None:
    """Fetch eligible results and attach content without changing result order.

    Indices are retained because PDFs are skipped and ``fetch_all`` returns only
    the fetchable subset. Stable ordering is required by later fair interleaving.
    """
    fetchable = [
        (index, result["url"])
        for index, result in enumerate(results)
        if ".pdf" not in result["url"].lower()
    ]
    click.echo(
        f"[kestrelsearch] Fetching {len(fetchable)} pages (concurrency={concurrency})...",
        err=True,
    )
    fetched = asyncio.run(
        fetch_all(
            [url for _, url in fetchable],
            timeout=timeout,
            content_limit=content_limit,
            max_concurrency=concurrency,
            parse_concurrency=parse_concurrency,
            max_response_bytes=max_response_bytes,
        )
    )
    for (index, url), content in zip(fetchable, fetched, strict=True):
        results[index]["content"] = f"Source: {url}\n\n{content}" if content else None

    fetched_count = sum(content is not None for content in fetched)
    click.echo(
        f"[kestrelsearch] Successfully fetched {fetched_count}/{len(fetchable)} pages.",
        err=True,
    )


def _render_text_results(results: list[dict], query: str) -> None:
    """Print terminal-friendly search results."""
    click.echo("\n" + "=" * 80)
    click.echo(f"Top {len(results)} results for: '{query}'")
    click.echo("=" * 80 + "\n")
    for index, result in enumerate(results, 1):
        score = (
            f"  [BM25: {result['bm25_score']:.2f}]" if "bm25_score" in result else ""
        )
        source = (
            f"  [{result['engine']}: {result['query']}]"
            if result.get("engine") and result.get("query")
            else ""
        )
        click.echo(f"{index}. {result['title']}{score}{source}")
        click.echo(f"   {result['url']}")
        click.echo(f"   {result['snippet']}")
        if result.get("content"):
            click.echo(f"\n   {result['content']}\n")
        else:
            click.echo()


@main.command("search")
@click.argument("query")
@click.option(
    "additional_queries",
    "-q",
    "--query",
    multiple=True,
    metavar="QUERY",
    help="Additional query to run. Repeat for multiple queries.",
)
@click.option(
    "engines",
    "-e",
    "--engine",
    multiple=True,
    default=("duckduckgo",),
    show_default=True,
    type=click.Choice(list(ENGINE_REGISTRY), case_sensitive=False),
    help="Search engine. Repeat to set fanout engines or fallback order.",
)
@click.option(
    "--mode",
    default="fallback",
    show_default=True,
    type=click.Choice(["fallback", "fanout"], case_sensitive=False),
    help="Use engines in order on failure, or run every engine/query pair.",
)
@click.option(
    "--search-concurrency",
    default=5,
    show_default=True,
    type=click.IntRange(min=1),
    metavar="N",
    help="Maximum concurrent search-engine requests.",
)
@click.option(
    "-k",
    "--top-k",
    default=5,
    show_default=True,
    type=click.IntRange(min=1),
    metavar="N",
    help="Number of top results to return.",
)
@click.option(
    "--fetch-candidates",
    type=click.IntRange(min=1),
    metavar="N",
    help="Maximum candidates to fetch before ranking (default: 3 x top-k).",
)
@click.option(
    "--fetch/--no-fetch",
    default=True,
    show_default=True,
    help="Fetch and parse full page content for each result.",
)
@click.option(
    "--rank/--no-rank",
    default=True,
    show_default=True,
    help="Re-rank results using BM25 on fetched content (requires --fetch).",
)
@click.option(
    "--region",
    default="",
    metavar="CODE",
    help="Provider region code (e.g. us-en, uk-en). Defaults to all regions.",
)
@click.option(
    "--time-filter",
    default="any",
    show_default=True,
    type=click.Choice(["any", "d", "w", "m", "y"], case_sensitive=False),
    help="Restrict by recency: any, d, w, m, y. Bing currently ignores this filter.",
)
@click.option(
    "--content-limit",
    default=2000,
    show_default=True,
    type=click.IntRange(min=1),
    metavar="CHARS",
    help="Maximum characters to extract per fetched page.",
)
@click.option(
    "--max-response-bytes",
    default=DEFAULT_MAX_RESPONSE_BYTES,
    show_default=True,
    type=click.IntRange(min=1),
    metavar="BYTES",
    help="Maximum response body accepted per fetched page.",
)
@click.option(
    "--timeout",
    default=10.0,
    show_default=True,
    type=click.FloatRange(min=0.001),
    metavar="SECS",
    help="HTTP timeout in seconds when fetching pages.",
)
@click.option(
    "--concurrency",
    default=5,
    show_default=True,
    type=click.IntRange(min=1),
    metavar="N",
    help="Max concurrent HTTP requests when fetching pages.",
)
@click.option(
    "--parse-concurrency",
    default=2,
    show_default=True,
    type=click.IntRange(min=1),
    metavar="N",
    help="Maximum concurrent HTML parsing jobs.",
)
@click.option(
    "--output",
    default="text",
    show_default=True,
    type=click.Choice(["text", "json"], case_sensitive=False),
    help="Output format. Use 'json' for agent/programmatic consumption.",
)
def search_cmd(
    query,
    additional_queries,
    engines,
    mode,
    search_concurrency,
    top_k,
    fetch_candidates,
    fetch,
    rank,
    region,
    time_filter,
    content_limit,
    max_response_bytes,
    timeout,
    concurrency,
    parse_concurrency,
    output,
):
    """Search one or more engines and return ranked results.

    \b
    Examples:
      kestrelsearch search "python async patterns" -k 3
      kestrelsearch search "rust ownership" --no-fetch --output json
      kestrelsearch search "climate news" --time-filter w --region us-en
      kestrelsearch search "react hooks" --content-limit 1000 --timeout 5
      kestrelsearch search "python typing" -q "pyright docs" -e duckduckgo -e bing --mode fanout
    """
    queries = (query, *additional_queries)
    query_label = " | ".join(queries)
    click.echo(
        f"[kestrelsearch] Searching {len(queries)} query(s) with "
        f"{', '.join(engines)} ({mode})...",
        err=True,
    )

    timings_ms: dict[str, int] = {}
    search_started = time.perf_counter()
    try:
        with span(
            "kestrel.search",
            {
                "kestrel.query_count": len(queries),
                "kestrel.engine_count": len(engines),
                "kestrel.search_mode": mode,
            },
        ):
            results = asyncio.run(
                async_search_many(
                    queries,
                    engines=engines,
                    mode=mode,
                    region=region,
                    time_filter=time_filter,
                    max_concurrency=search_concurrency,
                )
            )
    except (SearchError, ValueError) as exc:
        click.echo(f"[kestrelsearch] Search failed: {exc}", err=True)
        sys.exit(1)

    timings_ms["search"] = round((time.perf_counter() - search_started) * 1000)
    if not results:
        write_artifact(
            query_label,
            [],
            timings_ms,
            queries=list(queries),
            engines=list(engines),
            mode=mode,
        )
        click.echo("[kestrelsearch] No results found.", err=True)
        click.echo("[]" if output == "json" else "No results found.")
        sys.exit(0)

    click.echo(f"[kestrelsearch] Got {len(results)} results.", err=True)

    if fetch:
        # Search results are already round-robin merged, so taking a prefix keeps
        # query/engine diversity while bounding the expensive enrichment stage.
        candidate_limit = fetch_candidates or top_k * 3
        if len(results) > candidate_limit:
            click.echo(
                f"[kestrelsearch] Fetching the first {candidate_limit} candidates "
                f"before ranking (from {len(results)} search results).",
                err=True,
            )
            results = results[:candidate_limit]
        fetch_started = time.perf_counter()
        with span("kestrel.fetch", {"kestrel.result_count": len(results)}):
            _fetch_page_content(
                results,
                concurrency,
                parse_concurrency,
                content_limit,
                max_response_bytes,
                timeout,
            )
        timings_ms["fetch"] = round((time.perf_counter() - fetch_started) * 1000)

    if fetch and rank:
        click.echo("[kestrelsearch] Ranking with BM25...", err=True)
        rank_started = time.perf_counter()
        with span("kestrel.rank", {"kestrel.result_count": len(results)}):
            results = rank_results_by_query(results, queries)
        timings_ms["rank"] = round((time.perf_counter() - rank_started) * 1000)

    top_results = results[:top_k]
    write_artifact(
        query_label,
        top_results,
        timings_ms,
        queries=list(queries),
        engines=list(engines),
        mode=mode,
    )
    click.echo(f"[kestrelsearch] Returning top {len(top_results)} results.", err=True)

    if output == "json":
        click.echo(json.dumps(top_results, ensure_ascii=False, indent=2))
        return

    _render_text_results(top_results, query_label)


@main.group("skill", context_settings={"help_option_names": ["-h", "--help"]})
def skill_group():
    """Manage the kestrelsearch agent skill (SKILL.md)."""


def _prompt_install_options(agent: str | None, scope: str | None) -> tuple[str, str]:
    """Prompt for omitted skill-install options."""
    if agent is None:
        agent = click.prompt(
            "Which agent?",
            type=click.Choice(_AGENT_CHOICES, case_sensitive=False),
            default="all",
        )
    if scope is None:
        scope = click.prompt(
            "Install scope",
            type=click.Choice(["project", "global"], case_sensitive=False),
            default="project",
        )
    return agent, scope


def _skill_targets(agent: str, scope: str) -> list[Path]:
    """Return deduplicated installation paths for an agent selection."""
    agent_groups = {
        "both": ["claude", "vscode"],
        "all": ["claude", "vscode", "codex"],
    }
    agents = agent_groups.get(agent, [agent])
    paths = _GLOBAL_PATHS if scope == "global" else _PROJECT_PATHS
    return list(
        dict.fromkeys(paths[name] / _SKILL_NAME / "SKILL.md" for name in agents)
    )


def _write_skill_files(targets: list[Path], force: bool) -> None:
    """Write generated skills, asking before replacing existing files."""
    click.echo("\nWill write skill to:")
    for target in targets:
        click.echo(f"  {target}")
    click.echo()

    for target in targets:
        if (
            target.exists()
            and not force
            and not click.confirm(f"{target} already exists. Overwrite?", default=False)
        ):
            click.echo(f"  Skipped {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generate_skill_md(main), encoding="utf-8")
        record_installation(target)
        click.echo(f"  Installed: {target}")


@skill_group.command("install")
@click.option(
    "--agent",
    type=click.Choice(_AGENT_CHOICES, case_sensitive=False),
    default=None,
    help="Target agent. If omitted, you will be prompted.",
)
@click.option(
    "--scope",
    type=click.Choice(["project", "global"], case_sensitive=False),
    default=None,
    help="Install in the current project or globally for your user. If omitted, you will be prompted.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing SKILL.md without prompting.",
)
def skill_install(agent, scope, force):
    """Install the kestrelsearch SKILL.md for Claude Code, Codex, and/or VS Code Copilot.

    \b
    Skill locations
      Claude Code  project : .claude/skills/kestrelsearch/SKILL.md
      Claude Code  global  : ~/.claude/skills/kestrelsearch/SKILL.md
      VS Code      project : .github/skills/kestrelsearch/SKILL.md
      VS Code      global  : ~/.copilot/skills/kestrelsearch/SKILL.md
      Codex        project : .codex/skills/kestrelsearch/SKILL.md
      Codex        global  : ~/.codex/skills/kestrelsearch/SKILL.md

    ~/.claude/skills/ is recognised by both Claude Code and VS Code Copilot,
    so installing for Claude Code also activates the skill in VS Code.

    Use --agent all to install for every supported agent. The legacy --agent both
    option continues to install for Claude Code and VS Code Copilot.

    Installation paths are recorded in ~/.kestrelsearch/config.toml so that
    `kestrelsearch skill uninstall` can find and remove them later.
    """
    selected_agent, selected_scope = _prompt_install_options(agent, scope)
    _write_skill_files(_skill_targets(selected_agent, selected_scope), force)

    click.echo(f"\nInstallation paths recorded in {CONFIG_PATH}")
    click.echo("Restart your agent session to pick up the new skill.")


@skill_group.command("uninstall")
def skill_uninstall() -> None:
    """Remove previously installed SKILL.md files."""
    installations = get_installations()
    if not installations:
        click.echo("No skill installations recorded in config.")
        click.echo(f"(config: {CONFIG_PATH})")
        return

    existing = _remove_stale_installations(installations)
    if not existing:
        click.echo("\nNo skill files found on disk. Config has been cleaned up.")
        return

    selected = _select_installations(existing)
    if not selected:
        click.echo("Nothing selected. Aborting.")
        return

    _remove_skill_files(selected)
    click.echo("\nDone. Restart your agent session for changes to take effect.")


def _remove_stale_installations(installations: list[Path]) -> list[Path]:
    """Remove recorded paths that no longer exist and return the rest."""
    existing = [path for path in installations if path.exists()]
    stale = [path for path in installations if not path.exists()]
    if not stale:
        return existing

    click.echo(
        "\nThe following recorded paths no longer exist on disk (will be cleaned up):"
    )
    for path in stale:
        click.echo(f"  {path}")
        remove_installation(path)
    return existing


def _select_installations(existing: list[Path]) -> list[Path]:
    """Prompt the user to choose one or more existing skill files."""
    click.echo("\nInstalled skill locations:")
    for index, path in enumerate(existing, 1):
        click.echo(f"  [{index}] {path}")

    raw = click.prompt(
        "Which installation(s) to remove? (comma-separated numbers, or 'all')",
        default="all",
    ).strip()
    if raw.lower() == "all":
        return existing

    selected: list[Path] = []
    for entry in raw.split(","):
        selected_path = _path_from_selection(entry.strip(), existing)
        if selected_path is not None:
            selected.append(selected_path)
    return selected


def _path_from_selection(entry: str, existing: list[Path]) -> Path | None:
    """Resolve one interactive selection entry to a recorded path."""
    if not entry.isdigit():
        click.echo(f"  Skipping invalid entry: '{entry}'")
        return None
    index = int(entry) - 1
    if 0 <= index < len(existing):
        return existing[index]
    click.echo(f"  Index {entry} out of range, skipping.")
    return None


def _remove_skill_files(selected: list[Path]) -> None:
    """Remove selected skill files and their empty direct parent directories."""
    click.echo()
    for target in selected:
        try:
            target.unlink()
            with suppress(OSError):
                target.parent.rmdir()
            remove_installation(target)
            click.echo(f"  Removed: {target}")
        except OSError as exc:
            click.echo(f"  Failed to remove {target}: {exc}")
