"""Free web search: scrapes DuckDuckGo's no-JS HTML endpoint. No API key,
no per-query cost — the trade-off for "free" is that this is a best-effort
scrape rather than an official API, so if DuckDuckGo changes their markup
this parser may need updating. If it breaks, a free-tier API (Brave Search,
SerpAPI, etc.) is a drop-in replacement for this one function.
"""

from __future__ import annotations

import re

import requests

_RESULT_RE = re.compile(
    r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?'
    r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).strip()


def web_search(query: str, max_results: int = 5) -> str:
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; JarvisLocalAssistant/1.0)"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"Error: web search failed ({e})"

    results = []
    for url, title, snippet in _RESULT_RE.findall(resp.text):
        results.append(f"- {_strip_tags(title)}\n  {url}\n  {_strip_tags(snippet)}")
        if len(results) >= max_results:
            break

    return "\n".join(results) if results else "No results found."
