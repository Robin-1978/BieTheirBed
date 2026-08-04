"""Public artifact contracts shared by the Agent and client adapters."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ArtifactRef(BaseModel):
    """Bounded public reference; never contains a server path or file bytes."""

    artifact_id: str
    kind: Literal["image", "file"]
    name: str
    media_type: str
    size: int
    visibility: Literal["user"] = "user"
    temporary: bool = True
