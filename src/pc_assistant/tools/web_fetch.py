from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urljoin, urlparse

from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from pc_assistant.tools.http_limits import (
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
        return {"content": text, "url": url, "status_code": status_code}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "maxLength": _MAX_URL_LENGTH},
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
                "properties": {"url": {"type": "string", "description": "http(s) URL"}},
                "required": ["url"],
            },
        }
