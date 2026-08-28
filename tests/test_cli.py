from click.testing import CliRunner

from kestrelsearch import cli


def test_search_command_outputs_json_without_fetching(monkeypatch):
    async def search_many(*args, **kwargs):
        return [
            {
                "title": "Result",
                "url": "https://example.test",
                "display_url": "example.test",
                "snippet": "Snippet",
                "content": None,
                "engine": "duckduckgo",
                "query": "query",
            }
        ]

    monkeypatch.setattr(
        cli,
        "async_search_many",
        search_many,
    )

    result = CliRunner().invoke(
        cli.main, ["search", "query", "--no-fetch", "--output", "json"]
    )

    assert result.exit_code == 0
    assert '"title": "Result"' in result.output
    assert "Searching" in result.stderr


def test_search_command_fetches_ranks_and_prints_text(monkeypatch):
    results = [
        {
            "title": "Result",
            "url": "https://example.test",
            "display_url": "",
            "snippet": "Snippet",
            "content": None,
        },
        {
            "title": "PDF",
            "url": "https://example.test/file.pdf",
            "display_url": "",
            "snippet": "",
            "content": None,
        },
    ]

    async def search_many(*args, **kwargs):
        return results

    monkeypatch.setattr(cli, "async_search_many", search_many)

    async def fetch_all(*args, **kwargs):
        return ["Page content"]

    monkeypatch.setattr(cli, "fetch_all", fetch_all)
    monkeypatch.setattr(
        cli, "rank_results_by_query", lambda values, queries: values[:1]
    )

    result = CliRunner().invoke(cli.main, ["search", "query"])

    assert result.exit_code == 0
    assert "Source: https://example.test" in result.output
    assert "PDF" not in result.output


def test_search_command_handles_empty_results_and_http_errors(monkeypatch):
    runner = CliRunner()

    async def empty_results(*args, **kwargs):
        return []

    monkeypatch.setattr(cli, "async_search_many", empty_results)
    empty = runner.invoke(cli.main, ["search", "query", "--output", "json"])
    assert empty.exit_code == 0
    assert empty.stdout.strip() == "[]"

    async def raise_error(*args, **kwargs):
        raise cli.SearchError("offline")

    monkeypatch.setattr(cli, "async_search_many", raise_error)
    failed = runner.invoke(cli.main, ["search", "query"])
    assert failed.exit_code == 1
    assert "Search failed" in failed.stderr


def test_search_command_passes_multi_engine_fanout_options(monkeypatch):
    seen = {}

    async def search_many(queries, **kwargs):
        seen["queries"] = queries
        seen.update(kwargs)
        return []

    monkeypatch.setattr(cli, "async_search_many", search_many)
    result = CliRunner().invoke(
        cli.main,
        [
            "search",
            "first",
            "-q",
            "second",
            "-e",
            "bing",
            "-e",
            "yahoo",
            "--mode",
            "fanout",
            "--search-concurrency",
            "2",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert seen["queries"] == ("first", "second")
    assert seen["engines"] == ("bing", "yahoo")
    assert seen["mode"] == "fanout"
    assert seen["max_concurrency"] == 2


def test_search_command_bounds_fetch_candidates(monkeypatch):
    results = [
        {
            "title": f"Result {index}",
            "url": f"https://example.test/{index}",
            "display_url": "",
            "snippet": "snippet",
            "content": None,
            "query": "query",
        }
        for index in range(10)
    ]
    fetched_urls = []

    async def search_many(*args, **kwargs):
        return results

    async def bounded_fetch(urls, **kwargs):
        fetched_urls.extend(urls)
        return ["content"] * len(urls)

    monkeypatch.setattr(cli, "async_search_many", search_many)
    monkeypatch.setattr(cli, "fetch_all", bounded_fetch)
    monkeypatch.setattr(cli, "rank_results_by_query", lambda values, queries: values)

    result = CliRunner().invoke(
        cli.main,
        ["search", "query", "--top-k", "2", "--output", "json"],
    )

    assert result.exit_code == 0
    assert len(fetched_urls) == 6


def test_skill_helpers_and_install(monkeypatch, tmp_path):
    project_paths = {
        "claude": tmp_path / "claude",
        "vscode": tmp_path / "vscode",
        "codex": tmp_path / "codex",
    }
    monkeypatch.setattr(cli, "_PROJECT_PATHS", project_paths)
    monkeypatch.setattr(cli, "record_installation", lambda path: None)

    assert cli._skill_targets("all", "project") == [
        project_paths["claude"] / "kestrelsearch" / "SKILL.md",
        project_paths["vscode"] / "kestrelsearch" / "SKILL.md",
        project_paths["codex"] / "kestrelsearch" / "SKILL.md",
    ]
    result = CliRunner().invoke(
        cli.main, ["skill", "install", "--agent", "codex", "--scope", "project"]
    )
    target = project_paths["codex"] / "kestrelsearch" / "SKILL.md"
    assert result.exit_code == 0
    assert target.exists()
    assert "name: kestrelsearch" in target.read_text(encoding="utf-8")

    skipped = CliRunner().invoke(
        cli.main,
        ["skill", "install", "--agent", "codex", "--scope", "project"],
        input="n\n",
    )
    assert skipped.exit_code == 0
    assert "Skipped" in skipped.output


def test_skill_install_prompts_for_missing_options(monkeypatch, tmp_path):
    project_paths = {
        "claude": tmp_path / "claude",
        "vscode": tmp_path / "vscode",
        "codex": tmp_path / "codex",
    }
    monkeypatch.setattr(cli, "_PROJECT_PATHS", project_paths)
    monkeypatch.setattr(cli, "record_installation", lambda path: None)

    result = CliRunner().invoke(
        cli.main, ["skill", "install"], input="codex\nproject\n"
    )

    assert result.exit_code == 0
    assert (project_paths["codex"] / "kestrelsearch" / "SKILL.md").exists()


def test_skill_uninstall_no_installations(monkeypatch):
    monkeypatch.setattr(cli, "get_installations", lambda: [])

    result = CliRunner().invoke(cli.main, ["skill", "uninstall"])

    assert result.exit_code == 0
    assert "No skill installations" in result.output


def test_skill_uninstall_removes_stale_and_selected_files(monkeypatch, tmp_path):
    target = tmp_path / "skills" / "kestrelsearch" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("skill", encoding="utf-8")
    stale = tmp_path / "missing" / "SKILL.md"
    removed = []
    monkeypatch.setattr(cli, "get_installations", lambda: [stale, target])
    monkeypatch.setattr(cli, "remove_installation", removed.append)

    result = CliRunner().invoke(cli.main, ["skill", "uninstall"], input="all\n")

    assert result.exit_code == 0
    assert not target.exists()
    assert removed == [stale, target]


def test_skill_uninstall_rejects_an_empty_selection(monkeypatch, tmp_path):
    target = tmp_path / "SKILL.md"
    target.touch()
    monkeypatch.setattr(cli, "get_installations", lambda: [target])

    result = CliRunner().invoke(
        cli.main, ["skill", "uninstall"], input="not-a-number\n"
    )

    assert result.exit_code == 0
    assert "Nothing selected" in result.output
