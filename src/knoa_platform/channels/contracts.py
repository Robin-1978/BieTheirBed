"""Channel-neutral ingress/egress data contract.

Adapters may keep provider-specific envelopes at their edge, but once a
message enters Knoa it should have the same identity, text, attachments and
delivery semantics.  The contract is intentionally small so App, Feishu and
DingTalk can evolve independently without duplicating Core task logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class ChannelAttachment:
    kind: Literal["image", "file", "audio"]
    provider_key: str
    name: str = ""
    media_type: str = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    channel: str
    principal_id: str
    message_id: str
    text: str = ""
    attachments: tuple[ChannelAttachment, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChannelDelivery:
    channel: str
    principal_id: str
    provider_message_id: str = ""
    delivered: bool = False
    retryable: bool = True
    error: str = ""


__all__ = ["ChannelAttachment", "ChannelDelivery", "ChannelMessage"]
