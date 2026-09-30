from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from typing import Any

from knoa_agent_contracts import InteractionRequested
from knoa_platform.agent_runtime.tool_step import current_tool_step_context
from knoa_platform.location.reverse import enrich_device_location
from knoa_platform.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolRisk,
)


class DeviceLocationTool(ToolBase):
    """Request a just-in-time location snapshot from the active mobile App."""

    name = "device_location"
    description = (
        "Request the user's current phone location only when it is necessary for "
        "the current question. Do not call this when the user supplied a place, "
        "when a remembered address is sufficient, or for non-geographic meanings "
        "of words such as position, locate, or location. "
        "Pass the returned `location` string verbatim as the `location` argument "
        "of weather/nearby/navigation tools; do not re-infer a city name from "
        "coordinates."
    )
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.HOST_READ})
    risk = ToolRisk.LOW

    def __init__(
        self,
        *,
        reverse_geocode: Callable[[str], str] = enrich_device_location,
        response_timeout_seconds: float = 12.0,
    ) -> None:
        self._reverse_geocode = reverse_geocode
        self._response_timeout_seconds = response_timeout_seconds

    async def execute(self, **kwargs: Any) -> Any:
        return await self.execute_scoped(None, **kwargs)

    async def execute_scoped(self, scope: Any, **kwargs: Any) -> Any:
        del scope
        context = current_tool_step_context()
        interaction = None if context is None else context.interaction
        if (
            context is None
            or interaction is None
            or getattr(interaction, "owner_kind", "") != "conversation_turn"
        ):
            return {
                "available": False,
                "reason": "device_location_unavailable",
            }

        purpose = str(kwargs.get("purpose") or "")
        precision_raw = str(kwargs.get("precision") or "block")
        address_required = bool(kwargs.get("address_required", True))
        precision_rank = {"city": 0, "block": 1, "precise": 2}
        if precision_raw not in precision_rank:
            return {"available": False, "reason": "device_location_invalid"}
        precision = precision_raw
        run_suffix = secrets.token_hex(8)
        prefix = f"device-location-{context.run_id}-"
        max_run_chars = max(0, 128 - len(prefix) - len(run_suffix))
        interaction_id = f"{prefix}{context.run_id[:max_run_chars]}{run_suffix}"
        handle = await interaction.begin(
            context.scope,
            context.run_id,
            InteractionRequested(
                runtime_session_ref=context.scope.session_handle,
                runtime_turn_ref=context.run_id,
                occurred_at=time.time(),
                interaction_id=interaction_id,
                interaction_epoch=1,
                kind="device_location",
                display={
                    "title": "Current phone location",
                    "purpose": purpose,
                    "precision": precision,
                    "address_required": address_required,
                    "automatic": True,
                },
                resolution_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["available", "disabled", "unavailable"],
                        },
                        "location": {"type": "string", "maxLength": 500},
                        "precision": {
                            "type": "string",
                            "enum": ["city", "block", "precise"],
                        },
                    },
                    "required": ["status", "location", "precision"],
                    "additionalProperties": False,
                },
                expires_at=time.time() + self._response_timeout_seconds + 3.0,
            ),
        )
        try:
            response = await asyncio.wait_for(
                handle.wait(),
                timeout=self._response_timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            await handle.expire()
            return {"available": False, "reason": "device_location_timeout"}
        except asyncio.CancelledError:
            await handle.expire()
            raise
        if not isinstance(response, dict):
            return {"available": False, "reason": "device_location_invalid"}
        status = str(response.get("status") or "unavailable")
        if status != "available":
            return {"available": False, "reason": f"device_location_{status}"}
        location = str(response.get("location") or "").strip()[:500]
        resolved_precision = str(response.get("precision") or "")
        if not location or resolved_precision not in precision_rank:
            return {"available": False, "reason": "device_location_invalid"}
        if precision_rank[resolved_precision] > precision_rank[precision]:
            return {
                "available": False,
                "reason": "device_location_precision_exceeded",
            }
        if address_required:
            location = await asyncio.to_thread(self._reverse_geocode, location)
        return {
            "available": True,
            "location": location,
            "precision": resolved_precision,
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "purpose": {
                        "type": "string",
                        "enum": [
                            "weather",
                            "nearby",
                            "navigation",
                            "local_conditions",
                            "where_am_i",
                        ],
                        "description": "Why the current device location is required.",
                    },
                    "precision": {
                        "type": "string",
                        "enum": ["city", "block", "precise"],
                        "description": (
                            "Minimum precision needed: city for weather, block for nearby "
                            "places, precise only for navigation or an explicit exact-location request."
                        ),
                    },
                    "address_required": {
                        "type": "boolean",
                        "description": (
                            "Whether a readable place name is required. Use false when coordinates "
                            "are sufficient so reverse geocoding can be skipped."
                        ),
                    },
                },
                "required": ["purpose", "precision", "address_required"],
                "additionalProperties": False,
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return self.definition()
