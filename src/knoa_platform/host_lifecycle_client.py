"""Local proxy client used by embedded Hub and Node Consoles."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class HostLifecycleClient:
    def __init__(self, endpoint: str, token_file: Path, incoming_root: Path) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token_file = token_file
        self.incoming_root = incoming_root

    @classmethod
    def from_environment(cls) -> HostLifecycleClient | None:
        token_file = os.environ.get("KNOA_LIFECYCLE_TOKEN_FILE", "").strip()
        incoming_root = os.environ.get("KNOA_LIFECYCLE_INCOMING_ROOT", "").strip()
        if not token_file or not incoming_root:
            return None
        return cls(
            os.environ.get("KNOA_LIFECYCLE_ENDPOINT", "http://127.0.0.1:9533"),
            Path(token_file),
            Path(incoming_root),
        )

    def _request(self, path: str, body: dict[str, object] | None = None) -> dict[str, Any]:
        token = self.token_file.read_text(encoding="utf-8").strip()
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=encoded,
            method="GET" if body is None else "POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=1900) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read()).get("error", "lifecycle_request_failed")
            except (json.JSONDecodeError, AttributeError):
                detail = "lifecycle_request_failed"
            raise RuntimeError(detail) from error
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeError("lifecycle_unavailable") from error

    def status(self) -> dict[str, Any]:
        return self._request("/v1/lifecycle")

    def action(self, payload: dict[str, object]) -> dict[str, Any]:
        return self._request("/v1/lifecycle/actions", payload)

    def bundle_path(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError("invalid_bundle_name")
        root = self.incoming_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        destination = (root / name).resolve()
        if destination.parent != root:
            raise ValueError("invalid_bundle_name")
        return destination


__all__ = ["HostLifecycleClient"]
