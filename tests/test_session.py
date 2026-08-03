from __future__ import annotations

from pc_assistant.context.conversation import ConversationManager
from pc_assistant.harness.cancel import CancelToken
from pc_assistant.session import SessionManager, SessionState


class TestCancelToken:
    def test_default_not_cancelled(self):
        token = CancelToken()
        assert token.is_cancelled is False

    def test_cancel(self):
        token = CancelToken()
        token.cancel()
        assert token.is_cancelled is True

    def test_reset(self):
        token = CancelToken()
        token.cancel()
        token.reset()
        assert token.is_cancelled is False


class TestSessionState:
    def test_snapshot_and_rollback(self):
        conv = ConversationManager()
        conv.set_system_context("sys")
        state = SessionState(session_id="s", conversation=conv)
        state.mark_snapshot()  # snapshot at 0
        conv.add_user("hello")
        conv.add_assistant("hi")
        assert len(conv) == 2
        state.rollback_if_needed()
        assert len(conv) == 0

    def test_touch_updates_access(self):
        conv = ConversationManager()
        state = SessionState(session_id="s", conversation=conv)
        old = state.last_access
        state.touch()
        assert state.last_access >= old

    def test_to_dict(self):
        conv = ConversationManager()
        state = SessionState(session_id="s", conversation=conv)
        d = state.to_dict()
        assert d["session_id"] == "s"
        assert d["messages"] == 0


class TestSessionManager:
    def test_get_creates_and_reuses(self):
        mgr = SessionManager()
        s1 = mgr.get("a", "sys")
        s2 = mgr.get("a", "sys")
        assert s1 is s2

    def test_distinct_sessions_isolated(self):
        mgr = SessionManager()
        a = mgr.get("a", "sys")
        b = mgr.get("b", "sys")
        assert a is not b
        a.conversation.add_user("from a")
        assert len(b.conversation) == 0

    def test_lru_eviction(self):
        mgr = SessionManager(max_sessions=3)
        for i in range(4):
            mgr.get(f"s{i}", "sys")
        stats = mgr.stats()
        assert len(stats) == 3
        # Oldest (s0) evicted.
        ids = {s["session_id"] for s in stats}
        assert "s0" not in ids

    def test_drop(self):
        mgr = SessionManager()
        mgr.get("a", "sys")
        assert len(mgr) == 1
        mgr.drop("a")
        assert len(mgr) == 0
