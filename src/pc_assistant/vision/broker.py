"""Request-scoped image perception through a dedicated vision model."""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from pc_assistant.artifacts import ArtifactStore
from pc_assistant.llm_provider import LLMProvider


VISION_SYSTEM_PROMPT = """You are a visual perception component, not a problem-solving assistant.
Answer the supplied visual question using only what is visibly present in the image.
Do not diagnose causes. Do not propose fixes, recommendations, actions, commands, or next steps.
For an error screenshot, report only the visible error and surrounding visual evidence; the main model decides how to solve it.
Do not reveal chain-of-thought. Distinguish observation from uncertainty. Never infer hidden state.
Return concise natural-language observations. Do not wrap the answer in JSON or Markdown fences.
"""

_SOLUTION_FOCUS = re.compile(
    r"(?:\bwhy\b|how\s+to|how\s+do\s+i|what\s+caused|solve|fix|repair|diagnos|"
    r"root\s+cause|recommend|为什么|怎么解决|如何解决|怎么修复|如何修复|"
    r"解决方案|给.*建议|原因是什么|分析原因)",
    re.IGNORECASE,
)


class VisionBroker:
    def __init__(
        self,
        provider: LLMProvider,
        artifact_store: ArtifactStore,
        *,
        model_name: str = "",
        max_tokens: int = 1024,
    ) -> None:
        if not provider.supports_vision:
            raise ValueError("Dedicated vision provider must support image input")
        self._provider = provider
        self._store = artifact_store
        self._model_name = model_name or "default"
        self._max_tokens = max(64, max_tokens)
        self._cache: dict[str, dict[str, Any]] = {}

    async def inspect(
        self,
        session_id: str,
        image_id: str,
        *,
        question: str,
    ) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("question is required")
        if _SOLUTION_FOCUS.search(question):
            raise ValueError(
                "question must request visible observations only; diagnosis and solutions belong to the main model"
            )

        metadata = self._store.metadata(session_id, image_id)
        cache_key = hashlib.sha256(
            f"{metadata['content_sha256']}\0{self._model_name}\0{question}".encode()
        ).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": f"Visual question from the main model: {question}",
        }, self._store.hydrate_artifact(session_id, image_id)]

        response = await self._provider.chat(
            [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
            max_tokens=self._max_tokens,
        )
        if response.finish_reason == "error":
            raise RuntimeError(response.content)
        observation = (response.content or "").strip()
        if not observation:
            raise RuntimeError("Vision model returned an empty observation")
        result = {
            "observation_id": uuid.uuid4().hex,
            "image_id": image_id,
            "question": question,
            "observation": observation,
            "model": self._model_name,
            "cached": False,
        }
        self._cache[cache_key] = result
        return result
