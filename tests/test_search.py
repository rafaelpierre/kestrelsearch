import asyncio
import base64
import importlib
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from tenacity import wait_none

from kestrelsearch.search import (
    ENGINE_REGISTRY,
    SearchError,
    _canonical_url,
    _parse_bing_results,
    _parse_results,
    _parse_yahoo_results,
    _request_yahoo_with_retries,
    async_search,
    async_search_many,
    search,
)

search_module = importlib.import_module("kestrelsearch.search")

DDG_HTML = """
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a class="result__a" href="https://example.com">Example</a></h2>
  <a class="result__url">example.com</a>
  <a class="result__snippet">A useful result</a>
</div>
<div class="result results_links results_links_deep web-result"><h2></h2></div>
"""

BING_HTML = """
<ol><li class="b_algo">
  <h2><a href="https://www.bing.com/ck/a?u=a1{encoded}">Bing result</a></h2>
  <div class="b_attribution"><cite>example.com/bing</cite></div>
  <div class="b_caption"><p>Bing snippet</p></div>
</li></ol>
"""

YAHOO_HTML = """
<div class="dd algo">
  <h3><a href="https://r.search.yahoo.com/RU=https%3A%2F%2Fexample.com%2Fyahoo/RK=2/RS=x">Yahoo result</a></h3>
  <span class="fz-ms">example.com/yahoo</span>
  <div class="compText"><p>Yahoo snippet</p></div>
</div>
"""


def result(url="https://example.com", title="Example"):
    return {
        "title": title,
        "url": url,
        "display_url": "example.com",
        "snippet": "Snippet",
        "content": None,
    }


def test_async_duckduckgo_posts_filters_and_parses_results():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(200, text=DDG_HTML)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await async_search(
                "kestrel", region="uk-en", time_filter="w", client=client
            )

    results = asyncio.run(run())
    assert results == [result() | {"snippet": "A useful result"}]
    assert seen["request"].method == "POST"
    assert parse_qs(seen["request"].content.decode()) == {
        "q": ["kestrel"],
        "kl": ["uk-en"],
        "df": ["w"],
    }


def test_sync_search_wraps_async_api(monkeypatch):
    async_call = AsyncMock(return_value=[result()])
    monkeypatch.setattr(search_module, "async_search", async_call)

    assert search("kestrel", engine="bing") == [result()]
    async_call.assert_awaited_once_with(
        "kestrel", region="", time_filter="", engine="bing"
    )


def test_search_retries_transient_statuses_only(monkeypatch):
    monkeypatch.setattr(
        search_module, "wait_exponential_jitter", lambda **kwargs: wait_none()
    )
    request = httpx.Request("POST", "https://html.duckduckgo.com/html/")
    client = AsyncMock()
    client.request = AsyncMock(
        side_effect=[
            httpx.Response(500, request=request),
            httpx.Response(429, request=request),
            httpx.Response(200, text=DDG_HTML, request=request),
        ]
    )

    results = asyncio.run(async_search("kestrel", client=client))

    assert len(results) == 1
    assert client.request.await_count == 3


def test_search_does_not_retry_non_transient_status(monkeypatch):
    monkeypatch.setattr(
        search_module, "wait_exponential_jitter", lambda **kwargs: wait_none()
    )
    request = httpx.Request("POST", "https://html.duckduckgo.com/html/")
    client = AsyncMock()
    client.request = AsyncMock(return_value=httpx.Response(404, request=request))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(async_search("kestrel", client=client))
    assert client.request.await_count == 1


def test_engine_parsers_normalize_results_and_redirects():
    encoded = base64.urlsafe_b64encode(b"https://example.com/bing").decode().rstrip("=")
    assert _parse_bing_results(BING_HTML.format(encoded=encoded)) == [
        result("https://example.com/bing", "Bing result")
        | {"display_url": "example.com/bing", "snippet": "Bing snippet"}
    ]
    assert _parse_yahoo_results(YAHOO_HTML) == [
        result("https://example.com/yahoo", "Yahoo result")
        | {"display_url": "example.com", "snippet": "Yahoo snippet"}
    ]


def test_bing_provider_builds_region_params_and_ignores_time_filter():
    encoded = base64.urlsafe_b64encode(b"https://example.com/bing").decode().rstrip("=")
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(200, text=BING_HTML.format(encoded=encoded))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await async_search(
                "kestrel",
                engine="bing",
                region="uk-en",
                time_filter="w",
                client=client,
            )

    assert len(asyncio.run(run())) == 1
    assert dict(seen["request"].url.params) == {"q": "kestrel", "cc": "uk"}


def test_yahoo_provider_and_browser_impersonation_request(monkeypatch):
    seen = []

    class Response:
        status_code = 200
        text = YAHOO_HTML
        url = "https://search.yahoo.com/search?p=kestrel"

    class Session:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            seen.append(kwargs["params"])
            return Response()

    monkeypatch.setattr(search_module, "AsyncSession", Session)
    response = asyncio.run(
        _request_yahoo_with_retries("kestrel", {"p": "kestrel", "ei": "UTF-8"})
    )
    assert response.text == YAHOO_HTML

    request = httpx.Request("GET", "https://example.test")
    client = AsyncMock()
    client.request = AsyncMock(return_value=httpx.Response(200, request=request))
    results = asyncio.run(
        async_search(
            "kestrel", engine="yahoo", region="us-en", time_filter="d", client=client
        )
    )
    assert results[0]["title"] == "Yahoo result"
    assert seen[-1] == {"p": "kestrel", "ei": "UTF-8", "vl": "us-en", "btf": "d"}


def test_fanout_runs_every_pair_and_merges_duplicate_sources(monkeypatch):
    calls = []

    class Engine:
        def __init__(self, name):
            self.name = name

        async def search(self, query, **kwargs):
            calls.append((self.name, query))
            return [result(f"https://example.com/?utm_source={self.name}")]

    monkeypatch.setitem(ENGINE_REGISTRY, "one", Engine("one"))
    monkeypatch.setitem(ENGINE_REGISTRY, "two", Engine("two"))

    results = asyncio.run(
        async_search_many(["alpha", "beta"], engines=["one", "two"], mode="fanout")
    )

    assert set(calls) == {
        ("one", "alpha"),
        ("two", "alpha"),
        ("one", "beta"),
        ("two", "beta"),
    }
    assert len(results) == 1
    sources = results[0]["sources"]
    assert isinstance(sources, list)
    assert len(sources) == 4


def test_fanout_deduplicates_normalized_queries(monkeypatch):
    calls = []

    class Engine:
        name = "dedupe"

        async def search(self, query, **kwargs):
            calls.append(query)
            return [result(title=query)]

    monkeypatch.setitem(ENGINE_REGISTRY, "dedupe", Engine())

    asyncio.run(
        async_search_many(
            ["alpha", " alpha ", "beta"], engines=["dedupe"], mode="fanout"
        )
    )

    assert calls == ["alpha", "beta"]


def test_fanout_reuses_one_yahoo_session(monkeypatch):
    created = []

    class Response:
        status_code = 200
        text = YAHOO_HTML
        url = "https://search.yahoo.com/search"

    class Session:
        def __init__(self, **kwargs):
            self.requests = []
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            self.requests.append(kwargs["params"])
            return Response()

    monkeypatch.setattr(search_module, "AsyncSession", Session)

    results = asyncio.run(
        async_search_many(["alpha", "beta"], engines=["yahoo"], mode="fanout")
    )

    assert len(created) == 1
    assert len(created[0].requests) == 2
    assert len(results) == 1


def test_fallback_is_per_query_and_empty_results_are_success(monkeypatch):
    calls = []

    class First:
        name = "first"

        async def search(self, query, **kwargs):
            calls.append((self.name, query))
            if query == "fails":
                raise httpx.ConnectError("offline")
            return []

    class Second:
        name = "second"

        async def search(self, query, **kwargs):
            calls.append((self.name, query))
            return [result(title=query)]

    monkeypatch.setitem(ENGINE_REGISTRY, "first", First())
    monkeypatch.setitem(ENGINE_REGISTRY, "second", Second())

    results = asyncio.run(
        async_search_many(
            ["empty", "fails"], engines=["first", "second"], mode="fallback"
        )
    )

    assert calls == [("first", "empty"), ("first", "fails"), ("second", "fails")]
    assert [item["query"] for item in results] == ["fails"]


def test_all_failures_raise_search_error(monkeypatch):
    class Broken:
        name = "broken"

        async def search(self, query, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setitem(ENGINE_REGISTRY, "broken", Broken())
    with pytest.raises(SearchError, match="Every search failed"):
        asyncio.run(async_search_many(["query"], engines=["broken"]))


def test_parse_results_handles_missing_optional_fields_and_url_canonicalization():
    html = """
    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title"><a class="result__a">No URL</a></h2>
    </div>
    """
    assert _parse_results(html) == [
        result("", "No URL") | {"display_url": "", "snippet": ""}
    ]
    assert _canonical_url("HTTPS://Example.COM/path/?utm_source=x&a=1#top") == (
        "https://example.com/path?a=1"
    )
