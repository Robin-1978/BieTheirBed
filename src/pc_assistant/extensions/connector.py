"""Authenticated business Connectors behind the standard ToolStep boundary."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from pc_assistant.extensions.manager import ExtensionDescriptor, ExtensionProvider
from pc_assistant.extensions.models import YuqueConnectorConfig
from pc_assistant.extensions.secrets import SecretResolver, SecretValue
from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolOriginKind,
    ToolRisk,
)
from pc_assistant.tools.http_limits import read_limited_json


logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 128 * 1024
_MAX_NAMESPACE_LENGTH = 200
_MAX_DOCUMENT_ID_LENGTH = 200
_MAX_TITLE_LENGTH = 200
_MAX_BODY_LENGTH = 200_000
_YUQUE_NAMESPACE_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
)
_YUQUE_DOCUMENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"


class ConnectorAuthorizationError(RuntimeError):
    pass


class ConnectorRequestError(RuntimeError):
    pass


class ConnectorAuditRecorder:
    """Append metadata-only Connector request records; never arguments or Secrets."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()

    def record(
        self,
        *,
        connector_id: str,
        operation: str,
        outcome: str,
        status_code: int | None,
        elapsed_ms: float,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connector_id": connector_id,
            "operation": operation,
            "outcome": outcome,
            "status_code": status_code,
            "elapsed_ms": round(max(0.0, elapsed_ms), 2),
        }
        encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(
                self._path,
                flags,
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)


class YuqueClientPort(Protocol):
    async def start(self) -> None: ...

    async def get_document(self, namespace: str, identifier: str) -> dict[str, Any]: ...

    async def update_document(
        self,
        namespace: str,
        identifier: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class YuqueHTTPClient:
    def __init__(
        self,
        connector_id: str,
        config: YuqueConnectorConfig,
        token: SecretValue,
        audit: ConnectorAuditRecorder,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._connector_id = connector_id
        self._config = config
        self._token = token
        self._audit = audit
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._client is not None:
            raise RuntimeError("Yuque Connector client is already started")
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url + "/",
            headers={
                "X-Auth-Token": self._token.reveal(),
                "User-Agent": "Knoa-Personal-Agent",
            },
            timeout=httpx.Timeout(self._config.timeout_seconds),
            follow_redirects=False,
            transport=self._transport,
        )
        try:
            await self._request("GET", "user", operation="health")
        except BaseException:
            await self.close()
            raise

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Yuque Connector client is not started")
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._require_client()
        started = time.monotonic()
        status_code: int | None = None
        outcome = "failed"
        try:
            async with client.stream(method, path, json=json_body) as response:
                status_code = response.status_code
                if status_code in {401, 403}:
                    outcome = "reauthorization_required"
                    raise ConnectorAuthorizationError(
                        "Connector authorization required"
                    )
                response.raise_for_status()
                result = await read_limited_json(response, _MAX_RESPONSE_BYTES)
            if not isinstance(result, dict):
                raise ConnectorRequestError("Connector returned an invalid response")
            outcome = "completed"
            data = result.get("data", result)
            return data if isinstance(data, dict) else {"data": data}
        except ConnectorAuthorizationError:
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            raise ConnectorRequestError("Connector request failed") from exc
        finally:
            try:
                await asyncio.to_thread(
                    self._audit.record,
                    connector_id=self._connector_id,
                    operation=operation,
                    outcome=outcome,
                    status_code=status_code,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                )
            except Exception:
                logger.exception(
                    "Connector audit write failed: %s/%s",
                    self._connector_id,
                    operation,
                )

    async def get_document(self, namespace: str, identifier: str) -> dict[str, Any]:
        path = (
            f"repos/{quote(namespace, safe='/')}/docs/"
            f"{quote(identifier, safe='')}"
        )
        return await self._request("GET", path, operation="get_document")

    async def update_document(
        self,
        namespace: str,
        identifier: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        path = (
            f"repos/{quote(namespace, safe='/')}/docs/"
            f"{quote(identifier, safe='')}"
        )
        return await self._request(
            "PUT",
            path,
            operation="update_document",
            json_body={"title": title, "body": body},
        )

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()


def _document_parameters(*, include_content: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "namespace": {
            "type": "string",
            "minLength": 3,
            "maxLength": _MAX_NAMESPACE_LENGTH,
            "pattern": _YUQUE_NAMESPACE_PATTERN,
            "description": "Yuque repository namespace, for example team/repo",
        },
        "identifier": {
            "type": "string",
            "minLength": 1,
            "maxLength": _MAX_DOCUMENT_ID_LENGTH,
            "pattern": _YUQUE_DOCUMENT_PATTERN,
            "description": "Document ID or slug",
        },
    }
    required = ["namespace", "identifier"]
    if include_content:
        properties.update(
            {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_TITLE_LENGTH,
                },
                "body": {
                    "type": "string",
                    "maxLength": _MAX_BODY_LENGTH,
                    "description": "Document Markdown body",
                },
            }
        )
        required.extend(("title", "body"))
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class YuqueGetDocumentTool(ToolBase):
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset(
        {ToolCapability.NETWORK, ToolCapability.CONNECTOR}
    )
    risk = ToolRisk.MEDIUM

    def __init__(self, connector_id: str, client: YuqueClientPort) -> None:
        self.name = f"connector__{connector_id}__get_document"
        self.description = "Read one Yuque document by repository and ID or slug."
        self._client = client

    async def execute(self, **kwargs: Any) -> Any:
        try:
            return await self._client.get_document(
                str(kwargs["namespace"]),
                str(kwargs["identifier"]),
            )
        except ConnectorAuthorizationError:
            return {
                "error": "Connector authorization required",
                "code": "reauthorization_required",
            }
        except ConnectorRequestError:
            return {"error": "Connector request failed"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _document_parameters(include_content=False),
        }


class YuqueUpdateDocumentTool(ToolBase):
    effect = ToolEffect.EXTERNAL_SIDE_EFFECT
    capabilities = frozenset(
        {ToolCapability.NETWORK, ToolCapability.CONNECTOR}
    )
    risk = ToolRisk.HIGH

    def __init__(self, connector_id: str, client: YuqueClientPort) -> None:
        self.name = f"connector__{connector_id}__update_document"
        self.description = "Update the title and Markdown body of one Yuque document."
        self._client = client

    async def execute(self, **kwargs: Any) -> Any:
        try:
            return await self._client.update_document(
                str(kwargs["namespace"]),
                str(kwargs["identifier"]),
                title=str(kwargs["title"]),
                body=str(kwargs["body"]),
            )
        except ConnectorAuthorizationError:
            return {
                "error": "Connector authorization required",
                "code": "reauthorization_required",
            }
        except ConnectorRequestError:
            return {"error": "Connector request failed"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _document_parameters(include_content=True),
        }


class YuqueConnectorProvider(ExtensionProvider):
    def __init__(
        self,
        connector_id: str,
        config: YuqueConnectorConfig,
        secrets: SecretResolver,
        audit: ConnectorAuditRecorder,
        *,
        client_factory: Callable[
            [str, YuqueConnectorConfig, SecretValue, ConnectorAuditRecorder],
            YuqueClientPort,
        ] = YuqueHTTPClient,
    ) -> None:
        self._connector_id = connector_id
        self._config = config
        self._secrets = secrets
        self._audit = audit
        self._client_factory = client_factory
        self._client: YuqueClientPort | None = None
        self._descriptor = ExtensionDescriptor(
            extension_id=f"connector:{connector_id}",
            kind=ToolOriginKind.CONNECTOR,
        )

    @property
    def descriptor(self) -> ExtensionDescriptor:
        return self._descriptor

    async def start(self) -> tuple[ToolBase, ...]:
        token = self._secrets.resolve(self._config.token_secret.get_secret_value())
        client = self._client_factory(
            self._connector_id,
            self._config,
            token,
            self._audit,
        )
        self._client = client
        await client.start()
        return (
            YuqueGetDocumentTool(self._connector_id, client),
            YuqueUpdateDocumentTool(self._connector_id, client),
        )

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()


def build_connector_providers(
    configs: dict[str, YuqueConnectorConfig],
    secrets: SecretResolver,
    audit: ConnectorAuditRecorder,
) -> tuple[YuqueConnectorProvider, ...]:
    return tuple(
        YuqueConnectorProvider(connector_id, config, secrets, audit)
        for connector_id, config in configs.items()
        if config.enabled
    )
