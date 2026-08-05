from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlparse

from pc_assistant.tools.base import ToolBase, parameter, tool


def _is_safe_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme: {parsed.scheme}"
    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"
    try:
        import socket
        addr_info = socket.getaddrinfo(hostname, None)
        for family, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_reserved:
                return False, f"Access to private/reserved IP is blocked: {ip}"
    except Exception:
        pass
    return True, ""


@parameter("url", skim=True, skim_hint="fetch")
@parameter("query", skim=True, skim_hint="search")
@tool(name="web", description="Search the web or fetch a URL.", skim_description="Web search and fetch.")
class WebTool(ToolBase):
    name = "web"
    description = "Search the web and fetch web pages"

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action")
        handlers = {
            "fetch": self._fetch,
            "search": self._search,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown web action: {action}"}
        return await handler(kwargs)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["fetch", "search"],
                    },
                    "url": {"type": "string", "description": "URL to fetch"},
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max search results (default 5)"},
                },
                "required": ["action"],
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Search the web and fetch pages. Use search for queries, fetch for URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search", "fetch"]},
                    "query": {"type": "string"},
                    "url": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["action"],
            },
        }

    async def _fetch(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        url = kwargs.get("url", "")
        if not url:
            return {"error": "No URL provided"}
        safe, reason = _is_safe_url(url)
        if not safe:
            return {"error": f"URL blocked: {reason}"}
        try:
            import httpx

            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPError as e:
            return {"error": f"HTTP error: {e}"}
        try:
            from bs4 import BeautifulSoup
            from markdownify import markdownify as md

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = md(str(soup))
        except ImportError:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
        max_chars = 8000
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated, {len(text) - max_chars} chars omitted]"
        return {"content": text, "url": url, "status_code": resp.status_code}

    async def _search(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        query = kwargs.get("query", "")
        if not query:
            return {"error": "No query provided"}
        max_results = kwargs.get("max_results", 5)

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
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
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
                try:
                    from duckduckgo_search import DDGS

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
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
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
