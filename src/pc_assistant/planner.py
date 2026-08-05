"""Optional Plan-Execute layer for complex multi-step tasks.

The planner generates a structured plan *without executing any tools*.  The
agent loop then executes each step, optionally re-planning when intermediate
results change the picture.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pc_assistant.llm_provider import LLMProvider


_PLAN_SYSTEM = """\
You are a task planner.  Given the user's request, produce a JSON plan.
The plan is a list of steps.  Each step has:
- "description": what to do (one sentence)
- "expected_tool": the tool name most likely needed (or null)
- "depends_on": list of 0-based step indices this step depends on

Rules:
- Do NOT execute anything yourself — only output the plan.
- Keep it concise: usually 2-6 steps.
- Output valid JSON only, no markdown fences.
"""

_COMPLEXITY_KEYWORDS = re.compile(
    r"\b(step[- ]?by[- ]?step|first.*then.*(?:finally|after|next)"
    r"|batch.*(?:process|update|migrate)|migrate.*(?:from|to)"
    r"|refactor.*(?:across|all|entire)|deploy.*(?:and|then)"
    r"|install.*(?:and.*configure|then.*setup))\b",
    re.IGNORECASE,
)

_SIMPLE_PATTERNS = re.compile(
    r"^\s*(?:open|launch|run|start|show|list|check|what|who|when|where|how much"
    r"|打开|启动|查看|显示|列出|多少|天气|时间|帮我查)\b",
    re.IGNORECASE,
)


@dataclass
class PlanStep:
    index: int
    description: str
    expected_tool: str | None = None
    depends_on: list[int] = field(default_factory=list)
    status: str = "pending"
    result_summary: str = ""


@dataclass
class StructuredPlan:
    steps: list[PlanStep]
    original_input: str = ""

    def current_step(self) -> PlanStep | None:
        for s in self.steps:
            if s.status == "pending":
                return s
        return None

    def mark_done(self, index: int, summary: str = "") -> None:
        if 0 <= index < len(self.steps):
            self.steps[index].status = "done"
            self.steps[index].result_summary = summary

    def all_done(self) -> bool:
        return all(s.status == "done" for s in self.steps)

    def to_prompt(self) -> str:
        lines = ["Plan:"]
        for s in self.steps:
            marker = "x" if s.status == "done" else " "
            lines.append(f"  [{marker}] Step {s.index}: {s.description}")
            if s.result_summary:
                lines.append(f"       -> {s.result_summary}")
        return "\n".join(lines)


class AgentPlanner:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @staticmethod
    def should_plan(user_input: str) -> bool:
        """Only plan for genuinely complex multi-step tasks.

        Rejects simple commands, single-tool queries, and short requests to
        avoid the "open browser → 5-step plan" anti-pattern.
        """
        if _SIMPLE_PATTERNS.search(user_input):
            return False
        if len(user_input) < 80:
            return False
        if len(user_input) > 500 and _COMPLEXITY_KEYWORDS.search(user_input):
            return True
        if _COMPLEXITY_KEYWORDS.search(user_input):
            return True
        return False

    async def plan(
        self,
        user_input: str,
        available_tools: list[str] | None = None,
    ) -> StructuredPlan | None:
        tool_hint = ""
        if available_tools:
            tool_hint = f"\nAvailable tools: {', '.join(available_tools)}"

        messages = [
            {"role": "system", "content": _PLAN_SYSTEM + tool_hint},
            {"role": "user", "content": user_input},
        ]
        resp = await self._llm.chat(messages, tools=None, max_tokens=512)
        if resp.finish_reason == "error":
            return None
        return self._parse(resp.content, user_input)

    @staticmethod
    def _parse(raw: str, user_input: str) -> StructuredPlan | None:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and "steps" in data:
            data = data["steps"]
        if not isinstance(data, list) or len(data) == 0:
            return None
        steps: list[PlanStep] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            steps.append(PlanStep(
                index=i,
                description=item.get("description", f"Step {i}"),
                expected_tool=item.get("expected_tool"),
                depends_on=item.get("depends_on", []),
            ))
        if not steps:
            return None
        return StructuredPlan(steps=steps, original_input=user_input)
