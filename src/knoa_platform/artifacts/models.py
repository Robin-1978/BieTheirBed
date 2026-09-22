"""Public artifact metadata shared across Core and client boundaries."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


ArtifactId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
ArtifactName = Annotated[str, StringConstraints(min_length=1, max_length=160)]
MediaType = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ArtifactRef(BaseModel):
    """Bounded public reference; never contains a server path or file bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_id: ArtifactId
    kind: Literal["image", "file"]
    name: ArtifactName
    media_type: MediaType
    size: int = Field(gt=0)
    direction: Literal["inbound", "outbound"] = "outbound"
    ownership: Literal["borrowed", "managed", "generated"] = "generated"
    retention: Literal["temporary", "session", "persistent"] = "temporary"
    status: Literal["available", "delivered"] = "available"
    visibility: Literal["agent", "user"] = "user"
    # Owning conversation session. Empty for single-session responses that
    # already carry the session in the request path/query.
    session_handle: str = Field(default="", max_length=256)
    # Unix timestamp of registry insertion; 0 for rows predating tracking.
    created_at: float = Field(default=0.0, ge=0.0)
