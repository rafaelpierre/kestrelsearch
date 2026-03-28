import httpx
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

from .fetcher import _UA


def search(query: str, region: str = "", time_filter: str = "") -> list[dict]:
    """Submit a search to DuckDuckGo HTML and return parsed results.

    Args:
        query: Search query string.
        region: DuckDuckGo region code (e.g. "us-en", "uk-en"). Empty = all regions.
        time_filter: Limit results by date ("d"=day, "w"=week, "m"=month, "y"=year).
    """
    data: dict[str, str] = {"q": query}
    if region:
        data["kl"] = region
    if time_filter and time_filter != "any":
        data["df"] = time_filter

    response = httpx.post(
        url="https://html.duckduckgo.com/html/",
        headers={"User-Agent": _UA.random},
        data=data,
        timeout=15.0,
    )
    response.raise_for_status()
    return _parse_results(response.text)


def _parse_results(html_content: str) -> list[dict]:
    """Parse raw DuckDuckGo HTML into a list of result dicts."""
    soup = BeautifulSoup(html_content, "html.parser")
    results = []

    for result_div in soup.find_all("div", class_="result results_links results_links_deep web-result"):
        try:
            title_link = result_div.find("h2", class_="result__title").find("a", class_="result__a")
            title = title_link.get_text(strip=True)
            url = title_link.get("href")

            display_url_elem = result_div.find("a", class_="result__url")
            display_url = display_url_elem.get_text(strip=True) if display_url_elem else ""

            snippet_elem = result_div.find("a", class_="result__snippet")
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

            results.append({
                "title": title,
                "url": url,
                "display_url": display_url,
                "snippet": snippet,
                "content": None,
            })
        except AttributeError:
            continue

    return results
