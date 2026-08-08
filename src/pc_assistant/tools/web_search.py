from __future__ import annotations

import asyncio
from typing import Any

from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from pc_assistant.tools.http_limits import read_limited_text


_MAX_RESULTS = 10
_MAX_SEARCH_RESPONSE_BYTES = 2 * 1024 * 1024


class WebSearchTool(ToolBase):
    name = "web_search"
    description = "Search the web."
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.NETWORK})
    risk = ToolRisk.LOW

    async def execute(self, **kwargs: Any) -> Any:
        query = kwargs.get("query", "")
        if not query:
            return {"error": "query is required"}
        if not isinstance(query, str) or len(query) > 500:
            return {"error": "query must contain at most 500 characters"}
        try:
            max_results = max(1, min(_MAX_RESULTS, int(kwargs.get("max_results", 5))))
        except (TypeError, ValueError):
            return {"error": "max_results must be an integer"}

        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in query)

        if has_chinese:
            bing_result = await self._search_bing(query, max_results)
            if bing_result is not None and bing_result.get("results"):
                return bing_result
            ddgs_result = await self._search_ddgs(query, max_results)
            if ddgs_result is not None and ddgs_result.get("results"):
                return ddgs_result
            return await self._search_http(query, max_results)
        else:
            ddgs_result = await self._search_ddgs(query, max_results)
            if ddgs_result is not None and ddgs_result.get("results"):
                return ddgs_result
            bing_result = await self._search_bing(query, max_results)
            if bing_result is not None and bing_result.get("results"):
                return bing_result
            return await self._search_http(query, max_results)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 500},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_RESULTS,
                        "description": "Default 5",
                    },
                },
                "required": ["query"],
            },
        }

    async def _search_bing(self, query: str, max_results: int) -> dict[str, Any] | None:
        try:
            import httpx
            from urllib.parse import quote_plus

            has_chinese = any('\u4e00' <= c <= '\u9fff' for c in query)
            lang_param = "&setlang=zh-Hans&cc=CN" if has_chinese else "&setlang=en"
            url = f"https://www.bing.com/search?q={quote_plus(query)}{lang_param}"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers=headers) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    html = await read_limited_text(
                        resp,
                        _MAX_SEARCH_RESPONSE_BYTES,
                    )
        except Exception:
            return None

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            results: list[dict[str, str]] = []
            seen_urls: set[str] = set()

            for item in soup.select(".b_algo"):
                if len(results) >= max_results:
                    break

                title_el = item.select_one("h2 a")
                if not title_el:
                    continue

                href = title_el.get("href", "")
                if not href or href in seen_urls:
                    continue
                seen_urls.add(href)

                snippet_el = item.select_one(".b_caption p, .b_lineclamp2")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": href,
                    "snippet": snippet,
                })

            if not results:
                return None

            return {"results": results, "query": query, "count": len(results)}
        except ImportError:
            return None
        except Exception:
            return None

    async def _search_ddgs(self, query: str, max_results: int) -> dict[str, Any] | None:
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in query)
        region = "cn-zh" if has_chinese else None

        def _do_search() -> list[dict[str, Any]] | None:
            try:
                from ddgs import DDGS

                with DDGS() as ddgs:
                    kwargs = {"max_results": max_results}
                    if region:
                        kwargs["region"] = region
                    return list(ddgs.text(query, **kwargs))
            except ImportError:
                return None
            except Exception:
                return None

        results = await asyncio.to_thread(_do_search)
        if results is None:
            return None

        if not results:
            return {"results": [], "query": query, "message": "No results found"}

        formatted = []
        seen_urls: set[str] = set()
        for r in results:
            url = r.get("href", r.get("link", ""))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            snippet = r.get("body", r.get("snippet", ""))
            if not snippet:
                continue
            formatted.append({
                "title": r.get("title", ""),
                "url": url,
                "snippet": snippet,
            })

        return {"results": formatted, "query": query, "count": len(formatted)}

    async def _search_http(self, query: str, max_results: int) -> dict[str, Any]:
        try:
            import httpx
            from urllib.parse import quote_plus

            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            }) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    html = await read_limited_text(
                        resp,
                        _MAX_SEARCH_RESPONSE_BYTES,
                    )
        except Exception as e:
            return {"error": f"Search error: {e}", "query": query}

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            results = []
            seen_urls: set[str] = set()
            for item in soup.select(".result"):
                if len(results) >= max_results:
                    break
                title_el = item.select_one(".result__title a, .result__a")
                snippet_el = item.select_one(".result__snippet")
                if title_el:
                    href = title_el.get("href", "")
                    if href.startswith("//"):
                        href = "https:" + href
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    if not snippet:
                        continue
                    results.append({
                        "title": title_el.get_text(strip=True),
                        "url": href,
                        "snippet": snippet,
                    })
        except ImportError:
            return {"error": "beautifulsoup4 not available for search", "query": query}

        return {"results": results, "query": query, "count": len(results)}
