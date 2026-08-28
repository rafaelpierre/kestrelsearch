"""Async multi-provider search orchestration, normalization, and merging."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AsyncExitStack
from typing import Protocol
from urllib.parse import (
    parse_qs,
    unquote,
    unquote_plus,
    urlencode,
    urlparse,
    urlunparse,
)

import httpx
from bs4 import BeautifulSoup, Tag
from curl_cffi.requests import AsyncSession, RequestsError
from curl_cffi.requests import Response as CurlResponse
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .logging_utils import log_event

SearchResult = dict[str, object]
_SEARCH_TIMEOUT = 15.0
_TRACKING_PARAMS = {"fbclid", "gclid", "msclkid"}
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class SearchError(RuntimeError):
    """Raised when every requested search attempt fails."""


class SearchEngine(Protocol):
    """Contract implemented by each HTML search provider."""

    name: str

    async def search(
        self,
        query: str,
        *,
        region: str = "",
        time_filter: str = "",
        client: httpx.AsyncClient,
        yahoo_session: AsyncSession | None = None,
    ) -> list[SearchResult]: ...


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in {408, 429} or status >= 500
    return False


async def _request_with_retries(
    client: httpx.AsyncClient,
    *,
    engine: str,
    query: str,
    method: str,
    url: str,
    params: Mapping[str, str] | None = None,
    data: Mapping[str, str] | None = None,
) -> httpx.Response:
    """Make one search request, retrying transient HTTP failures."""

    def before_sleep(state: RetryCallState) -> None:
        error = state.outcome.exception() if state.outcome else None
        log_event(
            "search_retry",
            engine=engine,
            query=query,
            attempt=state.attempt_number,
            error_type=type(error).__name__ if error else "",
            error=str(error or ""),
        )

    retrying = AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.25, max=2.0),
        retry=retry_if_exception(_is_retryable),
        before_sleep=before_sleep,
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            response = await client.request(method, url, params=params, data=data)
            response.raise_for_status()
            return response
    raise AssertionError("Tenacity completed without returning or raising")


async def _request_yahoo_with_retries(
    query: str,
    params: Mapping[str, str],
    session: AsyncSession | None = None,
) -> CurlResponse:
    """Request Yahoo through a reusable browser-impersonating session.

    Multi-query orchestration supplies one invocation-scoped session so Yahoo
    requests reuse connections. Standalone calls retain ownership semantics by
    creating and closing a session here.
    """

    if session is None:
        async with AsyncSession(
            impersonate="chrome131", headers=_SEARCH_HEADERS
        ) as owned_session:
            return await _request_yahoo_with_retries(query, params, owned_session)

    def before_sleep(state: RetryCallState) -> None:
        error = state.outcome.exception() if state.outcome else None
        log_event(
            "search_retry",
            engine="yahoo",
            query=query,
            attempt=state.attempt_number,
            error_type=type(error).__name__ if error else "",
            error=str(error or ""),
        )

    retrying = AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.25, max=2.0),
        retry=retry_if_exception(_is_retryable),
        before_sleep=before_sleep,
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            try:
                response = await session.get(
                    "https://search.yahoo.com/search",
                    params=dict(params),
                    timeout=_SEARCH_TIMEOUT,
                )
            except RequestsError as exc:
                raise httpx.NetworkError(str(exc)) from exc
            if response.status_code >= 400:
                request = httpx.Request("GET", str(response.url))
                status_response = httpx.Response(response.status_code, request=request)
                raise httpx.HTTPStatusError(
                    f"Yahoo returned HTTP {response.status_code}",
                    request=request,
                    response=status_response,
                )
            return response
    raise AssertionError("Tenacity completed without returning or raising")


class DuckDuckGoSearchEngine:
    name = "duckduckgo"

    async def search(
        self,
        query: str,
        *,
        region: str = "",
        time_filter: str = "",
        client: httpx.AsyncClient,
        yahoo_session: AsyncSession | None = None,
    ) -> list[SearchResult]:
        data: dict[str, str] = {"q": query}
        if region:
            data["kl"] = region
        if time_filter and time_filter != "any":
            data["df"] = time_filter
        response = await _request_with_retries(
            client,
            engine=self.name,
            query=query,
            method="POST",
            url="https://html.duckduckgo.com/html/",
            data=data,
        )
        return _parse_results(response.text)


class BingSearchEngine:
    name = "bing"

    async def search(
        self,
        query: str,
        *,
        region: str = "",
        time_filter: str = "",
        client: httpx.AsyncClient,
        yahoo_session: AsyncSession | None = None,
    ) -> list[SearchResult]:
        params = {"q": query}
        if region:
            params["cc"] = region.split("-", 1)[0]
        if time_filter and time_filter != "any":
            log_event(
                "search_filter_unsupported",
                engine=self.name,
                query=query,
                filter="time_filter",
                value=time_filter,
            )
        response = await _request_with_retries(
            client,
            engine=self.name,
            query=query,
            method="GET",
            url="https://www.bing.com/search",
            params=params,
        )
        return _parse_bing_results(response.text)


class YahooSearchEngine:
    name = "yahoo"

    async def search(
        self,
        query: str,
        *,
        region: str = "",
        time_filter: str = "",
        client: httpx.AsyncClient,
        yahoo_session: AsyncSession | None = None,
    ) -> list[SearchResult]:
        params = {"p": query, "ei": "UTF-8"}
        if region:
            params["vl"] = region
        if time_filter and time_filter != "any":
            params["btf"] = time_filter
        response = await _request_yahoo_with_retries(query, params, yahoo_session)
        return _parse_yahoo_results(response.text)


ENGINE_REGISTRY: dict[str, SearchEngine] = {
    engine.name: engine
    for engine in (DuckDuckGoSearchEngine(), BingSearchEngine(), YahooSearchEngine())
}


async def async_search(
    query: str,
    region: str = "",
    time_filter: str = "",
    *,
    engine: str = "duckduckgo",
    client: httpx.AsyncClient | None = None,
    yahoo_session: AsyncSession | None = None,
) -> list[SearchResult]:
    """Run one provider search, closing only clients created by this call.

    Callers that batch searches can pass shared clients to retain connection
    pools across requests. The Yahoo session is separate because that provider
    requires curl-based browser impersonation rather than the HTTPX client.
    """
    provider = _get_engine(engine)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        headers=_SEARCH_HEADERS,
        timeout=_SEARCH_TIMEOUT,
        follow_redirects=True,
    )
    try:
        results = await provider.search(
            query,
            region=region,
            time_filter=time_filter,
            client=active_client,
            yahoo_session=yahoo_session,
        )
    except Exception as exc:
        log_event(
            "search_failed",
            engine=engine,
            query=query,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
    finally:
        if owns_client:
            await active_client.aclose()
    if not results:
        log_event("search_no_results", engine=engine, query=query)
    return results


def search(
    query: str,
    region: str = "",
    time_filter: str = "",
    *,
    engine: str = "duckduckgo",
) -> list[SearchResult]:
    """Synchronous compatibility wrapper for a single async search."""
    return asyncio.run(
        async_search(query, region=region, time_filter=time_filter, engine=engine)
    )


async def async_search_many(
    queries: Sequence[str],
    *,
    engines: Sequence[str] = ("duckduckgo",),
    mode: str = "fallback",
    region: str = "",
    time_filter: str = "",
    max_concurrency: int = 5,
) -> list[SearchResult]:
    """Search normalized unique queries with bounded, connection-reusing I/O.

    Fanout runs every query/engine pair. Fallback runs queries concurrently but
    tries each query's engines sequentially until one returns successfully. One
    HTTPX client and, when needed, one Yahoo session live for the whole batch.
    """
    clean_queries, clean_engines = _validate_search_request(
        queries, engines, mode, max_concurrency
    )
    semaphore = asyncio.Semaphore(max_concurrency)
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=_SEARCH_HEADERS,
                timeout=_SEARCH_TIMEOUT,
                follow_redirects=True,
            )
        )
        yahoo_session = None
        if "yahoo" in clean_engines:
            yahoo_session = await stack.enter_async_context(
                AsyncSession(impersonate="chrome131", headers=_SEARCH_HEADERS)
            )
        if mode == "fanout":
            outcomes = await _run_fanout(
                clean_queries,
                clean_engines,
                client,
                semaphore,
                yahoo_session,
                region,
                time_filter,
            )
        else:
            outcomes = await _run_fallback(
                clean_queries,
                clean_engines,
                client,
                semaphore,
                yahoo_session,
                region,
                time_filter,
            )
    return _merge_search_outcomes(outcomes, mode)


def _validate_search_request(
    queries: Sequence[str],
    engines: Sequence[str],
    mode: str,
    max_concurrency: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Normalize and deduplicate request dimensions while preserving order."""
    clean_queries = tuple(
        dict.fromkeys(query.strip() for query in queries if query.strip())
    )
    clean_engines = tuple(dict.fromkeys(engine.lower() for engine in engines))
    if not clean_queries:
        raise ValueError("At least one non-empty query is required")
    if not clean_engines:
        raise ValueError("At least one search engine is required")
    if mode not in {"fanout", "fallback"}:
        raise ValueError("mode must be 'fanout' or 'fallback'")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    for engine in clean_engines:
        _get_engine(engine)
    return clean_queries, clean_engines


async def _run_one(
    query: str,
    engine: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    yahoo_session: AsyncSession | None,
    region: str,
    time_filter: str,
) -> list[SearchResult]:
    """Run one query/provider pair under the shared limit and add provenance."""
    async with semaphore:
        results = await async_search(
            query,
            region=region,
            time_filter=time_filter,
            engine=engine,
            client=client,
            yahoo_session=yahoo_session,
        )
    return _with_provenance(results, engine=engine, query=query)


async def _run_fanout(
    queries: Sequence[str],
    engines: Sequence[str],
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    yahoo_session: AsyncSession | None,
    region: str,
    time_filter: str,
) -> list[list[SearchResult] | BaseException]:
    tasks = [
        _run_one(query, engine, client, semaphore, yahoo_session, region, time_filter)
        for query in queries
        for engine in engines
    ]
    return list(await asyncio.gather(*tasks, return_exceptions=True))


async def _run_with_fallback(
    query: str,
    engines: Sequence[str],
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    yahoo_session: AsyncSession | None,
    region: str,
    time_filter: str,
) -> list[SearchResult]:
    """Try providers sequentially for one query without blocking other queries."""
    errors: list[BaseException] = []
    for index, engine in enumerate(engines):
        try:
            return await _run_one(
                query,
                engine,
                client,
                semaphore,
                yahoo_session,
                region,
                time_filter,
            )
        except Exception as exc:  # noqa: BLE001 - isolate each provider
            errors.append(exc)
            log_event(
                "search_fallback",
                query=query,
                failed_engine=engine,
                next_engine=engines[index + 1] if index + 1 < len(engines) else "",
            )
    raise SearchError(
        f"All engines failed for query {query!r}: "
        + "; ".join(str(error) for error in errors)
    )


async def _run_fallback(
    queries: Sequence[str],
    engines: Sequence[str],
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    yahoo_session: AsyncSession | None,
    region: str,
    time_filter: str,
) -> list[list[SearchResult] | BaseException]:
    tasks = [
        _run_with_fallback(
            query,
            engines,
            client,
            semaphore,
            yahoo_session,
            region,
            time_filter,
        )
        for query in queries
    ]
    return list(await asyncio.gather(*tasks, return_exceptions=True))


def _merge_search_outcomes(
    outcomes: Sequence[list[SearchResult] | BaseException], mode: str
) -> list[SearchResult]:
    """Retain partial successes, failing only when every search failed."""
    buckets = [outcome for outcome in outcomes if isinstance(outcome, list)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    if not buckets and failures:
        raise SearchError(
            "Every search failed: " + "; ".join(str(error) for error in failures)
        )
    if failures:
        log_event(
            "search_partial_failure",
            mode=mode,
            failure_count=len(failures),
            success_count=len(buckets),
        )
    return _merge_round_robin(buckets)


def _get_engine(name: str) -> SearchEngine:
    try:
        return ENGINE_REGISTRY[name.lower()]
    except KeyError as exc:
        choices = ", ".join(ENGINE_REGISTRY)
        raise ValueError(
            f"Unknown search engine {name!r}; choose from: {choices}"
        ) from exc


def _with_provenance(
    results: Iterable[SearchResult], *, engine: str, query: str
) -> list[SearchResult]:
    decorated = []
    for rank, result in enumerate(results, start=1):
        item = dict(result)
        item.update(
            {
                "engine": engine,
                "query": query,
                "engine_rank": rank,
                "sources": [{"engine": engine, "query": query, "rank": rank}],
            }
        )
        decorated.append(item)
    return decorated


def _merge_round_robin(buckets: Sequence[Sequence[SearchResult]]) -> list[SearchResult]:
    """Merge provider buckets fairly while deduplicating canonical URLs."""
    merged: list[SearchResult] = []
    by_url: dict[str, SearchResult] = {}
    max_length = max((len(bucket) for bucket in buckets), default=0)
    for index in range(max_length):
        for bucket in buckets:
            if index >= len(bucket):
                continue
            _merge_result(bucket[index], merged, by_url)
    return merged


def _merge_result(
    item: SearchResult,
    merged: list[SearchResult],
    by_url: dict[str, SearchResult],
) -> None:
    key = _result_key(item)
    existing = by_url.get(key)
    if existing is not None:
        _extend_sources(existing, item)
        return
    by_url[key] = item
    merged.append(item)


def _result_key(item: SearchResult) -> str:
    key = _canonical_url(str(item.get("url") or ""))
    return key or f"{item.get('title', '')}\0{item.get('snippet', '')}"


def _extend_sources(existing: SearchResult, duplicate: SearchResult) -> None:
    existing_sources = existing.setdefault("sources", [])
    duplicate_sources = duplicate.get("sources")
    if isinstance(existing_sources, list) and isinstance(duplicate_sources, list):
        existing_sources.extend(duplicate_sources)


def _canonical_url(url: str) -> str:
    """Normalize URLs for deduplication without changing returned destinations."""
    if not url:
        return ""
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMS
        for value in values
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), "")
    )


def _result(title: str, url: str, display_url: str, snippet: str) -> SearchResult:
    return {
        "title": title.strip(),
        "url": url.strip(),
        "display_url": display_url.strip(),
        "snippet": snippet.strip(),
        "content": None,
    }


def _parse_results(html_content: str) -> list[SearchResult]:
    """Parse raw DuckDuckGo HTML into normalized result dictionaries."""
    soup = BeautifulSoup(html_content, "html.parser")
    results = []
    for result_div in soup.find_all(
        "div", class_="result results_links results_links_deep web-result"
    ):
        title_container = result_div.find("h2", class_="result__title")
        title_link = (
            title_container.find("a", class_="result__a") if title_container else None
        )
        if not isinstance(title_link, Tag):
            continue
        display_url_elem = result_div.find("a", class_="result__url")
        snippet_elem = result_div.find("a", class_="result__snippet")
        results.append(
            _result(
                title_link.get_text(strip=True),
                str(title_link.get("href") or ""),
                display_url_elem.get_text(strip=True) if display_url_elem else "",
                snippet_elem.get_text(strip=True) if snippet_elem else "",
            )
        )
    return results


def _decode_bing_url(url: str) -> str:
    if "/ck/a" not in url and "/cr?" not in url:
        return url
    values = parse_qs(urlparse(url).query)
    if values.get("rurl"):
        return unquote(values["rurl"][0])
    if not values.get("u"):
        return url
    encoded = values["u"][0]
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        encoded += "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(encoded).decode()
    except (ValueError, UnicodeDecodeError):
        return url


def _parse_bing_results(html_content: str) -> list[SearchResult]:
    soup = BeautifulSoup(html_content, "html.parser")
    results = []
    for item in soup.select("li.b_algo"):
        title_link = item.select_one("h2 a")
        if not isinstance(title_link, Tag):
            continue
        snippet = item.select_one(".b_caption p")
        display_url = item.select_one(".b_attribution cite, cite")
        results.append(
            _result(
                title_link.get_text(" ", strip=True),
                _decode_bing_url(str(title_link.get("href") or "")),
                display_url.get_text(" ", strip=True) if display_url else "",
                snippet.get_text(" ", strip=True) if snippet else "",
            )
        )
    return results


def _decode_yahoo_url(url: str) -> str:
    if "/RU=" not in url:
        return url
    encoded = url.split("/RU=", 1)[1].split("/RK=", 1)[0]
    return unquote_plus(encoded)


def _parse_yahoo_results(html_content: str) -> list[SearchResult]:
    soup = BeautifulSoup(html_content, "html.parser")
    items = soup.select("div.dd.algo") or soup.select("div.compTitle")
    parsed = (_parse_yahoo_item(item) for item in items)
    return [result for result in parsed if result is not None]


def _parse_yahoo_item(item: Tag) -> SearchResult | None:
    title_link = item.select_one(".compTitle > a, h3 a")
    if not isinstance(title_link, Tag):
        return None
    container = _yahoo_result_container(item)
    snippet = container.select_one(".compText p, .compText")
    url = _decode_yahoo_url(str(title_link.get("href") or ""))
    title = container.select_one("h3")
    return _result(
        title.get_text(" ", strip=True)
        if title
        else title_link.get_text(" ", strip=True),
        url,
        urlparse(url).netloc,
        snippet.get_text(" ", strip=True) if snippet else "",
    )


def _yahoo_result_container(item: Tag) -> Tag:
    is_title = "compTitle" in item.get_attribute_list("class")
    return item.parent if is_title and isinstance(item.parent, Tag) else item
