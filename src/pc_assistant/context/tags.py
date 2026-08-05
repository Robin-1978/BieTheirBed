"""XML delimiters + JSON payloads — structured context for LLM."""
from __future__ import annotations

import json
import re
import time
import ast
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

TAG_COMPACTED_HISTORY = "compacted_history"
TAG_RUNTIME_CONTEXT = "runtime_context"
TAG_TOOL_RESULT = "tool_result"
TAG_USER_REQUEST = "user_request"
TAG_CONTEXT_SUMMARY = "context_summary"

_COMPACTED_META_RE = re.compile(
    rf'^<{TAG_COMPACTED_HISTORY}\s+([^>]+)>',
    re.DOTALL,
)


def _xml_attr(value: str) -> str:
    return escape(str(value), {'"': "&quot;"})


# ---------------------------------------------------------------------------
# Runtime context
# ---------------------------------------------------------------------------

def is_runtime_context_message(msg: dict[str, Any]) -> bool:
    c = _content_text(msg.get("content")).strip()
    return msg.get("role") == "user" and c.startswith(f"<{TAG_RUNTIME_CONTEXT}")


def format_runtime_context(*blocks: str) -> str:
    body = "\n\n".join(b for b in blocks if b and b.strip())
    return f"<{TAG_RUNTIME_CONTEXT}>\n{body}\n</{TAG_RUNTIME_CONTEXT}>"


def is_session_context_message(msg: dict[str, Any]) -> bool:
    """Pinned session meta (time/role) — injected before the current dialogue turn."""
    if msg.get("role") != "user":
        return False
    c = _content_text(msg.get("content")).strip()
    if not c.startswith(f"<{TAG_RUNTIME_CONTEXT}>"):
        return False
    inner = strip_xml_wrapper(c, TAG_RUNTIME_CONTEXT)
    return inner.strip().startswith("<session>")


def is_strategy_context_message(msg: dict[str, Any]) -> bool:
    """Pinned strategy/memory block — injected at the start of each turn."""
    return is_runtime_context_message(msg) and not is_session_context_message(msg)


def format_session_context(current_time: str, *, working_dir: str = "", os_info: str = "") -> str:
    parts = [
        "<session>",
        f"<current_time>{escape(current_time)}</current_time>",
    ]
    if os_info:
        parts.append(f"<os_info>{escape(os_info)}</os_info>")
    if working_dir:
        parts.append(f"<working_directory>{escape(working_dir)}</working_directory>")
    parts.append("</session>")
    return format_runtime_context("\n".join(parts))


# ---------------------------------------------------------------------------
# Compacted history
# ---------------------------------------------------------------------------

def format_compacted_history(
    body_lines: list[str],
    *,
    covered_messages: int,
    keep_recent: int,
    source: str = "memory_trim",
) -> str:
    """Lossy history summary with explicit metadata."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    body = "\n".join(body_lines)
    attrs = (
        f'lossy="true" auto_compacted="true" '
        f'covered_messages="{covered_messages}" keep_recent="{keep_recent}" '
        f'generated_at="{ts}" source="{_xml_attr(source)}"'
    )
    return f"<{TAG_COMPACTED_HISTORY} {attrs}>\n{body}\n</{TAG_COMPACTED_HISTORY}>"


def is_compacted_history(content: Any) -> bool:
    c = _content_text(content).strip()
    return c.startswith(f"<{TAG_COMPACTED_HISTORY}")

def format_context_summary(body: str, *, covered_turns: int) -> str:
    # The body is intentionally Markdown, but it lives inside a tagged prompt
    # region. Prevent model-generated delimiter text from closing or nesting
    # the protocol envelope.
    safe_body = (body or "").strip()
    safe_body = re.sub(
        rf"</?{TAG_CONTEXT_SUMMARY}\b[^>]*>",
        lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        safe_body,
        flags=re.IGNORECASE,
    )
    return (
        f'<{TAG_CONTEXT_SUMMARY} lossy="true" covered_turns="{int(covered_turns)}">\n'
        f'{safe_body}\n</{TAG_CONTEXT_SUMMARY}>'
    )

def is_context_summary(content: Any) -> bool:
    return _content_text(content).strip().startswith(f"<{TAG_CONTEXT_SUMMARY}")


def parse_compacted_history_meta(content: Any) -> dict[str, str]:
    c = _content_text(content).strip()
    m = _COMPACTED_META_RE.match(c)
    if not m:
        return {}
    meta: dict[str, str] = {}
    for part in re.finditer(r'(\w+)="([^"]*)"', m.group(1)):
        meta[part.group(1)] = part.group(2)
    return meta


# ---------------------------------------------------------------------------
# Tool result wrapping
# ---------------------------------------------------------------------------

def wrap_tool_result(tool_name: str, payload: str, **attrs: Any) -> str:
    name = _xml_attr(tool_name)
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    if "status" not in attrs:
        parsed: Any = None
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            try:
                parsed = ast.literal_eval(body)
            except (SyntaxError, ValueError, TypeError):
                parsed = None
        attrs["status"] = "error" if isinstance(parsed, dict) and parsed.get("error") else "ok"
    extra = "".join(f' {_xml_attr(k)}="{_xml_attr(str(v))}"' for k, v in attrs.items())
    return f'<tool_result tool="{name}"{extra}>\n{body}\n</tool_result>'


def unwrap_tool_result(content: Any) -> str:
    c = _content_text(content).strip()
    if not c.startswith("<tool_result"):
        return c
    end = c.rfind("</tool_result>")
    if end < 0:
        return c
    return c[c.find(">") + 1:end].strip()


def tool_result_status(content: Any) -> str:
    """Read the explicit status marker from a wrapped tool result.

    This intentionally does not search result text for words like ``error``:
    schemas and ordinary output may contain those words while succeeding.
    """
    c = _content_text(content).strip()
    if not c.startswith(f"<{TAG_TOOL_RESULT}"):
        return "ok"
    try:
        root = ET.fromstring(c)
    except ET.ParseError:
        return "ok"
    return "error" if root.attrib.get("status") == "error" else "ok"


def parse_tool_result_payload(content: Any) -> Any:
    """Parse a wrapped result without interpreting arbitrary text."""
    body = unwrap_tool_result(content)
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        try:
            return ast.literal_eval(body)
        except (SyntaxError, ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Turn detection
# ---------------------------------------------------------------------------

def is_dialogue_user_turn(msg: dict[str, Any]) -> bool:
    """True for user messages that start a dialogue turn (excludes system injections)."""
    if msg.get("role") != "user":
        return False
    c = _content_text(msg.get("content")).strip()
    if is_compacted_history(c) or is_context_summary(c):
        return False
    if c.startswith(f"<{TAG_RUNTIME_CONTEXT}"):
        return False
    return True


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


def normalize_message_content(content: Any) -> str:
    return _content_text(content)


def strip_xml_wrapper(content: Any, tag: str) -> str:
    c = _content_text(content).strip()
    if not c.startswith(f"<{tag}"):
        return c
    start = c.find(">") + 1
    end = c.rfind(f"</{tag}>")
    if end < 0:
        return c
    return c[start:end].strip()


# ---------------------------------------------------------------------------
# Truncation limits
# ---------------------------------------------------------------------------

is_protected_history = is_compacted_history

PROTECTED_HISTORY_MAX = 1200
TOOL_RESULT_QUERY_MAX = 2000
TOOL_RESULT_DEFAULT_MAX = 1000


def truncate_text(text: str, max_len: int, *, suffix: str = "...[truncated]") -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


def tool_result_budget(tool_name: str) -> int:
    # Query-like tools get larger budget
    _query_tools = frozenset({"web", "shell", "filesystem"})
    if tool_name in _query_tools:
        return TOOL_RESULT_QUERY_MAX
    return TOOL_RESULT_DEFAULT_MAX
