import importlib
from unittest.mock import Mock

import httpx
import pytest

from kestrelsearch.search import _parse_results, search

search_module = importlib.import_module("kestrelsearch.search")

HTML = """
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a class="result__a" href="https://example.com">Example</a></h2>
  <a class="result__url">example.com</a>
  <a class="result__snippet">A useful result</a>
</div>
<div class="result results_links results_links_deep web-result"><h2></h2></div>
"""


def test_search_posts_filters_and_parses_results(monkeypatch):
    response = Mock(text=HTML)
    response.raise_for_status = Mock()
    post = Mock(return_value=response)
    monkeypatch.setattr(search_module.httpx, "post", post)

    results = search("kestrel", region="uk-en", time_filter="w")

    assert results == [
        {
            "title": "Example",
            "url": "https://example.com",
            "display_url": "example.com",
            "snippet": "A useful result",
            "content": None,
        }
    ]
    assert post.call_args.kwargs["data"] == {"q": "kestrel", "kl": "uk-en", "df": "w"}
    response.raise_for_status.assert_called_once()


def test_search_omits_default_filters(monkeypatch):
    response = Mock(text="")
    response.raise_for_status = Mock()
    post = Mock(return_value=response)
    monkeypatch.setattr(search_module.httpx, "post", post)

    assert search("kestrel", time_filter="any") == []
    assert post.call_args.kwargs["data"] == {"q": "kestrel"}


def test_search_propagates_http_errors(monkeypatch):
    request = httpx.Request("POST", "https://html.duckduckgo.com/html/")
    response = httpx.Response(500, request=request)
    monkeypatch.setattr(search_module.httpx, "post", Mock(return_value=response))

    with pytest.raises(httpx.HTTPStatusError):
        search("kestrel")


def test_parse_results_handles_missing_optional_fields():
    html = """
    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title"><a class="result__a">No URL</a></h2>
    </div>
    """

    assert _parse_results(html) == [
        {
            "title": "No URL",
            "url": "",
            "display_url": "",
            "snippet": "",
            "content": None,
        }
    ]
