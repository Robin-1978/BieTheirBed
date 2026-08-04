"""Paths and metadata for temporary tool-generated artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pc_assistant.runtime import RuntimePaths


class ArtifactPaths:
    """Allocate collision-resistant paths below the unified attachment root."""

    def __init__(self, root: str | Path | None = None) -> None:
        default_root = RuntimePaths.from_root().attachments / "screenshots"
        self.root = Path(root or default_root).expanduser().resolve()

    def allocate(
        self,
        *,
        prefix: str,
        suffix: str,
        requested: str | Path | None = None,
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if requested:
            raw = Path(requested).expanduser()
            candidate = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
            try:
                candidate.relative_to(self.root)
            except ValueError as exc:
                raise ValueError(
                    f"Screenshot path must stay below the temporary artifact directory: {self.root}"
                ) from exc
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            candidate = self.root / f"{prefix}-{timestamp}-{uuid4().hex[:8]}{suffix}"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate


def image_artifact(path: Path, media_type: str) -> dict[str, str | bool]:
    """Return channel-safe metadata; binary image bytes are never embedded."""
    return {
        "kind": "image",
        "path": str(path),
        "media_type": media_type,
        "temporary": True,
    }
