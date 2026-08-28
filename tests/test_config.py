from pathlib import Path

from kestrelsearch import _config


def test_records_deduplicates_and_removes_installations(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(_config, "CONFIG_PATH", config_path)
    skill_path = tmp_path / "skills" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.touch()

    _config.record_installation(skill_path)
    _config.record_installation(skill_path)

    assert _config.get_installations() == [skill_path.resolve()]
    assert "installations" in config_path.read_text()

    _config.remove_installation(skill_path)

    assert _config.get_installations() == []


def test_remove_installation_handles_missing_skill_table(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("title = 'empty'\n", encoding="utf-8")
    monkeypatch.setattr(_config, "CONFIG_PATH", config_path)

    _config.remove_installation(Path("missing"))

    assert config_path.read_text(encoding="utf-8") == "title = 'empty'\n"
