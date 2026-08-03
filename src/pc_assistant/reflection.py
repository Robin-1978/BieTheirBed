"""Reflection: self-critique before yielding a final answer.

Only activates for tasks that warrant the extra LLM call — complex multi-tool
tasks, system-modifying operations, or long answers.  Skips trivial queries
(greetings, single-tool lookups, short factual responses) to avoid wasting
tokens.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pc_assistant.llm_provider import LLMProvider

_REFLECTION_SYSTEM = """\
You are a quality reviewer.  Given the user's original question and the
assistant's draft answer, evaluate whether the answer is:
1. Correct and factually accurate
2. Complete — addresses all parts of the question
3. Helpful — actionable and well-structured

Output JSON only:
{"score": <0-10>, "critique": "<one sentence if score < 7, empty if >= 7>"}
"""

_RISKY_PATTERNS = re.compile(
    r"\b(rm\s|delete|remove|install|uninstall|deploy|migrate|format|config|修改|删除|安装|部署)\b",
    re.IGNORECASE,
)


class ReflectionChecker:
    def __init__(
        self,
        llm: LLMProvider,
        threshold: int = 7,
        min_tool_calls: int = 2,
        min_answer_length: int = 300,
    ) -> None:
        self._llm = llm
        self._threshold = threshold
        self._min_tool_calls = min_tool_calls
        self._min_answer_length = min_answer_length

    def should_reflect(
        self,
        user_input: str,
        draft_answer: str,
        tool_call_count: int,
    ) -> bool:
        """Decide whether reflection is worth the extra LLM call."""
        if tool_call_count >= self._min_tool_calls:
            return True
        if len(draft_answer) >= self._min_answer_length:
            return True
        if _RISKY_PATTERNS.search(user_input):
            return True
        return False

    async def check(
        self,
        user_input: str,
        draft_answer: str,
        tool_results: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Returns (passes, critique).  passes=True means answer is good enough."""
        context = f"User question: {user_input}\n\nDraft answer:\n{draft_answer}"
        if tool_results:
            context += "\n\nTool results used:\n" + "\n".join(tool_results[:3])

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _REFLECTION_SYSTEM},
            {"role": "user", "content": context},
        ]
        resp = await self._llm.chat(messages, tools=None, max_tokens=256)
        if resp.finish_reason == "error":
            return True, ""

        return self._parse(resp.content)

    def _parse(self, raw: str) -> tuple[bool, str]:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return True, ""
        score = int(data.get("score", 10))
        critique = data.get("critique", "")
        return score >= self._threshold, critique
