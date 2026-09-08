"""Fluent builder for constructing universal Action Cards."""

from __future__ import annotations

import time
import uuid
from typing import Any

from knoa_platform.action_card.models import (
    ActionCard,
    ActionCardBlock,
    ActionCardButton,
    ActionCardInput,
    ActionCardLevel,
    ActionCardSource,
    ActionCardStatus,
    KeyValueItem,
)


class ActionCardBuilder:
    """Fluent builder to easily construct compliant ActionCard instances."""

    def __init__(
        self,
        title: str,
        *,
        card_id: str | None = None,
        level: ActionCardLevel | str = ActionCardLevel.INFO,
    ) -> None:
        self._title = title
        self._card_id = card_id or f"card_{uuid.uuid4().hex[:16]}"
        self._subtitle: str | None = None
        self._level = (
            ActionCardLevel(level) if isinstance(level, str) else level
        )
        self._status = ActionCardStatus.PENDING
        self._source = ActionCardSource(plugin_name="system")
        self._blocks: list[ActionCardBlock] = []
        self._inputs: list[ActionCardInput] = []
        self._actions: list[ActionCardButton] = []
        self._metadata: dict[str, Any] = {}

    def set_subtitle(self, subtitle: str) -> ActionCardBuilder:
        self._subtitle = subtitle
        return self

    def set_status(self, status: ActionCardStatus | str) -> ActionCardBuilder:
        self._status = (
            ActionCardStatus(status) if isinstance(status, str) else status
        )
        return self

    def set_source(
        self,
        plugin_name: str,
        *,
        agent_name: str | None = None,
        session_id: str | None = None,
        expires_in_seconds: float | None = None,
    ) -> ActionCardBuilder:
        now = time.time()
        expires_at = (now + expires_in_seconds) if expires_in_seconds else None
        self._source = ActionCardSource(
            plugin_name=plugin_name,
            agent_name=agent_name,
            session_id=session_id,
            created_at=now,
            expires_at=expires_at,
        )
        return self

    def add_markdown(self, content: str) -> ActionCardBuilder:
        self._blocks.append(
            ActionCardBlock(type="markdown", payload={"content": content})
        )
        return self

    def add_key_value(
        self, items: list[dict[str, str] | KeyValueItem]
    ) -> ActionCardBuilder:
        rendered: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, KeyValueItem):
                rendered.append(item.to_dict())
            elif isinstance(item, dict):
                rendered.append(
                    {
                        "key": str(item.get("key", "")),
                        "value": str(item.get("value", "")),
                        "style": str(item.get("style", "default")),
                    }
                )
        self._blocks.append(
            ActionCardBlock(type="key_value", payload={"items": rendered})
        )
        return self

    def add_code_diff(
        self,
        filename: str,
        *,
        language: str = "text",
        old_code: str | None = None,
        new_code: str | None = None,
        unified_diff: str = "",
    ) -> ActionCardBuilder:
        self._blocks.append(
            ActionCardBlock(
                type="code_diff",
                payload={
                    "filename": filename,
                    "language": language,
                    "old_code": old_code,
                    "new_code": new_code,
                    "unified_diff": unified_diff,
                },
            )
        )
        return self

    def add_callout(
        self, text: str, *, level: str = "info"
    ) -> ActionCardBuilder:
        self._blocks.append(
            ActionCardBlock(
                type="callout", payload={"text": text, "level": level}
            )
        )
        return self

    def add_artifact_link(
        self,
        name: str,
        url: str,
        *,
        size_bytes: int | None = None,
        mime_type: str | None = None,
    ) -> ActionCardBuilder:
        self._blocks.append(
            ActionCardBlock(
                type="artifact_link",
                payload={
                    "name": name,
                    "url": url,
                    "size_bytes": size_bytes,
                    "mime_type": mime_type,
                },
            )
        )
        return self

    def add_input(
        self,
        id: str,
        label: str,
        *,
        input_type: str = "text",
        placeholder: str = "",
        default_value: Any = None,
        options: list[dict[str, str]] | None = None,
        required: bool = False,
    ) -> ActionCardBuilder:
        self._inputs.append(
            ActionCardInput(
                id=id,
                label=label,
                input_type=input_type,
                placeholder=placeholder,
                default_value=default_value,
                options=options or [],
                required=required,
            )
        )
        return self

    def add_action_button(
        self,
        id: str,
        label: str,
        *,
        style: str = "primary",
        action_type: str = "invoke_tool",
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        confirm_dialog: dict[str, str] | None = None,
        include_form_inputs: bool = True,
    ) -> ActionCardBuilder:
        self._actions.append(
            ActionCardButton(
                id=id,
                label=label,
                style=style,
                action_type=action_type,
                tool_name=tool_name,
                arguments=arguments or {},
                confirm_dialog=confirm_dialog,
                include_form_inputs=include_form_inputs,
            )
        )
        return self

    def set_metadata(self, key: str, value: Any) -> ActionCardBuilder:
        self._metadata[key] = value
        return self

    def build(self) -> ActionCard:
        return ActionCard(
            card_id=self._card_id,
            title=self._title,
            subtitle=self._subtitle,
            level=self._level,
            status=self._status,
            source=self._source,
            blocks=list(self._blocks),
            inputs=list(self._inputs),
            actions=list(self._actions),
            metadata=dict(self._metadata),
        )
