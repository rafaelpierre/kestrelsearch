import asyncio
import re
from html import unescape

import httpx
from bs4 import BeautifulSoup, Tag
from fake_useragent import UserAgent

# Single UA instance — instantiation is expensive
_UA = UserAgent()

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
) -> str | None:
    async with semaphore:
        try:
            response = await client.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
            return _parse_content(response.text, content_limit)
        except httpx.HTTPError:
            return None


async def fetch_all(
    urls: list[str],
    timeout: float = 10.0,
    content_limit: int = 2000,
    max_concurrency: int = 5,
) -> list[str | None]:
    """Fetch and parse multiple URLs concurrently over a shared HTTP/2 client.

    Args:
        urls: List of page URLs to fetch.
        timeout: Per-request HTTP timeout in seconds.
        content_limit: Maximum characters to extract per page.
        max_concurrency: Maximum simultaneous in-flight requests.

    Returns:
        List of extracted text strings (or None for failed fetches),
        in the same order as `urls`.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    async with httpx.AsyncClient(
        http2=True,
        headers={"User-Agent": _UA.random},
        follow_redirects=True,
    ) as client:
        return list(
            await asyncio.gather(
                *[
                    _fetch_one(url, client, semaphore, timeout, content_limit)
                    for url in urls
                ]
            )
        )
