import asyncio
import re
from html import unescape
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

# Single UA instance — instantiation is expensive
_UA = UserAgent()

_CLUTTER_PATTERNS = [
    "menu", "sidebar", "navbar", "topbar", "advertisement",
    "ad", "cookie", "modal", "popup", "banner", "nav", "breadcrumb",
]

_NOISE_LINE_PATTERNS = [
    r"^Source:", r"^Status:", r"^Use Case:", r"^Description:", r"^Stars:",
    r"^Verified:", r"^Purpose:", r"^Portability:", r"^Token Cost:",
]


def _parse_content(html: str, content_limit: int) -> Optional[str]:
    """Extract clean main-body text from raw HTML (lxml-backed, SEO-weighted)."""
    # lxml is ~3-5x faster than html.parser for large pages
    soup = BeautifulSoup(html, "lxml")

    for element in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        element.decompose()

    for element in list(soup.find_all(["div", "section"])):
        try:
            classes = element.get("class") or []
            elem_id = element.get("id") or ""
            if any(p in str(classes).lower() or p in elem_id.lower() for p in _CLUTTER_PATTERNS):
                element.decompose()
        except Exception:
            pass

    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(class_=re.compile(r"content|main|post|body", re.I))
    )
    if main_content:
        soup = main_content  # type: ignore[assignment]

    parts: list[str] = []
    for h1 in soup.find_all("h1"):
        text = h1.get_text(strip=True)
        if text and len(text) > 5:
            parts.extend([text] * 3)
    for h2 in soup.find_all("h2"):
        text = h2.get_text(strip=True)
        if text and len(text) > 5:
            parts.extend([text] * 2)
    for tag in soup.find_all(["p", "li"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 20:
            parts.append(text)

    combined = "\n".join(parts)
    for char in ("\u200b", "\u200d", "\u200c"):
        combined = combined.replace(char, "")
    combined = unescape(combined)

    deduped: list[str] = []
    prev: Optional[str] = None
    for line in combined.split("\n"):
        if line and line != prev:
            deduped.append(line)
        prev = line if line else None
    combined = "\n".join(deduped)

    combined = "\n".join(
        line for line in combined.split("\n")
        if not any(re.match(p, line) for p in _NOISE_LINE_PATTERNS)
    )
    combined = re.sub(r"\n\s*\n", "\n", combined)
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined[:content_limit] if combined else None


async def _fetch_one(
    url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    timeout: float,
    content_limit: int,
) -> Optional[str]:
    async with semaphore:
        try:
            response = await client.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
            return _parse_content(response.text, content_limit)
        except Exception:
            return None


async def fetch_all(
    urls: list[str],
    timeout: float = 10.0,
    content_limit: int = 2000,
    max_concurrency: int = 5,
) -> list[Optional[str]]:
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
                *[_fetch_one(url, client, semaphore, timeout, content_limit) for url in urls]
            )
        )
