"""Loopback-only Node Console routes."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from knoa_platform import __version__
from knoa_platform.console_ui import node_console_html
from knoa_platform.configuration import ManagedConfig
from knoa_platform.gateway.pairing import GatewayPairingPayload
from knoa_platform.gateway.protocol import NodeHubEnrollmentRequest, WriteSecretRequest


class ConsoleRoutes:
    async def _console_page(self, request: Request) -> Response:
        if not self._console_local(request):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return HTMLResponse(
            node_console_html(self._console_csrf_token),
            headers=self._console_headers(),
        )

    async def _console_status(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        from knoa_platform.desktop_companion import desktop_companion_status

        return JSONResponse(
            {
                "node": self._node_identity.descriptor(),
                "runtime_version": __version__,
                "hub": self._node_relay.status,
                "p2p": self._p2p.status(),
                "mdns": (
                    self._mdns.status()
                    if self._mdns is not None
                    else {
                        "enabled": False,
                        "available": False,
                        "advertising": False,
                        "responder": False,
                        "addresses": [],
                        "port": 0,
                        "service_type": "_knoa-node._tcp.local.",
                        "last_error": "lan_gateway_disabled_or_not_started",
                    }
                ),
                "desktop": await asyncio.to_thread(desktop_companion_status),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _console_diagnostics(self, request: Request) -> JSONResponse:
        """Run bounded, read-only checks that explain common Node failures."""
        if (error := self._console_authorize(request)) is not None:
            return error

        checks: list[dict[str, str]] = []

        def add(check_id: str, label: str, status: str, detail: str) -> None:
            checks.append({"id": check_id, "label": label, "status": status, "detail": detail})

        add("node", "Node 服务", "ok", "Console API 正常响应")

        async def check_local_port(check_id: str, label: str, port: int) -> None:
            if port <= 0:
                add(check_id, label, "warning", "端口未配置")
                return
            try:
                connection = await asyncio.to_thread(
                    socket.create_connection, ("127.0.0.1", port), 0.4
                )
                connection.close()
                add(check_id, label, "ok", f"127.0.0.1:{port} 正在监听")
            except OSError as exc:
                add(check_id, label, "error", f"127.0.0.1:{port} 无法连接：{exc.strerror or type(exc).__name__}")

        await check_local_port("core_port", "Core 端口", self._config.service_port)
        await check_local_port("gateway_port", "Gateway 端口", self._config.gateway_port)
        await check_local_port("mcp_port", "Capability MCP 端口", self._config.capability_mcp_port)

        for check_id, label, raw_path in (
            ("runtime_root", "运行目录", self._config.runtime_root),
            ("workspace", "工作目录", self._config.working_directory),
        ):
            path = Path(raw_path).expanduser()
            if not path.exists():
                add(check_id, label, "error", f"目录不存在：{path}")
            elif not path.is_dir() or not os.access(path, os.R_OK | os.W_OK):
                add(check_id, label, "error", f"目录不可读写：{path}")
            else:
                add(check_id, label, "ok", str(path))
            try:
                usage = shutil.disk_usage(path if path.exists() else path.parent)
                free_gb = usage.free / (1024 ** 3)
                if free_gb < 1:
                    add(f"disk_{check_id}", f"{label}磁盘空间", "warning", f"剩余 {free_gb:.2f} GB")
            except OSError:
                pass

        mdns = self._mdns.status() if self._mdns is not None else {
            "enabled": False,
            "available": False,
            "advertising": False,
            "responder": False,
            "addresses": [],
            "last_error": "lan_gateway_disabled_or_not_started",
        }
        if not mdns["enabled"]:
            add("mdns", "mDNS", "warning", "局域网 Gateway 或 mDNS 未启用")
        elif not mdns["advertising"]:
            add("mdns", "mDNS", "error", f"未开始广播：{mdns['last_error'] or 'unknown'}")
        elif not mdns["responder"]:
            add("mdns", "mDNS", "warning", "已广播，但查询响应器不可用")
        else:
            addresses = ", ".join(str(item) for item in mdns["addresses"])
            add("mdns", "mDNS", "ok", f"已广播并监听：{addresses}")

        p2p = self._p2p.status()
        if not p2p.get("available"):
            add("p2p", "P2P", "warning", p2p.get("last_error") or "WebRTC Runtime 不可用，将使用 Relay")
        elif p2p.get("connected_peers"):
            add("p2p", "P2P", "ok", f"已连接 {p2p['connected_peers']} 个对端")
        elif p2p.get("answers_total"):
            add("p2p", "P2P", "warning", "Node 已生成应答，但尚无 ICE 对端连接")
        else:
            add("p2p", "P2P", "ok", "运行时可用，等待 App 建连")

        relay = self._node_relay.status
        if not relay.get("enrolled"):
            add("relay", "Relay", "warning", "Node 尚未加入 Workspace Hub")
        elif relay.get("relay_connected"):
            add("relay", "Relay", "ok", "Relay 已连接")
        else:
            add("relay", "Relay", "warning", relay.get("last_error") or "Relay 正在连接")

        try:
            revision, _state, _generations = await self._core.get_config_current(
                self._config.owner_principal_id
            )
            document = revision.document
            add("config", "配置", "ok", f"当前配置已应用：{revision.revision_id}")

            local_llm = next(
                (
                    provider
                    for provider in document.providers.values()
                    if provider.driver == "llamacpp"
                ),
                None,
            )
            if local_llm is None:
                add("llamacpp", "llama.cpp", "warning", "未配置本机 llama.cpp Provider")
            else:
                endpoint = local_llm.server_url or local_llm.api_base
                parsed = urlsplit(endpoint)
                if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port:
                    await check_local_port("llamacpp", "llama.cpp", parsed.port)
                elif endpoint:
                    add("llamacpp", "llama.cpp", "ok", f"已配置远程 Endpoint：{endpoint}")
                else:
                    add("llamacpp", "llama.cpp", "error", "Provider Endpoint 未配置")

            codex = document.agents.agents.get("codex")
            if codex is None or not codex.enabled:
                add("codex", "Codex Runtime", "warning", "Codex Agent 未启用")
            elif not codex.command:
                add("codex", "Codex Runtime", "error", "Codex command 未配置")
            elif shutil.which(codex.command[0]) is None:
                add("codex", "Codex Runtime", "error", f"找不到可执行文件：{codex.command[0]}")
            else:
                try:
                    probe = await asyncio.to_thread(
                        subprocess.run,
                        [*codex.command, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        cwd=self._config.working_directory,
                        check=False,
                    )
                    if probe.returncode != 0:
                        detail = (probe.stderr or probe.stdout or "command failed").strip().splitlines()[0]
                        add("codex", "Codex Runtime", "error", f"命令无法正常执行：{detail[:240]}")
                    else:
                        version = (probe.stdout or probe.stderr or "可执行").strip().splitlines()[0]
                        add("codex", "Codex Runtime", "ok", f"{version[:240]}")
                except subprocess.TimeoutExpired:
                    add("codex", "Codex Runtime", "error", "Runtime 检查超过 3 秒")
                except OSError as exc:
                    add("codex", "Codex Runtime", "error", f"Runtime 启动失败：{exc}")

            if not document.vision_enabled:
                add("vision", "图片理解", "warning", "图片理解能力未启用")
            elif not document.vision_model:
                add("vision", "图片理解", "warning", "尚未配置图片理解模型")
            else:
                vision = document.models.get(document.vision_model)
                if vision is None:
                    add("vision", "图片理解", "error", f"找不到模型：{document.vision_model}")
                elif vision.supports_vision is not True:
                    add("vision", "图片理解", "error", f"模型未声明图片能力：{document.vision_model}")
                else:
                    add("vision", "图片理解", "ok", f"当前模型：{document.vision_model}")
        except Exception as exc:  # noqa: BLE001
            add("config", "配置", "error", f"无法读取当前配置：{type(exc).__name__}")
            add("codex", "Codex Runtime", "warning", "配置读取失败，无法检查")
            add("vision", "图片理解", "warning", "配置读取失败，无法检查")

        status = "error" if any(item["status"] == "error" for item in checks) else (
            "warning" if any(item["status"] == "warning" for item in checks) else "ok"
        )
        return JSONResponse(
            {"status": status, "checks": checks},
            headers={"Cache-Control": "no-store"},
        )

    async def _console_hub_enroll(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        try:
            raw = await request.body()
            if len(raw) > 16 * 1024:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
            parsed = NodeHubEnrollmentRequest.model_validate_json(raw)
            enrollment = await self._node_hub.enroll(parsed)
            await self._node_relay.restart()
        except ValidationError:
            return JSONResponse({"error": "invalid_enrollment_code"}, status_code=400)
        except PermissionError:
            return JSONResponse({"error": "enrollment_rejected"}, status_code=401)
        except Exception:
            return JSONResponse({"error": "hub_unavailable"}, status_code=503)
        return JSONResponse(
            {"enrollment": enrollment.__dict__, "relay_connected": False},
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    async def _console_pairing(self, request: Request) -> Response:
        if (error := self._console_authorize(request)) is not None:
            return error
        enrollment = self._node_hub_store.load()
        if enrollment is None:
            return JSONResponse({"error": "node_not_enrolled"}, status_code=409)
        try:
            grant = self._identities.create_pairing_grant(
                self._config.owner_principal_id,
                ttl_seconds=300,
            )
            payload = GatewayPairingPayload.from_grant(
                grant,
                enrollment.hub_url,
                transport="relay",
                node_id=self._node_identity.node_id,
                node_signing_public_key=self._node_identity.signing_public_key,
                node_configuration_public_key=(
                    self._node_identity.configuration_public_key
                ),
            ).encoded()
            import qrcode

            code = qrcode.QRCode(border=2)
            code.add_data(payload)
            code.make(fit=True)
            stream = BytesIO()
            code.make_image(fill_color="black", back_color="white").save(
                stream,
                format="PNG",
            )
        except (LookupError, ValueError):
            return JSONResponse({"error": "pairing_unavailable"}, status_code=409)
        return Response(
            stream.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": "inline; filename=knoa-pairing.png",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _console_lifecycle(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        try:
            body = await asyncio.to_thread(self._host_lifecycle.status)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=503)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    async def _console_lifecycle_action(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        raw = await request.body()
        if len(raw) > 16 * 1024:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            payload = json.loads(raw)
            body = await asyncio.to_thread(self._host_lifecycle.action, payload)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_action"}, status_code=400)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=503)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    async def _console_lifecycle_bundle(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        name = request.path_params["name"]
        if not name.endswith(".zip"):
            return JSONResponse({"error": "invalid_bundle_name"}, status_code=400)
        try:
            destination = self._host_lifecycle.bundle_path(name)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        size = 0
        try:
            with temporary.open("xb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024 * 1024:
                        raise OverflowError
                    stream.write(chunk)
            if size == 0:
                raise ValueError("empty_bundle")
            os.replace(temporary, destination)
        except OverflowError:
            temporary.unlink(missing_ok=True)
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            return JSONResponse({"error": "bundle_upload_failed"}, status_code=400)
        return JSONResponse({"bundle_name": name, "size_bytes": size}, status_code=201)

    async def _console_config(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        try:
            revision, state, generations = await self._core.get_config_current(
                self._config.owner_principal_id
            )
        except Exception as error:
            return self._core_error(error)
        return JSONResponse(
            {
                "revision": revision.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
                "generations": generations,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _console_config_publish(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        raw = await request.body()
        if len(raw) > 1024 * 1024:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            payload = json.loads(raw)
            document = ManagedConfig.model_validate(payload.get("document"))
            summary = str(payload.get("summary") or "Node Console configuration update")[:512]
            principal = self._config.owner_principal_id
            draft = await self._core.create_config_draft(principal)
            draft = await self._core.replace_config_draft(
                principal,
                draft.draft_id,
                document,
                expected_version=draft.draft_version,
            )
            validation = await self._core.validate_config_draft(
                principal,
                draft.draft_id,
                preflight=True,
            )
            if not validation.valid:
                return JSONResponse(
                    {"error": "preflight_failed", "validation": validation.model_dump(mode="json")},
                    status_code=422,
                )
            result = await self._core.publish_config_draft(
                principal,
                draft.draft_id,
                expected_version=draft.draft_version,
                summary=summary,
            )
        except ValidationError as error:
            issue = error.errors(include_url=False)[0]
            return JSONResponse(
                {
                    "error": "invalid_configuration",
                    "detail": str(issue.get("msg") or "Configuration is invalid"),
                    "path": ".".join(str(part) for part in issue.get("loc", ())),
                },
                status_code=400,
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            return JSONResponse({"error": "invalid_configuration"}, status_code=400)
        except Exception as error:
            return self._core_error(error)
        workspace_sync: dict = {}
        try:
            workspace_sync = await self._node_relay.sync_workspace_resources()
        except Exception as error:  # Local configuration remains applied.
            workspace_sync = {"error": type(error).__name__}
        return JSONResponse(
            {
                "result": result.model_dump(mode="json"),
                "workspace_sync": workspace_sync,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _console_workspace_resources(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        try:
            state = await self._node_relay.workspace_resource_state()
        except PermissionError:
            return JSONResponse({"error": "node_not_enrolled"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "hub_unavailable"}, status_code=503)
        return JSONResponse(state, headers={"Cache-Control": "no-store"})

    async def _console_secret(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        reference = request.path_params["reference"]
        try:
            if request.method == "GET":
                status = await asyncio.to_thread(self._provider_secrets.status, reference)
            else:
                raw = await request.body()
                if len(raw) > 70_000:
                    return JSONResponse({"error": "payload_too_large"}, status_code=413)
                parsed = WriteSecretRequest.model_validate_json(raw)
                status = await asyncio.to_thread(
                    self._provider_secrets.put,
                    reference,
                    parsed.value,
                )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except (OSError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse(status, headers={"Cache-Control": "no-store"})

    def _console_authorize(self, request: Request) -> JSONResponse | None:
        if not self._console_local(request):
            return JSONResponse({"error": "not_found"}, status_code=404)
        supplied = request.headers.get("X-Knoa-Console", "")
        if not supplied or not hmac.compare_digest(
            supplied,
            self._console_csrf_token,
        ):
            return JSONResponse({"error": "console_csrf_rejected"}, status_code=403)
        return None

    @staticmethod
    def _console_local(request: Request) -> bool:
        if request.client is None:
            return False
        try:
            return ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _console_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }


__all__ = ["ConsoleRoutes"]
