"""Core artifact registry and public references."""

from knoa_platform.artifacts.models import ArtifactRef
from knoa_platform.artifacts.store import ArtifactStore

__all__ = ["ArtifactRef", "ArtifactStore"]
