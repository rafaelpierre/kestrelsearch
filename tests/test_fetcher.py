import asyncio

import httpx

from kestrelsearch import fetcher

PAGE = """
<html><body>
  <nav>Ignore navigation</nav><aside>Ignore sidebar</aside>
  <main><h1>Kestrel heading</h1><h2>Useful section</h2>
  <p>This is meaningful page content that should be preserved in the extraction.</p>
  <p>Source: ignored metadata</p></main>
</body></html>
"""


def test_parse_content_extracts_main_text_weights_headings_and_removes_noise():
    content = fetcher._parse_content(PAGE, 500)

    assert content is not None
    assert "Kestrel heading" in content
    assert "Useful section" in content
    assert "meaningful page content" in content
    assert "Ignore navigation" not in content
    assert "ignored metadata" not in content


def test_parse_content_returns_none_when_no_meaningful_text_exists():
    assert fetcher._parse_content("<html><body><p>short</p></body></html>", 100) is None


def test_parse_content_handles_nested_nodes_removed_with_their_parent():
    html = """
    <main>
      <div class="sidebar"><section class="nested">Ignored nested clutter</section></div>
      <p>This meaningful content remains available after nested clutter is removed.</p>
    </main>
    """

    content = fetcher._parse_content(html, 500)

    assert (
        content
        == "This meaningful content remains available after nested clutter is removed."
    )


def test_fetch_one_uses_mock_transport_and_handles_http_errors():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok":
            return httpx.Response(200, text=PAGE, request=request)
        return httpx.Response(500, request=request)

    async def run() -> tuple[str | None, str | None]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            semaphore = asyncio.Semaphore(1)
            good = await fetcher._fetch_one(
                "https://example.test/ok", client, semaphore, 1, 500
            )
            bad = await fetcher._fetch_one(
                "https://example.test/fail", client, semaphore, 1, 500
            )
            return good, bad

    good, bad = asyncio.run(run())

    assert good is not None
    assert bad is None


def test_fetch_all_uses_mocked_async_client(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE, request=request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        fetcher.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    results = asyncio.run(
        fetcher.fetch_all(["https://example.test/one", "https://example.test/two"])
    )

    assert len(results) == 2
    assert all(result is not None for result in results)
