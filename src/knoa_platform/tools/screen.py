"""Visual grounding and verification through screenshots and vision."""
from __future__ import annotations

import asyncio
import re
from io import BytesIO
from typing import Any

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import current_tool_step_context
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.context.scope import current_memory_scope
from knoa_platform.tools.artifacts import ArtifactPaths
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from knoa_platform.vision import VisionBroker
from knoa_platform.vision.grid import crop_region, overlay_grid


class ScreenTool(ToolBase):
    name = "screen"
    description = (
        "Capture the desktop for visual grounding, verify GUI actions, or answer "
        "targeted questions about what is visible."
    )
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.DESKTOP_OBSERVE})
    risk = ToolRisk.LOW

    def __init__(
        self,
        broker: VisionBroker,
        store: ArtifactStore,
        artifact_dir: str,
        *,
        grid_enabled: bool = False,
        grid_size: int = 4,
    ) -> None:
        self._broker = broker
        self._store = store
        self._paths = ArtifactPaths(artifact_dir)
        self._grid_enabled = grid_enabled
        self._grid_size = grid_size

    async def execute(self, **kwargs: Any) -> Any:
        raise RuntimeError("screen requires an invocation scope")

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        action = kwargs.get("action", "look")
        handlers = {
            "look": self._look,
            "verify": self._verify,
            "understand": self._understand,
        }
        handler = handlers.get(action)
        if handler is None:
            return {
                "error": f"Unknown action: {action}. Use: look, verify, understand."
            }
        return await handler(scope, kwargs)

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["look", "verify", "understand"],
                        "description": "Visual observation action.",
                    },
                    "region": {
                        "type": "string",
                        "description": "Optional grid cell such as A1 for understand.",
                    },
                    "question": {
                        "type": "string",
                        "description": "Question for understand.",
                    },
                    "action_description": {
                        "type": "string",
                        "description": "Description of the GUI action to verify.",
                    },
                    "grid": {
                        "type": "boolean",
                        "description": "Override default grid overlay setting.",
                    },
                },
                "required": ["action"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["look", "verify", "understand"]},
                    "region": {"type": "string"},
                    "question": {"type": "string"},
                    "action_description": {"type": "string"},
                },
                "required": ["action"],
            },
        }

    async def _look(self, scope: RuntimeScope, kwargs: dict[str, Any]) -> dict[str, Any]:
        use_grid = bool(kwargs["grid"]) if "grid" in kwargs else self._grid_enabled
        try:
            metadata, _image_bytes = await self._capture_sync(use_grid=use_grid)
        except RuntimeError as exc:
            return {"error": str(exc)}

        question = (
            "Describe what is visible on this desktop screenshot. "
            "Mention prominent windows, controls, text, and any grid cell labels if present."
        )
        inspection = await self._inspect(scope, metadata["artifact_id"], question)
        if inspection.get("error"):
            return {**metadata, **inspection}
        return {
            "success": True,
            **metadata,
            "observation": inspection.get("observation", ""),
            "model": inspection.get("model", ""),
        }

    async def _understand(self, scope: RuntimeScope, kwargs: dict[str, Any]) -> dict[str, Any]:
        question = str(kwargs.get("question") or "").strip()
        if not question:
            return {"error": "question is required for understand action"}
        use_grid = bool(kwargs["grid"]) if "grid" in kwargs else self._grid_enabled
        region = str(kwargs.get("region") or "").strip().upper()
        try:
            metadata, image_bytes = await self._capture_sync(use_grid=use_grid)
            if region:
                cropped = crop_region(image_bytes, region, grid_size=self._grid_size)
                save_path = self._paths.allocate(prefix="screen-region", suffix=".jpg")
                save_path.write_bytes(cropped)
                artifact = self._store.register_generated(
                    current_memory_scope().session_id,
                    save_path,
                    media_type="image/jpeg",
                    retention="temporary",
                )
                metadata = {
                    "artifact_id": str(artifact["artifact_id"]),
                    "artifact": artifact,
                    "region": region,
                    "grid_size": self._grid_size,
                }
        except (RuntimeError, ValueError) as exc:
            return {"error": str(exc)}

        inspection = await self._inspect(scope, metadata["artifact_id"], question)
        if inspection.get("error"):
            return {**metadata, **inspection}
        return {
            "success": True,
            **metadata,
            "question": question,
            "observation": inspection.get("observation", ""),
            "model": inspection.get("model", ""),
        }

    async def _verify(self, scope: RuntimeScope, kwargs: dict[str, Any]) -> dict[str, Any]:
        description = str(kwargs.get("action_description") or "").strip()
        if not description:
            return {"error": "action_description is required for verify action"}
        try:
            metadata, _image_bytes = await self._capture_sync(use_grid=False)
        except RuntimeError as exc:
            return {"error": str(exc)}

        question = (
            f"A GUI action was just performed: {description}. "
            "Did this action appear to succeed based on what is visible now? "
            "Answer with YES or NO on the first line, then briefly explain what you see."
        )
        inspection = await self._inspect(scope, metadata["artifact_id"], question)
        if inspection.get("error"):
            return {**metadata, **inspection}
        observation = str(inspection.get("observation") or "")
        success = self._parse_yes_no(observation)
        return {
            "success": success is True,
            "verified": success is True,
            "uncertain": success is None,
            "action_description": description,
            "observation": observation,
            "model": inspection.get("model", ""),
            **metadata,
        }

    async def _capture_sync(self, *, use_grid: bool) -> tuple[dict[str, Any], bytes]:
        return await asyncio.to_thread(self._capture_blocking, use_grid)

    def _capture_blocking(self, use_grid: bool) -> tuple[dict[str, Any], bytes]:
        save_path = self._paths.allocate(prefix="screen", suffix=".jpg")
        try:
            from PIL import Image
        except ImportError as exc:
            save_path.unlink(missing_ok=True)
            raise RuntimeError("Screen capture unavailable (Pillow not installed)") from exc

        from knoa_platform.desktop_companion import (
            desktop_companion_required,
            invoke_desktop_companion,
        )

        if desktop_companion_required():
            result = invoke_desktop_companion("screenshot", {})
            try:
                import base64

                image_bytes = base64.b64decode(
                    str(result["content_base64"]), validate=True
                )
                image = Image.open(BytesIO(image_bytes))
                image.load()
            except (KeyError, ValueError, OSError) as exc:
                save_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "Desktop Companion returned an invalid screenshot"
                ) from exc
        else:
            try:
                import mss
            except ImportError as exc:
                save_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "Screen capture unavailable (mss not installed)"
                ) from exc
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[0])
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                max_width = 3200
                if image.width > max_width:
                    image = image.resize(
                        (
                            max_width,
                            max(1, round(image.height * max_width / image.width)),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=85, optimize=True)
                image_bytes = buffer.getvalue()

        if use_grid:
            image_bytes = overlay_grid(image_bytes, grid_size=self._grid_size)

        save_path.write_bytes(image_bytes)
        artifact = self._store.register_generated(
            current_memory_scope().session_id,
            save_path,
            media_type="image/jpeg",
            retention="temporary",
        )
        metadata = {
            "artifact_id": str(artifact["artifact_id"]),
            "artifact": artifact,
            "screen_size": {"width": image.width, "height": image.height},
            "grid_enabled": use_grid,
            "grid_size": self._grid_size if use_grid else None,
        }
        return metadata, image_bytes

    async def _inspect(
        self,
        scope: RuntimeScope,
        artifact_id: str,
        question: str,
    ) -> dict[str, Any]:
        if not self._broker.available:
            return {"error": "Dedicated vision model is not configured"}
        context = current_tool_step_context()
        try:
            return await self._broker.inspect(
                scope.session_handle,
                artifact_id,
                question=question,
                cancellation=context.cancellation if context is not None else None,
            )
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            return {"error": str(exc), "artifact_id": artifact_id}

    @staticmethod
    def _parse_yes_no(observation: str) -> bool | None:
        first_line = observation.strip().splitlines()[0] if observation.strip() else ""
        normalized = first_line.strip().casefold()
        if re.search(r"\byes\b", normalized):
            return True
        if re.search(r"\bno\b", normalized):
            return False
        if "成功" in first_line or "是的" in first_line:
            return True
        if "失败" in first_line or "没有" in first_line:
            return False
        return None
