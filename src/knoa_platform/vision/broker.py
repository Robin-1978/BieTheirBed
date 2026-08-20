"""Request-scoped visual perception through one dedicated model."""
from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from typing import Any

from knoa_platform.agent_runtime.model_step import ModelProviderPort, ProviderCallRequest
from knoa_platform.artifacts import ArtifactStore


VISION_SYSTEM_PROMPT = """You are a visual perception component, not a problem-solving assistant.
Answer the supplied visual question using only what is visibly present in the image.
Do not diagnose causes or propose fixes, recommendations, commands, or next steps.
Report visible text and evidence concisely. Distinguish observation from uncertainty.
Return plain natural-language observations without JSON or Markdown fences.
"""

_SOLUTION_FOCUS = re.compile(
    r"(?:\bwhy\b|how\s+to|solve|fix|repair|diagnos|root\s+cause|recommend|"
    r"为什么|怎么解决|如何解决|怎么修复|如何修复|解决方案|分析原因)",
    re.IGNORECASE,
)


class VisionBroker:
    def __init__(
        self,
        provider: ModelProviderPort | None,
        artifact_store: ArtifactStore,
        *,
        model_alias: str = "",
        max_output_tokens: int = 1024,
    ) -> None:
        self._provider = provider
        self._store = artifact_store
        self._model_alias = model_alias
        self._max_output_tokens = max(64, max_output_tokens)
        self._cache: dict[str, dict[str, Any]] = {}

    @property
    def available(self) -> bool:
        return self._provider is not None and bool(self._model_alias)

    def configure(
        self,
        provider: ModelProviderPort | None,
        *,
        model_alias: str = "",
    ) -> None:
        if provider is None:
            self._provider = None
            self._model_alias = ""
            return
        if not model_alias.strip():
            raise ValueError("Vision model alias is required")
        self._provider = provider
        self._model_alias = model_alias.strip()

    async def inspect(
        self,
        session_id: str,
        artifact_id: str,
        *,
        question: str,
        cancellation: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("question is required")
        if _SOLUTION_FOCUS.search(question):
            raise ValueError(
                "question must request visible observations only; diagnosis belongs to the main model"
            )
        provider = self._provider
        model_alias = self._model_alias
        if provider is None or not model_alias:
            raise RuntimeError("Dedicated vision model is not configured")
        metadata = self._store.metadata(session_id, artifact_id)
        if not str(metadata["media_type"]).startswith("image/"):
            raise ValueError("Artifact is not an image")
        cache_key = hashlib.sha256(
            f"{metadata['content_sha256']}\0{model_alias}\0{question}".encode()
        ).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        image = self._store.hydrate_ref(session_id, {"artifact_id": artifact_id})
        request = ProviderCallRequest(
            call_id=uuid.uuid4().hex,
            purpose="react",
            messages=(
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Visual question: {question}"},
                        image,
                    ],
                },
            ),
            temperature=0.0,
            max_output_tokens=self._max_output_tokens,
        )
        stopped = cancellation or asyncio.Event()
        parts: list[str] = []
        terminal = None
        async for chunk in provider.stream(request, stopped):
            if stopped.is_set():
                raise RuntimeError("Vision request was cancelled")
            if chunk.content_delta:
                parts.append(chunk.content_delta)
            if chunk.terminal:
                terminal = chunk
        if terminal is None or terminal.finish_reason == "error":
            raise RuntimeError(
                f"Vision model failed: {getattr(terminal, 'error_code', '') or 'provider_failed'}"
            )
        observation = "".join(parts).strip()
        if not observation:
            raise RuntimeError("Vision model returned an empty observation")
        result = {
            "observation_id": uuid.uuid4().hex,
            "artifact_id": artifact_id,
            "question": question,
            "observation": observation,
            "model": model_alias,
            "cached": False,
        }
        self._cache[cache_key] = result
        return result
