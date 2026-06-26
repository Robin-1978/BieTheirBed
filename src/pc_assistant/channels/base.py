from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ChannelBase(ABC):
    name: str = ""

    @abstractmethod
    async def start(self, agent: Any) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...

    @abstractmethod
    def send_message(self, recipient_id: str, text: str) -> bool:
        ...

    @abstractmethod
    def send_card(self, recipient_id: str, card: dict) -> bool:
        ...

    def __repr__(self) -> str:
        return f"<Channel {self.name}>"
