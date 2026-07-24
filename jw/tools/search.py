"""Web search tools.

Provides ``tavily_search``, ``duckduckgo_search`` and ``fetch_webpage_content``
for the research agent. Tavily is preferred when ``TAVILY_API_KEY`` is set;
otherwise a DuckDuckGo HTML fallback is used so research still works out of
the box.
"""

import asyncio
import os
import urllib.parse
from typing import Annotated, Literal

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import InjectedToolArg, tool
from markdownify import markdownify
from tavily import TavilyClient

# Lazy initialization - only create client when needed
_tavily_client = None


def _get_tavily_client() -> TavilyClient:
    """Get or create the Tavily client (lazy initialization)."""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient()
    return _tavily_client


def _has_tavily_key() -> bool:
    """Return True when a Tavily API key is available."""
    return bool(os.environ.get("TAVILY_API_KEY"))


async def fetch_webpage_content(url: str, timeout: float = 10.0) -> str:
    """Fetch and convert webpage content to markdown.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Webpage content as markdown
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return markdownify(response.text)
    except Exception as e:
        return f"Error fetching content from {url}: {e!s}"


async def _duckduckgo_html_search(
    query: str, max_results: int = 3
) -> list[dict[str, str]]:
    """Lightweight DuckDuckGo HTML search fallback (no API key required).

    Returns a list of ``{"title": ..., "url": ...}`` dicts. Full content is
    fetched separately via ``fetch_webpage_content`` so that timeouts or
    fetch failures do not break result discovery.
    """
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict[str, str]] = []
    for result in soup.find_all("div", class_="result"):
        if len(results) >= max_results:
            break
        title_a = result.find("a", class_="result__a")
        if not title_a:
            continue
        href = title_a.get("href")
        if not href:
            continue
        # DuckDuckGo redirects via /d.js?; extract the real URL if present.
        if href.startswith("//"):
            href = "https:" + href
        parsed = urllib.parse.urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.query:
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                href = urllib.parse.unquote(qs["uddg"][0])
            elif "q" in qs:
                href = urllib.parse.unquote(qs["q"][0])
        title = title_a.get_text(strip=True)
        snippet_tag = result.find("a", class_="result__snippet")
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
        results.append({"title": title, "url": href, "snippet": snippet})
    return results


@tool(parse_docstring=True)
async def tavily_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 3,
    topic: Annotated[
        Literal["general", "news", "finance"], InjectedToolArg
    ] = "general",
) -> str:
    """Search the web for information on a given query.

    Uses Tavily when ``TAVILY_API_KEY`` is set; otherwise falls back to a
    DuckDuckGo HTML search so research still works without an API key.
    Fetches and returns full webpage content as markdown for comprehensive
    research.

    Args:
        query: Search query to execute

    Returns:
        Formatted search results with full webpage content in markdown
    """

    if _has_tavily_key():

        def _sync_search() -> dict:
            return _get_tavily_client().search(
                query,
                max_results=max_results,
                topic=topic,
            )

        try:
            search_results = await asyncio.to_thread(_sync_search)
            results = search_results.get("results", [])
        except Exception as e:
            return f"Tavily search failed: {e!s}"
    else:
        try:
            results = await _duckduckgo_html_search(query, max_results=max_results)
        except Exception as e:
            return (
                f"Web search failed (no Tavily key, DuckDuckGo fallback error): {e!s}"
            )

    if not results:
        return f"No results found for '{query}'"

    # Fetch full content for each URL concurrently
    fetch_tasks = [fetch_webpage_content(r["url"]) for r in results]
    contents = await asyncio.gather(*fetch_tasks)

    # Format results
    result_texts = []
    for result, content in zip(results, contents, strict=False):
        result_text = f"""## {result["title"]}
**URL:** {result["url"]}

{content}

---
"""
        result_texts.append(result_text)

    source_note = (
        "via Tavily"
        if _has_tavily_key()
        else "via DuckDuckGo HTML fallback (no TAVILY_API_KEY set)"
    )
    return f"""Found {len(result_texts)} result(s) for '{query}' {source_note}:

{"".join(result_texts)}"""
