from __future__ import annotations

from pc_assistant.llm_provider import LLMProvider


JUDGE_PROMPT = """You are an impartial evaluator. Rate the following answer based on the given rubric.

Rubric: {rubric}

Question: {question}

Answer: {answer}

Rate the answer on a scale of 0-10 where 0 is completely wrong/irrelevant and 10 is perfectly correct. Respond with ONLY a single number. Do not include any text.

Score (0-10):"""


class LLMJudge:
    def __init__(self, provider: LLMProvider):
        self._provider = provider

    async def judge(self, question: str, answer: str, rubric: str) -> float:
        if not answer:
            return 0.0

        prompt = JUDGE_PROMPT.format(question=question, answer=answer, rubric=rubric)

        try:
            resp = await self._provider.chat([{"role": "user", "content": prompt}], temperature=0.0)
            text = resp.content.strip()

            import re
            match = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
            if match:
                raw_score = float(match.group(1))
                normalized = max(0.0, min(1.0, raw_score / 10.0))
                return round(normalized, 2)
        except Exception:
            pass

        return 0.0