import click

from kestrelsearch._skill_content import _get_examples, _param_info, generate_skill_md
from kestrelsearch.cli import main


def test_generate_skill_contains_live_cli_details():
    skill = generate_skill_md(main)

    assert "name: kestrelsearch" in skill
    assert "kestrelsearch search" in skill
    assert "Codex" in skill
    assert "--time-filter" in skill
    assert "--max-response-bytes" in skill
    assert "--parse-concurrency" in skill
    assert "three times `--top-k`" in skill


def test_example_and_option_helpers_handle_missing_values():
    def callback():
        """No examples."""

    option = click.Option(["--example"], help="A | B")

    assert _get_examples(callback) == []
    assert _param_info(option)["help"] == "A \\| B"
