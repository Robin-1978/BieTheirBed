from __future__ import annotations

import asyncio
import re

from knoa_platform.agent_runtime.model_step import (
    ModelProviderPort,
    ProviderCallRequest,
)


JUDGE_PROMPT = """You are an impartial evaluator. Rate the following answer based on the given rubric.

Rubric: {rubric}

Question: {question}

Answer: {answer}

Rate the answer on a scale of 0-10 where 0 is completely wrong/irrelevant and 10 is perfectly correct. Respond with ONLY a single number. Do not include any text.

Score (0-10):"""


class LLMJudge:
    def __init__(self, provider: ModelProviderPort):
        self._provider = provider

    async def judge(self, question: str, answer: str, rubric: str) -> float:
        if not answer:
            return 0.0

        prompt = JUDGE_PROMPT.format(question=question, answer=answer, rubric=rubric)

        try:
            chunks: list[str] = []
            terminal_ok = False
            request = ProviderCallRequest(
                call_id="benchmark-judge",
                purpose="reflection",
                messages=({"role": "user", "content": prompt},),
                temperature=0.0,
                max_output_tokens=32,
            )
            async for chunk in self._provider.stream(request, asyncio.Event()):
                if chunk.content_delta:
                    chunks.append(chunk.content_delta)
                if chunk.terminal:
                    terminal_ok = chunk.finish_reason == "stop"
            if not terminal_ok:
                return 0.0
            text = "".join(chunks).strip()
            match = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
            if match:
                raw_score = float(match.group(1))
                normalized = max(0.0, min(1.0, raw_score / 10.0))
                return round(normalized, 2)
        except Exception:
            pass

        return 0.0
