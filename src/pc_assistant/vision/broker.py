"""Request-scoped image perception through a dedicated vision model."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from pc_assistant.attachments import AttachmentStore
from pc_assistant.llm_provider import LLMProvider


VISION_SYSTEM_PROMPT = """You are a visual perception component, not a problem-solving assistant.
Observe only what is visibly present in the supplied image(s) and return one JSON object.
You may describe visible content, transcribe visible text, locate visible objects, or compare visible differences.
Do not diagnose causes. Do not propose fixes, recommendations, actions, commands, or next steps.
For an error screenshot, report only the visible error and surrounding visual evidence; the main model decides how to solve it.
Do not reveal chain-of-thought. Distinguish observation from uncertainty. Never infer hidden state.

JSON fields: description (string), visible_text (array of strings), entities (array of strings),
regions (array of objects with label, x, y, width, height when available), confidence (0..1), uncertainty (string).
"""

_ACTIONS = frozenset({"describe", "ocr", "locate", "compare"})
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
        attachment_store: AttachmentStore,
        *,
        model_name: str = "",
        max_tokens: int = 1024,
    ) -> None:
        if not provider.supports_vision:
            raise ValueError("Dedicated vision provider must support image input")
        self._provider = provider
        self._store = attachment_store
        self._model_name = model_name or "default"
        self._max_tokens = max(64, max_tokens)
        self._cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = content.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Vision model did not return valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Vision model response must be a JSON object")
        return parsed

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, (str, int, float))]

    @staticmethod
    def _region_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    async def inspect(
        self,
        session_id: str,
        image_id: str,
        *,
        action: str = "describe",
        focus: str = "",
        region: dict[str, int | float] | None = None,
        compare_image_id: str = "",
    ) -> dict[str, Any]:
        if action not in _ACTIONS:
            raise ValueError(f"Unsupported image inspection action: {action}")
        if action == "compare" and not compare_image_id:
            raise ValueError("compare_image_id is required for compare")
        if focus and _SOLUTION_FOCUS.search(focus):
            raise ValueError(
                "focus must request visible observations only; diagnosis and solutions belong to the main model"
            )

        metadata = self._store.metadata(session_id, image_id)
        compare_metadata = (
            self._store.metadata(session_id, compare_image_id)
            if compare_image_id
            else None
        )
        cache_payload = {
            "image": metadata["content_sha256"],
            "compare": compare_metadata["content_sha256"] if compare_metadata else "",
            "model": self._model_name,
            "action": action,
            "focus": focus.strip(),
            "region": region or {},
        }
        cache_key = hashlib.sha256(
            json.dumps(cache_payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": (
                f"Operation: {action}.\n"
                f"Observation focus: {focus.strip() or 'Describe the visible image content objectively.'}\n"
                f"Region of interest: {json.dumps(region, ensure_ascii=False) if region else 'entire image'}.\n"
                "Return visual observations only. Do not answer how to solve, fix, or act on anything shown."
            ),
        }, self._store.hydrate_attachment(session_id, image_id)]
        if compare_image_id:
            content.extend([
                {"type": "text", "text": "Second image for visible comparison:"},
                self._store.hydrate_attachment(session_id, compare_image_id),
            ])

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
        parsed = self._parse_json(response.content)
        try:
            confidence = float(parsed.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        result = {
            "observation_id": uuid.uuid4().hex,
            "image_id": image_id,
            "compare_image_id": compare_image_id or None,
            "action": action,
            "focus": focus.strip(),
            "description": str(parsed.get("description", "")),
            "visible_text": self._string_list(parsed.get("visible_text")),
            "entities": self._string_list(parsed.get("entities")),
            "regions": self._region_list(parsed.get("regions")),
            "confidence": min(1.0, max(0.0, confidence)),
            "uncertainty": str(parsed.get("uncertainty", "")),
            "model": self._model_name,
            "cached": False,
        }
        self._cache[cache_key] = result
        return result
