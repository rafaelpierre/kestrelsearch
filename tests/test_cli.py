import httpx
from click.testing import CliRunner

from kestrelsearch import cli


def test_search_command_outputs_json_without_fetching(monkeypatch):
    monkeypatch.setattr(
        cli,
        "search",
        lambda *args, **kwargs: [
            {
                "title": "Result",
                "url": "https://example.test",
                "display_url": "example.test",
                "snippet": "Snippet",
                "content": None,
            }
        ],
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
    monkeypatch.setattr(cli, "search", lambda *args, **kwargs: results)

    async def fetch_all(*args, **kwargs):
        return ["Page content"]

    monkeypatch.setattr(cli, "fetch_all", fetch_all)
    monkeypatch.setattr(cli, "rank_results", lambda values, query: values[:1])

    result = CliRunner().invoke(cli.main, ["search", "query"])

    assert result.exit_code == 0
    assert "Source: https://example.test" in result.output
    assert "PDF" not in result.output


def test_search_command_handles_empty_results_and_http_errors(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli, "search", lambda *args, **kwargs: [])
    empty = runner.invoke(cli.main, ["search", "query", "--output", "json"])
    assert empty.exit_code == 0
    assert empty.stdout.strip() == "[]"

    def raise_error(*args, **kwargs):
        request = httpx.Request("GET", "https://example.test")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(cli, "search", raise_error)
    failed = runner.invoke(cli.main, ["search", "query"])
    assert failed.exit_code == 1
    assert "Search failed" in failed.stderr


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
