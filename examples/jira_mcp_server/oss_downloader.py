"""Asynchronous client for listing and downloading files from public OSS buckets."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

_BUCKET = "gs-public-shared"
_BASE_URL = f"https://{_BUCKET}.oss-cn-shanghai.aliyuncs.com"
_PREFIX = f"oss://{_BUCKET}/"
_PATH_RE = re.compile(r"oss://gs-public-shared/[^\s\r\n\]|{},]+")


def extract_oss_links(texts: list[str]) -> list[str]:
    """Extract all unique oss://gs-public-shared/... paths from issue texts."""
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in _PATH_RE.finditer(text):
            found.add(match.group(0))
    return sorted(found)


async def list_oss_objects(
    prefix: str,
    *,
    limit: int = 100,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """List objects in the public bucket under a given prefix."""
    norm_prefix = prefix[len(_PREFIX) :] if prefix.startswith(_PREFIX) else prefix
    norm_prefix = norm_prefix.lstrip("/")

    params = {
        "list-type": "2",
        "prefix": norm_prefix,
        "delimiter": "/",
        "max-keys": str(min(1000, max(1, limit))),
    }

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
        own_client = True

    results: list[dict[str, Any]] = []
    try:
        resp = await client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        tree = ET.fromstring(resp.text)
        ns = ""
        if tree.tag.startswith("{"):
            ns = tree.tag.split("}")[0] + "}"

        for cp in tree.findall(f"{ns}CommonPrefixes"):
            p = cp.findtext(f"{ns}Prefix", "")
            results.append({"path": f"{_PREFIX}{p}", "is_dir": True})
            if len(results) >= limit:
                break

        for content in tree.findall(f"{ns}Contents"):
            key = content.findtext(f"{ns}Key", "")
            size = int(content.findtext(f"{ns}Size", "0"))
            if key.endswith("/") and size == 0:
                continue
            results.append(
                {"path": f"{_PREFIX}{key}", "size": size, "is_dir": False}
            )
            if len(results) >= limit:
                break
    finally:
        if own_client:
            await client.aclose()

    return results


async def download_oss_file(
    oss_url: str,
    destination_dir: Path,
    *,
    max_bytes: int = 500 * 1024 * 1024,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Download one file from oss://gs-public-shared/... to destination_dir."""
    if not oss_url.startswith(_PREFIX):
        raise ValueError(f"Only {_PREFIX} URLs are supported")
    key = oss_url[len(_PREFIX) :]
    if not key or key.endswith("/"):
        raise ValueError("Target OSS URL must be a file, not a directory")

    filename = Path(key).name
    # Basic sanitize
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:200]
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_path = destination_dir / filename

    encoded_key = quote(key, safe="/")
    download_url = f"{_BASE_URL}/{encoded_key}"

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
                    f"OSS file size ({content_length} bytes) exceeds limit ({max_bytes} bytes)"
                )

            # Atomic download to tmp file
            fd, tmp_name = tempfile.mkstemp(
                prefix=".oss-", suffix=".tmp", dir=destination_dir
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
                                f"OSS download exceeded maximum bytes limit ({max_bytes})"
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
        "oss_url": oss_url,
        "filename": filename,
        "path": str(target_path),
        "size": size,
        "sha256": hasher.hexdigest(),
    }
