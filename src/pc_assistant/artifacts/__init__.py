"""Core artifact registry and public references."""

from pc_assistant.artifacts.models import ArtifactRef
from pc_assistant.artifacts.store import ArtifactStore

__all__ = ["ArtifactRef", "ArtifactStore"]
