"""Request-local identity used to scope durable memory."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryScope:
    principal_id: str
    session_id: str


_DEFAULT_SCOPE = MemoryScope(principal_id="local", session_id="local:default")
_CURRENT_SCOPE: ContextVar[MemoryScope] = ContextVar(
    "pc_assistant_memory_scope",
    default=_DEFAULT_SCOPE,
)


def derive_memory_scope(session_id: str) -> MemoryScope:
    normalized = session_id or "local:default"
    if normalized.startswith("feishu:"):
        # Channel session IDs may add a conversation suffix later; durable
        # memory remains owned by the Feishu user, not by that conversation.
        parts = normalized.split(":", 2)
        principal_id = ":".join(parts[:2])
    elif normalized.startswith(("ws:", "tui:", "cli:", "local:")):
        principal_id = "local"
    else:
        # Unknown channel namespaces fail closed: they don't share memory with
        # unrelated sessions unless the transport adopts an explicit mapping.
        principal_id = f"session:{normalized}"
    return MemoryScope(principal_id=principal_id, session_id=normalized)


def current_memory_scope() -> MemoryScope:
    return _CURRENT_SCOPE.get()


def set_memory_scope(scope: MemoryScope) -> Token[MemoryScope]:
    return _CURRENT_SCOPE.set(scope)


def reset_memory_scope(token: Token[MemoryScope]) -> None:
    _CURRENT_SCOPE.reset(token)
