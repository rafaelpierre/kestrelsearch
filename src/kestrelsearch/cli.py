import asyncio
import json
import sys
from pathlib import Path

import click

from ._config import CONFIG_PATH, get_installations, record_installation, remove_installation
from ._skill_content import generate_skill_md
from .fetcher import fetch_all
from .ranking import rank_results
from .search import search

_SKILL_NAME = "kestrelsearch"

# Paths where each agent looks for skills
_PROJECT_PATHS = {
    "claude":  Path(".claude/skills"),
    "vscode":  Path(".github/skills"),
    "codex":   Path(".codex/skills"),
}
_GLOBAL_PATHS = {
    "claude":  Path.home() / ".claude" / "skills",
    "vscode":  Path.home() / ".copilot" / "skills",
    "codex":   Path.home() / ".codex" / "skills",
}
_AGENT_CHOICES = ["claude", "vscode", "codex", "all", "both"]


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main():
    """Kestrel Search — web search, page extraction, and relevance ranking for AI agents."""


@main.command("search")
@click.argument("query")
@click.option("-k", "--top-k", default=5, show_default=True, metavar="N",
              help="Number of top results to return.")
@click.option("--fetch/--no-fetch", default=True, show_default=True,
              help="Fetch and parse full page content for each result.")
@click.option("--rank/--no-rank", default=True, show_default=True,
              help="Re-rank results using BM25 on fetched content (requires --fetch).")
@click.option("--region", default="", metavar="CODE",
              help="DuckDuckGo region code (e.g. us-en, uk-en). Defaults to all regions.")
@click.option("--time-filter", default="any", show_default=True,
              type=click.Choice(["any", "d", "w", "m", "y"], case_sensitive=False),
              help="Restrict by recency: any, d (day), w (week), m (month), y (year).")
@click.option("--content-limit", default=2000, show_default=True, metavar="CHARS",
              help="Maximum characters to extract per fetched page.")
@click.option("--timeout", default=10.0, show_default=True, metavar="SECS",
              help="HTTP timeout in seconds when fetching pages.")
@click.option("--concurrency", default=5, show_default=True, metavar="N",
              help="Max concurrent HTTP requests when fetching pages.")
@click.option("--output", default="text", show_default=True,
              type=click.Choice(["text", "json"], case_sensitive=False),
              help="Output format. Use 'json' for agent/programmatic consumption.")
def search_cmd(query, top_k, fetch, rank, region, time_filter, content_limit, timeout, concurrency, output):
    """Search DuckDuckGo and return ranked results.

    \b
    Examples:
      kestrelsearch search "python async patterns" -k 3
      kestrelsearch search "rust ownership" --no-fetch --output json
      kestrelsearch search "climate news" --time-filter w --region us-en
      kestrelsearch search "react hooks" --content-limit 1000 --timeout 5
    """
    click.echo(f"[kestrelsearch] Searching: '{query}'", err=True)

    try:
        results = search(query, region=region, time_filter=time_filter)
    except Exception as exc:
        click.echo(f"[kestrelsearch] Search failed: {exc}", err=True)
        sys.exit(1)

    if not results:
        click.echo("[kestrelsearch] No results found.", err=True)
        click.echo("[]" if output == "json" else "No results found.")
        sys.exit(0)

    click.echo(f"[kestrelsearch] Got {len(results)} results.", err=True)

    if fetch:
        fetchable = [
            (i, r["url"]) for i, r in enumerate(results)
            if ".pdf" not in r["url"].lower()
        ]
        click.echo(
            f"[kestrelsearch] Fetching {len(fetchable)} pages "
            f"(concurrency={concurrency})...",
            err=True,
        )
        fetched = asyncio.run(
            fetch_all(
                [url for _, url in fetchable],
                timeout=timeout,
                content_limit=content_limit,
                max_concurrency=concurrency,
            )
        )
        for (i, url), content in zip(fetchable, fetched):
            if content:
                results[i]["content"] = f"Source: {url}\n\n{content}"
            else:
                results[i]["content"] = None

        fetched_count = sum(1 for _, c in zip(fetchable, fetched) if c)
        click.echo(
            f"[kestrelsearch] Successfully fetched {fetched_count}/{len(fetchable)} pages.",
            err=True,
        )

    if fetch and rank:
        click.echo("[kestrelsearch] Ranking with BM25...", err=True)
        results = rank_results(results, query)

    top_results = results[:top_k]
    click.echo(f"[kestrelsearch] Returning top {len(top_results)} results.", err=True)

    if output == "json":
        click.echo(json.dumps(top_results, ensure_ascii=False, indent=2))
        return

    click.echo("\n" + "=" * 80)
    click.echo(f"Top {len(top_results)} results for: '{query}'")
    click.echo("=" * 80 + "\n")
    for i, result in enumerate(top_results, 1):
        score_str = f"  [BM25: {result['bm25_score']:.2f}]" if "bm25_score" in result else ""
        click.echo(f"{i}. {result['title']}{score_str}")
        click.echo(f"   {result['url']}")
        click.echo(f"   {result['snippet']}")
        if result.get("content"):
            click.echo(f"\n   {result['content']}\n")
        else:
            click.echo()


@main.group("skill", context_settings={"help_option_names": ["-h", "--help"]})
def skill_group():
    """Manage the kestrelsearch agent skill (SKILL.md)."""


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
@click.option("--force", is_flag=True, default=False,
              help="Overwrite an existing SKILL.md without prompting.")
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

    agent_groups = {
        "both": ["claude", "vscode"],
        "all": ["claude", "vscode", "codex"],
    }
    agents = agent_groups.get(agent, [agent])

    targets: list[Path] = []
    for ag in agents:
        base = _GLOBAL_PATHS[ag] if scope == "global" else _PROJECT_PATHS[ag]
        targets.append(base / _SKILL_NAME / "SKILL.md")

    # Deduplicate in case both agents resolved to the same path
    seen: set[Path] = set()
    unique_targets: list[Path] = []
    for t in targets:
        resolved = t.resolve() if t.exists() else t
        if resolved not in seen:
            seen.add(resolved)
            unique_targets.append(t)

    click.echo("\nWill write skill to:")
    for t in unique_targets:
        click.echo(f"  {t}")
    click.echo()

    for target in unique_targets:
        if target.exists() and not force:
            overwrite = click.confirm(f"{target} already exists. Overwrite?", default=False)
            if not overwrite:
                click.echo(f"  Skipped {target}")
                continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generate_skill_md(main), encoding="utf-8")
        record_installation(target)
        click.echo(f"  Installed: {target}")

    click.echo(f"\nInstallation paths recorded in {CONFIG_PATH}")
    click.echo("Restart your agent session to pick up the new skill.")


@skill_group.command("uninstall")
def skill_uninstall():
    """Remove previously installed SKILL.md files.

    Reads installation paths from ~/.kestrelsearch/config.toml and presents
    an interactive selection dialogue.
    """
    installations = get_installations()

    if not installations:
        click.echo("No skill installations recorded in config.")
        click.echo(f"(config: {CONFIG_PATH})")
        return

    # Split into existing-on-disk and stale (already deleted)
    existing = [p for p in installations if p.exists()]
    stale = [p for p in installations if not p.exists()]

    if stale:
        click.echo("\nThe following recorded paths no longer exist on disk (will be cleaned up):")
        for p in stale:
            click.echo(f"  {p}")
        for p in stale:
            remove_installation(p)

    if not existing:
        click.echo("\nNo skill files found on disk. Config has been cleaned up.")
        return

    click.echo("\nInstalled skill locations:")
    for i, p in enumerate(existing, 1):
        click.echo(f"  [{i}] {p}")

    click.echo()
    raw = click.prompt(
        "Which installation(s) to remove? (comma-separated numbers, or 'all')",
        default="all",
    ).strip()

    if raw.lower() == "all":
        selected = existing
    else:
        chosen: list[Path] = []
        for part in raw.split(","):
            part = part.strip()
            if not part.isdigit():
                click.echo(f"  Skipping invalid entry: '{part}'")
                continue
            idx = int(part) - 1
            if 0 <= idx < len(existing):
                chosen.append(existing[idx])
            else:
                click.echo(f"  Index {part} out of range, skipping.")
        selected = chosen

    if not selected:
        click.echo("Nothing selected. Aborting.")
        return

    click.echo()
    for target in selected:
        try:
            target.unlink()
            # Remove empty parent dir (the kestrelsearch/ skills folder)
            try:
                target.parent.rmdir()
            except OSError:
                pass  # not empty, that's fine
            remove_installation(target)
            click.echo(f"  Removed: {target}")
        except OSError as exc:
            click.echo(f"  Failed to remove {target}: {exc}")

    click.echo("\nDone. Restart your agent session for changes to take effect.")
