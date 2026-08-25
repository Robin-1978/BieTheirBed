"""DingTalk-specific projection for the channel-neutral task card shape.

The task presentation is shared with Feishu, but its rendered card payload is
not portable: Feishu markdown accepts HTML-like ``font`` tags while DingTalk's
Markdown card displays them literally.  This module is the explicit channel
boundary that converts the common card shape into DingTalk title/Markdown.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Mapping

from knoa_platform.branding import ASSISTANT_NAME


_FONT = re.compile(r"<font\b[^>]*>(.*?)</font\s*>", re.IGNORECASE | re.DOTALL)
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLOCK = re.compile(r"</?(?:div|p|section|article|ul|ol|li)\b[^>]*>", re.IGNORECASE)
_STRONG_OPEN = re.compile(r"<(?:strong|b)\b[^>]*>", re.IGNORECASE)
_STRONG_CLOSE = re.compile(r"</(?:strong|b)\s*>", re.IGNORECASE)
_EM_OPEN = re.compile(r"<(?:em|i)\b[^>]*>", re.IGNORECASE)
_EM_CLOSE = re.compile(r"</(?:em|i)\s*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"</?[a-z][^>\n]*>", re.IGNORECASE)
_EXCESS_BLANKS = re.compile(r"\n{3,}")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


@dataclass(frozen=True)
class DingTalkCardProjection:
    title: str
    markdown: str

    def as_text(self) -> str:
        body = self.markdown.strip()
        return f"{self.title}\n\n{body}" if body else self.title


def _plain_html_fragment(value: str) -> str:
    value = html.unescape(value)
    value = _BR.sub("\n", value)
    value = _BLOCK.sub("\n", value)
    value = _STRONG_OPEN.sub("**", value)
    value = _STRONG_CLOSE.sub("**", value)
    value = _EM_OPEN.sub("*", value)
    value = _EM_CLOSE.sub("*", value)
    return _HTML_TAG.sub("", value)


def _muted(match: re.Match[str]) -> str:
    body = _plain_html_fragment(match.group(1)).strip()
    return "\n".join(f"> {line}" if line else ">" for line in body.splitlines())


def dingtalk_markdown(value: str) -> str:
    """Translate supported rich text and remove unsupported HTML/table syntax.

    The built-in DingTalk AI Card V2 template accepts a small Markdown subset
    and can render an empty body when a GitLab-style table is passed through.
    Convert tables to bullets at the channel boundary so notification cards
    remain readable instead of relying on template-specific table support.
    """
    rendered: list[str] = []
    plain: list[str] = []
    fence = ""

    def flush_plain() -> None:
        if not plain:
            return
        segment = "".join(plain)
        segment = _FONT.sub(_muted, segment)
        rendered.append(_plain_html_fragment(segment))
        plain.clear()

    for line in str(value).splitlines(keepends=True):
        marker = line.lstrip()[:3]
        if not fence and marker in {"```", "~~~"}:
            flush_plain()
            fence = marker
            rendered.append(line)
        elif fence:
            rendered.append(line)
            if marker == fence:
                fence = ""
        else:
            plain.append(line)
    flush_plain()
    normalized = _EXCESS_BLANKS.sub("\n\n", "".join(rendered)).strip()
    return _tables_to_bullets(normalized)


def _tables_to_bullets(value: str) -> str:
    lines = value.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and "|" in lines[index]
            and _TABLE_SEPARATOR.match(lines[index + 1])
        ):
            headers = [part.strip() for part in lines[index].strip().strip("|").split("|")]
            index += 2
            rows: list[str] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                cells = [part.strip() for part in lines[index].strip().strip("|").split("|")]
                if len(cells) < len(headers):
                    cells.extend([""] * (len(headers) - len(cells)))
                pairs = [f"{header}: {cell}" for header, cell in zip(headers, cells) if cell]
                if pairs:
                    rows.append("- " + "；".join(pairs))
                index += 1
            if rows:
                output.extend(rows)
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def project_dingtalk_card(card: Mapping[str, Any]) -> DingTalkCardProjection:
    title = str(
        card.get("header", {}).get("title", {}).get("content")
        or ASSISTANT_NAME
    )
    parts: list[str] = []
    elements = card.get("body", {}).get("elements", [])
    if isinstance(elements, list):
        for element in elements:
            if not isinstance(element, Mapping):
                continue
            if element.get("tag") == "hr":
                parts.append("---")
                continue
            content = element.get("content")
            if content is not None:
                normalized = dingtalk_markdown(str(content))
                if normalized:
                    parts.append(normalized)
    return DingTalkCardProjection(title=title, markdown="\n\n".join(parts))


__all__ = [
    "DingTalkCardProjection",
    "dingtalk_markdown",
    "project_dingtalk_card",
]
