from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from pc_assistant.tools.base import ToolBase


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


class WebFetchTool(ToolBase):
    name = "web_fetch"
    description = "Fetch a URL as text."
    is_side_effecting = False

    async def execute(self, **kwargs: Any) -> Any:
        url = kwargs.get("url", "")
        if not url:
            return {"error": "url is required"}
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

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "http(s) URL"}},
                "required": ["url"],
            },
        }
