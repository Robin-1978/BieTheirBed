"""Paths and metadata for temporary tool-generated artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from knoa_platform.runtime import RuntimePaths


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
    ) -> Path:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        candidate = self.root / f"{prefix}-{timestamp}-{uuid4().hex[:8]}{suffix}"
        return candidate
