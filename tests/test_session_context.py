from __future__ import annotations

from pathlib import Path

from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.context.session_context import (
    SessionContextRepository,
    SessionContextService,
)


class _Tokens:
    def messages_tokens(self, messages):
        return sum(len(str(message.get("content", ""))) for message in messages)


def _service(tmp_path: Path):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    service = SessionContextService(
        SessionContextRepository(database),
        token_estimator=_Tokens(),
        soft_token_limit=256,
        keep_recent_turns=2,
    )
    return scope, service


def test_session_context_compacts_old_turns_and_persists_summary(tmp_path: Path) -> None:
    scope, service = _service(tmp_path)
    messages = tuple(
        message
        for index in range(5)
        for message in (
            {"role": "user", "content": f"question-{index}-" + "x" * 40},
            {"role": "assistant", "content": f"answer-{index}-" + "y" * 40},
        )
    )

    record = service.compact(scope, messages)

    assert record.covered_messages == 6
    assert "question-0" in record.summary
    assert "answer-2" in record.summary
    assert "question-3" not in record.summary
    restored = service.context(scope)
    assert "session_rolling_summary" in restored
    assert 'covered_messages="6"' in restored


def test_session_context_does_not_compact_below_budget(tmp_path: Path) -> None:
    scope, service = _service(tmp_path)

    record = service.compact(
        scope,
        (
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ),
    )

    assert record.covered_messages == 0
    assert service.context(scope) == ""
