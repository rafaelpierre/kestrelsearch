import json

from kestrelsearch import logging_utils


def test_log_event_writes_daily_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(logging_utils.Path, "home", lambda: tmp_path)

    logging_utils.log_event("search_no_results", query="orchids")

    log_file = next((tmp_path / ".kestrel" / "logs").glob("*/events.jsonl"))
    record = json.loads(log_file.read_text().strip())
    assert record["event"] == "search_no_results"
    assert record["query"] == "orchids"
