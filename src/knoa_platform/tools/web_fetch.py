from __future__ import annotations

import asyncio
import ipaddress
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from knoa_platform.tools.http_limits import (
    HttpResponseTooLargeError,
    read_limited_text,
)


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
    if parsed.username is not None or parsed.password is not None:
        return False, "URL credentials are not allowed"
    try:
        import socket

        addr_info = socket.getaddrinfo(hostname, None)
        if not addr_info:
            return False, "Hostname did not resolve"
        for family, _, _, _, sockaddr in addr_info:
            del family
            ip = ipaddress.ip_address(sockaddr[0])
            if not ip.is_global or ip.is_multicast:
                return False, f"Access to non-global IP is blocked: {ip}"
    except (OSError, ValueError):
        return False, "Hostname resolution failed"
    return True, ""


_MAX_REDIRECTS = 5
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_URL_LENGTH = 4096


class WebFetchTool(ToolBase):
    name = "web_fetch"
    description = "Fetch a URL as text."
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.NETWORK})
    risk = ToolRisk.MEDIUM

    async def execute(self, **kwargs: Any) -> Any:
        url = kwargs.get("url", "")
        if not url:
            return {"error": "url is required"}
        if not isinstance(url, str) or len(url) > _MAX_URL_LENGTH:
            return {"error": f"url must contain at most {_MAX_URL_LENGTH} characters"}
        try:
            import httpx

            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=30.0,
            ) as client:
                current_url = url
                for redirect_count in range(_MAX_REDIRECTS + 1):
                    safe, reason = await asyncio.to_thread(
                        _is_safe_url,
                        current_url,
                    )
                    if not safe:
                        return {"error": f"URL blocked: {reason}"}
                    async with client.stream("GET", current_url) as resp:
                        if resp.is_redirect:
                            if redirect_count >= _MAX_REDIRECTS:
                                return {"error": "URL blocked: Too many redirects"}
                            location = resp.headers.get("location")
                            if not location:
                                return {"error": "URL blocked: Redirect has no location"}
                            current_url = urljoin(current_url, location)
                            continue
                        resp.raise_for_status()
                        html = await read_limited_text(
                            resp,
                            _MAX_RESPONSE_BYTES,
                        )
                        status_code = resp.status_code
                        break
        except HttpResponseTooLargeError as e:
            return {"error": str(e)}
        except httpx.HTTPError as e:
            return {"error": f"HTTP error: {e}"}
        clean_text = _extract_clean_text(html)
        query = str(kwargs.get("query", "") or "").strip()
        focused_text = _extract_query_focused(clean_text, query=query, max_chars=1800)
        return {
            "content": focused_text,
            "url": url,
            "status_code": status_code,
            "total_chars": len(clean_text),
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Fetch a webpage as clean text. Optionally specify 'query' to extract "
                "and focus on the most relevant sections or answers directly."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "maxLength": _MAX_URL_LENGTH,
                        "description": "http or https URL to fetch",
                    },
                    "query": {
                        "type": "string",
                        "maxLength": 500,
                        "description": (
                            "Optional search term, question, or keywords to pinpoint and "
                            "extract the most relevant sections from the webpage"
                        ),
                    },
                },
                "required": ["url"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL"},
                    "query": {"type": "string", "description": "Optional search query"},
                },
                "required": ["url"],
            },
        }


def _extract_clean_text(html: str) -> str:
    """Extract clean, dense text from HTML using trafilatura with BeautifulSoup fallback."""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            include_links=False,
            include_images=False,
            include_tables=True,
            output_format="txt",
        )
        if extracted and len(extracted.strip()) > 40:
            return extracted.strip()
    except Exception:
        pass

    try:
        from bs4 import BeautifulSoup
        from markdownify import markdownify as md

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(
            [
                "script",
                "style",
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "svg",
                "iframe",
                "noscript",
            ]
        ):
            tag.decompose()
        for el in soup.find_all(
            class_=re.compile(r"cookie|banner|advertisement|sidebar|comment", re.I)
        ):
            el.decompose()
        text = md(str(soup), strip=["a", "img"])
    except Exception:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n", strip=True)

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_keywords(query: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_\-\.]+", query.lower())
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", query)
    cjk_ngrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return list(set(words + cjk_chars + cjk_ngrams))


def _score_paragraph(para: str, keywords: list[str], raw_query: str) -> float:
    lowered = para.lower()
    score = 0.0
    if raw_query.lower() in lowered:
        score += 10.0
    for kw in keywords:
        count = lowered.count(kw.lower())
        if count > 0:
            score += count * (len(kw) ** 0.5)
    return score


def _extract_query_focused(text: str, query: str = "", max_chars: int = 1800) -> str:
    """Extract query-relevant paragraphs or lead paragraphs up to max_chars."""
    if len(text) <= max_chars:
        return text

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return text[:max_chars]

    if query:
        keywords = _extract_keywords(query)
        scored = [
            (idx, _score_paragraph(p, keywords, query), p)
            for idx, p in enumerate(paragraphs)
        ]
        matching = [item for item in scored if item[1] > 0]
        if matching:
            matching.sort(key=lambda x: x[1], reverse=True)
            selected_indices: set[int] = set()
            accumulated_len = 0
            for idx, _score, p in matching:
                if accumulated_len + len(p) + 12 > max_chars:
                    if not selected_indices:
                        selected_indices.add(idx)
                    break
                selected_indices.add(idx)
                accumulated_len += len(p) + 12

            selected_paras = [paragraphs[idx] for idx in sorted(selected_indices)]
            assembled = "\n\n---\n\n".join(selected_paras)
            if len(assembled) > max_chars:
                assembled = assembled[:max_chars].rsplit("\n", 1)[0]
            remaining = len(text) - len(assembled)
            if remaining > 0:
                assembled += (
                    f"\n\n[... {remaining} chars omitted. Relevant excerpts for '{query}' shown above]"
                )
            return assembled

    selected: list[str] = []
    current_len = 0
    for p in paragraphs:
        if current_len + len(p) + 2 > max_chars:
            break
        selected.append(p)
        current_len += len(p) + 2

    if not selected:
        return text[:max_chars] + f"\n\n[... {len(text) - max_chars} chars omitted]"

    result = "\n\n".join(selected)
    omitted = len(text) - len(result)
    if omitted > 0:
        result += (
            f"\n\n[... {omitted} chars omitted. "
            "Use web_fetch(url=..., query='<topic>') to target specific sections]"
        )
    return result
