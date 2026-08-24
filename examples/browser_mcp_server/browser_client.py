"""Isolated Chromium/CDP implementation owned entirely by Browser MCP."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import mimetypes
import os
import signal
import shutil
import socket
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import websockets

MAX_SNAPSHOT_NODES = 240
MAX_SNAPSHOT_BYTES = 48 * 1024
MAX_FIELD_CHARS = 500
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
SESSION_TTL_SECONDS = 30 * 60


def _browser_executable() -> str:
    """Resolve a supported Chromium browser across Knoa's desktop platforms."""

    configured = os.environ.get("KNOA_BROWSER_CHROME", "").strip().strip('"')
    command_candidates = (
        configured,
        "google-chrome",
        "chromium",
        "chromium-browser",
        "chrome",
        "chrome.exe",
        "msedge",
        "msedge.exe",
    )
    for candidate in command_candidates:
        if not candidate:
            continue
        expanded = os.path.expandvars(os.path.expanduser(candidate))
        if Path(expanded).is_file():
            return str(Path(expanded).resolve())
        discovered = shutil.which(expanded)
        if discovered:
            return discovered

    path_candidates = (
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
        r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    )
    for candidate in path_candidates:
        expanded = Path(os.path.expandvars(candidate)).expanduser()
        if expanded.is_file():
            return str(expanded.resolve())
    return ""


def _safe_url(value: str, allow_private_origins: frozenset[str]) -> str:
    normalized = value.strip()
    if len(normalized) > 4096 or "\x00" in normalized:
        raise ValueError("URL is empty or too long")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only explicit http/https URLs are supported")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URLs must not contain credentials or fragments")
    origin = f"{parsed.scheme}://{parsed.hostname.lower()}"
    if parsed.port:
        origin += f":{parsed.port}"
    if origin in allow_private_origins:
        return normalized
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("URL host could not be resolved") from exc
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError(
            "URL host has a non-public DNS result and was blocked to prevent "
            "DNS-rebinding/SSRF; do not retry the same URL. An operator may "
            "allow an exact trusted origin with KNOA_BROWSER_ALLOW_PRIVATE_ORIGINS"
        )
    return normalized


def _managed_file(path: Path, root: Path, media_type: str) -> dict[str, Any]:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    size = resolved.stat().st_size
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return {
        "kind": "managed_file",
        "relative_handle": resolved.relative_to(root.resolve()).as_posix(),
        "name": resolved.name,
        "media_type": media_type,
        "size_bytes": size,
        "sha256": digest,
    }


@dataclass
class BrowserSession:
    session_id: str
    profile: Path
    download_directory: Path
    process: asyncio.subprocess.Process
    websocket: Any
    allow_private_origins: frozenset[str]
    temporary: bool = True
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    _command_id: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    element_refs: dict[str, int] = field(default_factory=dict)

    async def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.touched_at = time.time()
        async with self._lock:
            self._command_id += 1
            command_id = self._command_id
            await self.websocket.send(json.dumps({
                "id": command_id, "method": method, "params": params or {},
            }))
            while True:
                message = json.loads(await asyncio.wait_for(self.websocket.recv(), timeout=30))
                if message.get("id") != command_id:
                    continue
                if "error" in message:
                    raise RuntimeError(str(message["error"].get("message") or "CDP command failed"))
                return dict(message.get("result") or {})

    async def ready(self, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = await self.command("Runtime.evaluate", {
                "expression": "document.readyState",
                "returnByValue": True,
            })
            state = str(((result.get("result") or {}).get("value") or ""))
            if state in {"interactive", "complete"}:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("Browser navigation did not become ready")

    async def close(self) -> None:
        try:
            await self.websocket.close()
        except Exception:
            pass
        if hasattr(os, "killpg"):
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif self.process.returncode is None:
            self.process.terminate()
        if self.process.returncode is None:
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                if hasattr(os, "killpg"):
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    self.process.kill()
                await self.process.wait()
        await asyncio.sleep(0.05)
        if self.temporary:
            shutil.rmtree(self.profile, ignore_errors=True)
        shutil.rmtree(self.download_directory, ignore_errors=True)


class BrowserManager:
    def __init__(self) -> None:
        state = os.environ.get("KNOA_BROWSER_STATE_ROOT", "").strip()
        downloads = (
            os.environ.get("KNOA_BROWSER_DOWNLOAD_ROOT", "").strip()
            or os.environ.get("KNOA_MCP_MANAGED_FILE_ROOT", "").strip()
        )
        self.state_root = Path(state).expanduser().resolve() if state else Path(tempfile.gettempdir()) / "knoa-browser-profiles"
        self.download_root = Path(downloads).expanduser().resolve() if downloads else Path(tempfile.gettempdir()) / "knoa-browser-downloads"
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.download_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.chrome = _browser_executable()
        allowed = os.environ.get("KNOA_BROWSER_ALLOW_PRIVATE_ORIGINS", "")
        self.allow_private_origins = frozenset(item.strip().lower().rstrip("/") for item in allowed.split(",") if item.strip())
        self.sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def cleanup_expired(self) -> None:
        expired = [
            item for item in self.sessions.values()
            if time.time() - item.touched_at > SESSION_TTL_SECONDS
        ]
        for item in expired:
            self.sessions.pop(item.session_id, None)
            await item.close()

    async def open(self, *, profile_name: str = "") -> dict[str, Any]:
        await self.cleanup_expired()
        if not self.chrome:
            raise RuntimeError(
                "Chrome, Chromium, or Microsoft Edge is required; install one or "
                "set KNOA_BROWSER_CHROME to its executable path"
            )
        if profile_name and not profile_name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Persistent profile names use only letters, numbers, '-' and '_'")
        async with self._lock:
            session_id = "bs_" + uuid.uuid4().hex
            temporary = not bool(profile_name)
            profile = (
                Path(tempfile.mkdtemp(prefix="session-", dir=self.state_root))
                if temporary else self.state_root / f"profile-{profile_name}"
            )
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            download_dir = self.download_root / session_id
            download_dir.mkdir(mode=0o700)
            port_socket = socket.socket()
            port_socket.bind(("127.0.0.1", 0))
            port = int(port_socket.getsockname()[1])
            port_socket.close()
            process = await asyncio.create_subprocess_exec(
                self.chrome,
                "--headless=new", "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", "--disable-sync",
                "--disable-extensions", "--disable-component-update",
                "--disable-features=OptimizationHints,MediaRouter",
                f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                "about:blank",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                ws_url = await self._debugger_url(port, process)
                websocket = await websockets.connect(ws_url, max_size=2 * 1024 * 1024)
            except Exception:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                if temporary:
                    shutil.rmtree(profile, ignore_errors=True)
                raise
            session = BrowserSession(
                session_id, profile, download_dir, process, websocket,
                self.allow_private_origins, temporary,
            )
            await session.command("Page.enable")
            await session.command("Runtime.enable")
            await session.command("DOM.enable")
            await session.command("Network.enable")
            self.sessions[session_id] = session
            return {"browser_session_id": session_id, "temporary_profile": temporary}

    async def _debugger_url(self, port: int, process: asyncio.subprocess.Process) -> str:
        deadline = time.monotonic() + 15
        async with httpx.AsyncClient(timeout=1) as client:
            while time.monotonic() < deadline:
                if process.returncode is not None:
                    raise RuntimeError("Chrome exited while starting")
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/json")
                    for target in response.json():
                        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                            return str(target["webSocketDebuggerUrl"])
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
                await asyncio.sleep(0.1)
        raise TimeoutError("Chrome DevTools endpoint did not start")

    def get(self, session_id: str) -> BrowserSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise LookupError("Browser session does not exist or has expired")
        return session

    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        auto_opened = not session_id
        if auto_opened:
            opened = await self.open()
            session_id = str(opened["browser_session_id"])
        session = self.get(session_id)
        try:
            safe = _safe_url(url, session.allow_private_origins)
            result = await session.command("Page.navigate", {"url": safe})
            if result.get("errorText"):
                raise RuntimeError(str(result["errorText"]))
            await session.ready()
            location = await session.command(
                "Runtime.evaluate",
                {"expression": "location.href", "returnByValue": True},
            )
            final_url = str(((location.get("result") or {}).get("value") or ""))
            _safe_url(final_url, session.allow_private_origins)
            return {
                "browser_session_id": session_id,
                "url": final_url,
                "auto_opened": auto_opened,
            }
        except BaseException:
            if auto_opened:
                await self.close(session_id)
            raise

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        result = await session.command("Accessibility.getFullAXTree", {"depth": 8})
        nodes: list[dict[str, Any]] = []
        refs: dict[str, int] = {}
        total = 0
        for raw in result.get("nodes", []):
            if len(nodes) >= MAX_SNAPSHOT_NODES or total >= MAX_SNAPSHOT_BYTES:
                break
            role = str((raw.get("role") or {}).get("value") or "")[:80]
            name = str((raw.get("name") or {}).get("value") or "")[:MAX_FIELD_CHARS]
            value = str((raw.get("value") or {}).get("value") or "")[:MAX_FIELD_CHARS]
            if not role or (not name and not value and role in {"none", "generic", "StaticText"}):
                continue
            backend_id = int(raw.get("backendDOMNodeId") or 0)
            ref = ""
            if backend_id and role in {"button", "link", "textbox", "checkbox", "radio", "combobox", "menuitem"}:
                ref = f"e{len(refs) + 1}"
                refs[ref] = backend_id
            item = {"ref": ref, "role": role, "name": name, "value": value}
            encoded = len(json.dumps(item, ensure_ascii=False).encode())
            if total + encoded > MAX_SNAPSHOT_BYTES:
                break
            total += encoded
            nodes.append(item)
        session.element_refs = refs
        return {
            "browser_session_id": session_id,
            "nodes": nodes,
            "truncated": len(result.get("nodes", [])) > len(nodes),
            "untrusted_page_content": True,
        }

    def _backend_id(self, session: BrowserSession, ref: str) -> int:
        value = session.element_refs.get(ref)
        if value is None:
            raise LookupError("Element ref is stale; take a new snapshot")
        return value

    async def _object_id(self, session: BrowserSession, ref: str) -> str:
        resolved = await session.command("DOM.resolveNode", {
            "backendNodeId": self._backend_id(session, ref),
        })
        object_id = str((resolved.get("object") or {}).get("objectId") or "")
        if not object_id:
            raise LookupError("Element ref can no longer be resolved")
        return object_id

    async def click(self, session_id: str, ref: str) -> dict[str, Any]:
        session = self.get(session_id)
        object_id = await self._object_id(session, ref)
        await session.command("Runtime.callFunctionOn", {
            "objectId": object_id, "functionDeclaration": "function(){this.click()}",
        })
        return {"browser_session_id": session_id, "activated_ref": ref}

    async def fill(self, session_id: str, ref: str, text: str) -> dict[str, Any]:
        if len(text) > 20_000:
            raise ValueError("Input text is too large")
        session = self.get(session_id)
        backend_id = self._backend_id(session, ref)
        await session.command("DOM.focus", {"backendNodeId": backend_id})
        await session.command("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})
        await session.command("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 2})
        await session.command("Input.insertText", {"text": text})
        return {"browser_session_id": session_id, "filled_ref": ref, "characters": len(text)}

    async def submit(self, session_id: str, ref: str) -> dict[str, Any]:
        session = self.get(session_id)
        object_id = await self._object_id(session, ref)
        await session.command("Runtime.callFunctionOn", {
            "objectId": object_id,
            "functionDeclaration": "function(){if(this.form){this.form.requestSubmit()}else{this.click()}}",
        })
        return {"browser_session_id": session_id, "submitted_ref": ref}

    async def wait_for(self, session_id: str, *, url_contains: str = "", text: str = "", timeout_seconds: float = 15) -> dict[str, Any]:
        if not url_contains and not text:
            raise ValueError("wait_for requires url_contains or text")
        session = self.get(session_id)
        deadline = time.monotonic() + min(max(timeout_seconds, 0.1), 60)
        while time.monotonic() < deadline:
            location = await session.command("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
            url = str(((location.get("result") or {}).get("value") or ""))
            snapshot = await self.snapshot(session_id) if text else {"nodes": []}
            found_text = not text or any(text in f"{item['name']} {item['value']}" for item in snapshot["nodes"])
            if (not url_contains or url_contains in url) and found_text:
                return {"browser_session_id": session_id, "matched": True, "url": url}
            await asyncio.sleep(0.2)
        raise TimeoutError("Browser wait condition timed out")

    async def screenshot(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        result = await session.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        data = base64.b64decode(str(result.get("data") or ""), validate=True)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("Screenshot exceeds the managed-file limit")
        path = session.download_directory / f"screenshot-{uuid.uuid4().hex}.png"
        path.write_bytes(data)
        path.chmod(0o600)
        return {"browser_session_id": session_id, "managed_file": _managed_file(path, self.download_root, "image/png")}

    async def download(self, session_id: str, url: str, filename: str = "") -> dict[str, Any]:
        session = self.get(session_id)
        current = _safe_url(url, session.allow_private_origins)
        cookies_result = await session.command("Network.getAllCookies")
        cookies = {str(item["name"]): str(item["value"]) for item in cookies_result.get("cookies", [])}
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, cookies=cookies) as client:
            for _ in range(6):
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise RuntimeError("Download redirect has no destination")
                        current = _safe_url(
                            urljoin(current, location), session.allow_private_origins
                        )
                        continue
                    response.raise_for_status()
                    length_text = response.headers.get("content-length", "").strip()
                    if length_text:
                        try:
                            declared_length = int(length_text)
                        except ValueError as exc:
                            raise ValueError("Download Content-Length is invalid") from exc
                        if declared_length < 0 or declared_length > MAX_DOWNLOAD_BYTES:
                            raise ValueError("Download exceeds the managed-file limit")
                    suggested = (
                        filename.strip()
                        or Path(urlsplit(current).path).name
                        or "download.bin"
                    )
                    safe_name = "".join(
                        char for char in suggested if char.isalnum() or char in "._-"
                    )[:160]
                    if not safe_name or safe_name in {".", ".."}:
                        safe_name = "download.bin"
                    path = session.download_directory / safe_name
                    if path.exists():
                        path = session.download_directory / f"{uuid.uuid4().hex[:8]}-{safe_name}"
                    size = 0
                    try:
                        with path.open("xb") as stream:
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > MAX_DOWNLOAD_BYTES:
                                    raise ValueError(
                                        "Download exceeds the managed-file limit"
                                    )
                                stream.write(chunk)
                    except BaseException:
                        path.unlink(missing_ok=True)
                        raise
                    path.chmod(0o600)
                    media_type = (
                        response.headers.get("content-type", "").split(";", 1)[0]
                        or mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream"
                    )
                    return {
                        "browser_session_id": session_id,
                        "managed_file": _managed_file(
                            path, self.download_root, media_type
                        ),
                    }
        raise RuntimeError("Download has too many redirects")

    async def close(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return {"browser_session_id": session_id, "closed": True, "already_closed": True}
        await session.close()
        return {"browser_session_id": session_id, "closed": True}

    async def shutdown(self) -> None:
        sessions, self.sessions = tuple(self.sessions.values()), {}
        await asyncio.gather(*(item.close() for item in sessions), return_exceptions=True)


__all__ = ["BrowserManager", "MAX_DOWNLOAD_BYTES", "_safe_url"]
