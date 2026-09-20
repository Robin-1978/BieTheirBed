"""Settings and client for the cloud_files MCP reference server."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .oss_files import download_oss_file, list_oss_objects
    from .tempo_files import download_tempo_file, list_tempo_records
except ImportError:  # installed as a self-contained MCP package
    from oss_files import download_oss_file, list_oss_objects
    from tempo_files import download_tempo_file, list_tempo_records

_CASE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class CloudFilesSettings:
    download_root: Path
    max_download_bytes: int = 500 * 1024 * 1024

    @classmethod
    def from_env(cls) -> CloudFilesSettings:
        root = Path(
            os.environ.get("CLOUD_FILES_DOWNLOAD_ROOT", "/tmp/cloud_files")
        ).expanduser()
        max_bytes = int(os.environ.get("CLOUD_FILES_MAX_BYTES", str(500 * 1024 * 1024)))
        return cls(download_root=root, max_download_bytes=max_bytes)


class CloudFilesClient:
    """List and download cloud evidence files grouped by caller case ID."""

    def __init__(self, settings: CloudFilesSettings) -> None:
        self.settings = settings

    def _case_dir(self, case_id: str) -> Path:
        label = _CASE_RE.sub("_", (case_id or "").strip()).strip("._")[:64]
        if not label:
            label = "shared-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.settings.download_root / label
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.chmod(0o700)
        return target

    async def list_files(
        self,
        source: str,
        *,
        prefix: str = "",
        share_id: str = "",
        sn: str = "",
        status: str | None = None,
        file_type: str | None = None,
        filename: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        normalized = source.strip().lower()
        bounded_limit = max(1, min(int(limit), 100))
        if normalized == "oss":
            return {
                "source": "oss",
                "objects": await list_oss_objects(prefix, limit=bounded_limit),
            }
        if normalized == "tempo":
            if not share_id.strip() or not sn.strip():
                raise ValueError("Tempo listing requires share_id and sn")
            return {
                "source": "tempo",
                **await list_tempo_records(
                    share_id.strip(),
                    sn.strip(),
                    status=status,
                    file_type=file_type,
                    filename=filename,
                    limit=bounded_limit,
                ),
            }
        raise ValueError("source must be 'oss' or 'tempo'")

    async def download_file(
        self,
        source: str,
        url: str,
        *,
        filename: str = "",
        case_id: str = "",
    ) -> dict[str, Any]:
        normalized = source.strip().lower()
        destination = self._case_dir(case_id)
        if normalized == "oss":
            if not url.strip():
                raise ValueError("OSS download requires url")
            return {
                "source": "oss",
                "case_id": destination.name,
                **await download_oss_file(
                    url.strip(),
                    destination,
                    max_bytes=self.settings.max_download_bytes,
                ),
            }
        if normalized == "tempo":
            if not url.strip() or not filename.strip():
                raise ValueError("Tempo download requires url and filename")
            return {
                "source": "tempo",
                "case_id": destination.name,
                **await download_tempo_file(
                    url.strip(),
                    filename.strip(),
                    destination,
                    max_bytes=self.settings.max_download_bytes,
                ),
            }
        raise ValueError("source must be 'oss' or 'tempo'")
