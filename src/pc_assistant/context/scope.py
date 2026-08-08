"""Request-local identity used to scope durable memory."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryScope:
    principal_id: str
    session_id: str


_CURRENT_SCOPE: ContextVar[MemoryScope | None] = ContextVar(
    "pc_assistant_memory_scope",
    default=None,
)


def current_memory_scope() -> MemoryScope:
    scope = _CURRENT_SCOPE.get()
    if scope is None:
        raise RuntimeError("Memory scope is not bound to the current request")
    return scope


def set_memory_scope(scope: MemoryScope) -> Token[MemoryScope | None]:
    return _CURRENT_SCOPE.set(scope)


def reset_memory_scope(token: Token[MemoryScope | None]) -> None:
    _CURRENT_SCOPE.reset(token)
