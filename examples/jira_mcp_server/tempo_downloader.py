"""Client for extracting, querying and downloading shared robot records from Tempo."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

SHARE_LINK_RE = re.compile(
    r"shared-record-list/([0-9a-f-]{36})/([A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?)"
)

COLUMBUS_MAP = {
    "TBPR": "https://bot.gs-robot.com",
    "MSCU": "https://bot.eu.gausium-robot.com",
    "MSNA": "https://bot.us.gausium-robot.com",
}
DEFAULT_ENDPOINT = "https://bot.gs-robot.com"


def resolve_tempo_endpoint(sn: str) -> str:
    """Resolve regional Tempo endpoint based on robot Serial Number."""
    if len(sn) >= 4:
        columbus = sn[:4].upper()
        if columbus in COLUMBUS_MAP:
            return COLUMBUS_MAP[columbus]
    return DEFAULT_ENDPOINT


def extract_tempo_share_links(texts: list[str]) -> list[dict[str, str]]:
    """Extract all shared-record-list links from issue text fields."""
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, str]] = []

    for text in texts:
        if not text:
            continue
        for match in SHARE_LINK_RE.finditer(text):
            share_id = match.group(1)
            sn = match.group(2)
            key = (share_id, sn)
            if key not in seen:
                seen.add(key)
                results.append(
                    {
                        "share_id": share_id,
                        "sn": sn,
                        "url": f"https://service.gs-robot.com/#/robot/shared-record-list/{share_id}/{sn}",
                    }
                )
    return results


async def list_tempo_records(
    share_id: str,
    sn: str,
    *,
    status: str | None = None,
    file_type: str | None = None,
    filename: str | None = None,
    limit: int = 100,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Query Tempo API to list records for a shared link."""
    base = resolve_tempo_endpoint(sn)
    params: dict[str, Any] = {
        "page": 1,
        "pageSize": min(100, max(1, limit)),
        "orderBy": "start_time DESC",
    }
    if status:
        params["filter.status"] = status
    if file_type:
        types = file_type.split(",")
        params["filter.type"] = (
            f"[{','.join(t.strip() for t in types)}]"
            if len(types) > 1
            else types[0].strip()
        )
    if filename:
        params["filter.fileName"] = filename

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
        own_client = True

    try:
        resp = await client.get(
            f"{base}/tempo/v1beta1/aggManifests/{share_id}/records",
            params=params,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        records_raw = data.get("records", []) if isinstance(data, dict) else []
        records = [
            {
                "id": str(r.get("id", "")),
                "fileName": str(r.get("fileName", "")),
                "dataType": str(r.get("dataType", "")),
                "status": str(r.get("status", "")),
                "size": int(r.get("size", 0)),
                "startTime": str(r.get("startTime", "")),
                "region": str(r.get("region", "CN")),
            }
            for r in records_raw
            if isinstance(r, dict)
        ]
        return {
            "share_id": share_id,
            "sn": sn,
            "total": data.get("total", len(records)) if isinstance(data, dict) else len(records),
            "records": records,
        }
    finally:
        if own_client:
            await client.aclose()


async def download_tempo_file(
    download_url: str,
    filename: str,
    destination_dir: Path,
    *,
    max_bytes: int = 500 * 1024 * 1024,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Download a file from signed Tempo URL to destination_dir."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:200]
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_path = destination_dir / safe_name

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        own_client = True

    try:
        async with client.stream("GET", download_url) as resp:
            resp.raise_for_status()
            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(
                    f"Tempo file size ({content_length} bytes) exceeds limit ({max_bytes} bytes)"
                )

            fd, tmp_name = tempfile.mkstemp(
                prefix=".tempo-", suffix=".tmp", dir=destination_dir
            )
            os.close(fd)
            tmp_path = Path(tmp_name)

            hasher = hashlib.sha256()
            size = 0
            try:
                with tmp_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError(
                                f"Tempo download exceeded maximum bytes limit ({max_bytes})"
                            )
                        hasher.update(chunk)
                        f.write(chunk)
                    f.flush()
                tmp_path.chmod(0o600)
                os.replace(tmp_path, target_path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
    finally:
        if own_client:
            await client.aclose()

    return {
        "filename": safe_name,
        "path": str(target_path),
        "size": size,
        "sha256": hasher.hexdigest(),
    }
