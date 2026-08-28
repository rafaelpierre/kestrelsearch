"""Bounded asynchronous page retrieval and off-loop HTML extraction."""

import asyncio
import re
from html import unescape
from typing import Final

import httpx
from bs4 import BeautifulSoup, Tag
from fake_useragent import UserAgent

from .logging_utils import log_event

# Single UA instance — instantiation is expensive
_UA = UserAgent()

DEFAULT_MAX_RESPONSE_BYTES: Final = 2_000_000
_SUPPORTED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

_CLUTTER_PATTERNS = [
    "menu",
    "sidebar",
    "navbar",
    "topbar",
    "advertisement",
    "ad",
    "cookie",
    "modal",
    "popup",
    "banner",
    "nav",
    "breadcrumb",
]

_NOISE_LINE_PATTERNS = [
    r"^Source:",
    r"^Status:",
    r"^Use Case:",
    r"^Description:",
    r"^Stars:",
    r"^Verified:",
    r"^Purpose:",
    r"^Portability:",
    r"^Token Cost:",
]


def _remove_page_chrome(soup: BeautifulSoup) -> None:
    """Remove elements that are unlikely to contain the page's main content."""
    for element in soup(
        ["script", "style", "nav", "header", "footer", "aside", "form"]
    ):
        element.decompose()

    for element in list(soup.find_all(["div", "section"])):
        if element.attrs is None:
            continue
        classes = str(element.get("class") or "").lower()
        elem_id = str(element.get("id") or "").lower()
        if any(
            pattern in classes or pattern in elem_id for pattern in _CLUTTER_PATTERNS
        ):
            element.decompose()


def _main_content(soup: BeautifulSoup) -> BeautifulSoup | Tag:
    """Return a likely main-content element, or the full document as a fallback."""
    return (
        soup.find("main")
        or soup.find("article")
        or soup.find(class_=re.compile(r"content|main|post|body", re.IGNORECASE))
        or soup
    )


def _extract_weighted_text(soup: BeautifulSoup | Tag) -> str:
    """Extract semantic text, weighting headings for later relevance ranking."""
    parts: list[str] = []
    for tag_name, weight, min_length in (
        ("h1", 3, 5),
        ("h2", 2, 5),
        ("p", 1, 20),
        ("li", 1, 20),
    ):
        for tag in soup.find_all(tag_name):
            text = tag.get_text(strip=True)
            if text and len(text) > min_length:
                parts.extend([text] * weight)
    return "\n".join(parts)


def _clean_text(text: str, content_limit: int) -> str | None:
    """Normalise extracted text, dropping duplicate and known-noise lines."""
    for char in ("\u200b", "\u200d", "\u200c"):
        text = text.replace(char, "")
    text = unescape(text)

    deduped: list[str] = []
    previous: str | None = None
    for line in text.split("\n"):
        if line and line != previous:
            deduped.append(line)
        previous = line or None

    cleaned = "\n".join(
        line
        for line in deduped
        if not any(re.match(pattern, line) for pattern in _NOISE_LINE_PATTERNS)
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:content_limit] if cleaned else None


def _parse_content(html: str, content_limit: int) -> str | None:
    """Extract clean main-body text from raw HTML (lxml-backed, SEO-weighted)."""
    # lxml is ~3-5x faster than html.parser for large pages
    soup = BeautifulSoup(html, "lxml")

    _remove_page_chrome(soup)
    return _clean_text(_extract_weighted_text(_main_content(soup)), content_limit)


async def _fetch_one(
    url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    timeout: float,
    content_limit: int,
    parse_semaphore: asyncio.Semaphore | None = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> str | None:
    """Download one bounded text response, then extract it off the event loop.

    The network semaphore is released before parsing starts. This keeps slow DOM
    construction from occupying a connection slot and lets other responses make
    progress while extraction runs in a worker thread. ``parse_semaphore`` is
    optional for direct callers; :func:`fetch_all` always supplies one.
    """
    try:
        async with (
            semaphore,
            client.stream(
                "GET", url, timeout=timeout, follow_redirects=True
            ) as response,
        ):
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not content_type.startswith(_SUPPORTED_CONTENT_TYPES):
                log_event(
                    "fetch_skipped",
                    url=url,
                    reason="unsupported_content_type",
                    content_type=content_type,
                )
                return None

            # Content-Length is only an early rejection hint. The streaming
            # check remains authoritative for missing or incorrect headers.
            declared_size = response.headers.get("content-length")
            if declared_size and int(declared_size) > max_response_bytes:
                log_event(
                    "fetch_skipped",
                    url=url,
                    reason="response_too_large",
                    response_bytes=int(declared_size),
                    max_response_bytes=max_response_bytes,
                )
                return None

            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_response_bytes:
                    log_event(
                        "fetch_skipped",
                        url=url,
                        reason="response_too_large",
                        response_bytes=len(body),
                        max_response_bytes=max_response_bytes,
                    )
                    return None
            encoding = response.encoding or "utf-8"

        html = body.decode(encoding, errors="replace")
        if parse_semaphore is None:
            return await asyncio.to_thread(_parse_content, html, content_limit)
        async with parse_semaphore:
            return await asyncio.to_thread(_parse_content, html, content_limit)
    except (httpx.HTTPError, UnicodeError, ValueError) as exc:
        log_event(
            "fetch_failed", url=url, error_type=type(exc).__name__, error=str(exc)
        )
        return None


async def fetch_all(
    urls: list[str],
    timeout: float = 10.0,
    content_limit: int = 2000,
    max_concurrency: int = 5,
    parse_concurrency: int = 2,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> list[str | None]:
    """Fetch and parse URLs over a shared client with separate resource limits.

    Response bodies are streamed and bounded before decoding or DOM creation.
    Network concurrency limits open requests and buffered bodies, while parse
    concurrency independently limits CPU- and memory-intensive extraction work.

    Args:
        urls: List of page URLs to fetch.
        timeout: Per-request HTTP timeout in seconds.
        content_limit: Maximum characters to extract per page.
        max_concurrency: Maximum simultaneous in-flight requests.
        parse_concurrency: Maximum simultaneous HTML extraction jobs.
        max_response_bytes: Maximum response body size accepted per page.

    Returns:
        List of extracted text strings (or None for failed fetches),
        in the same order as `urls`.

    Raises:
        ValueError: If any concurrency or response-size limit is less than one.
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    if parse_concurrency < 1:
        raise ValueError("parse_concurrency must be at least 1")
    if max_response_bytes < 1:
        raise ValueError("max_response_bytes must be at least 1")

    semaphore = asyncio.Semaphore(max_concurrency)
    parse_semaphore = asyncio.Semaphore(parse_concurrency)
    async with httpx.AsyncClient(
        http2=True,
        headers={"User-Agent": _UA.random},
        follow_redirects=True,
    ) as client:
        return list(
            await asyncio.gather(
                *[
                    _fetch_one(
                        url,
                        client,
                        semaphore,
                        timeout,
                        content_limit,
                        parse_semaphore,
                        max_response_bytes,
                    )
                    for url in urls
                ]
            )
        )
