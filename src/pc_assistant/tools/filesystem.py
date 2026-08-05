from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from pc_assistant.tools.base import ToolBase, parameter, tool


_MAX_FILE_SIZE = 1_048_576


@parameter("content", skim=True, skim_hint="write")
@parameter("destination", public_name="destination_path", skim=True, skim_hint="copy/move")
@parameter("path", public_name="file_path", required=True)
@tool(name="files", description="Read, write, list, copy, move, or delete files and folders.", skim_description="Local files and folders.")
class FilesystemTool(ToolBase):
    name = "filesystem"
    description = "Read, write, list, copy, move, or delete files and directories"
    is_side_effecting = True

    def __init__(self, working_directory: str | Path | None = None) -> None:
        self._working_directory = Path(working_directory or os.getcwd()).expanduser().resolve()

    def _path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self._working_directory / path
        return path.resolve()

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action")
        path = kwargs.get("path", "")
        encoding = kwargs.get("encoding", "utf-8")
        handlers = {
            "read": lambda p, k: self._read(p, k, encoding),
            "write": lambda p, k: self._write(p, k, encoding),
            "list": self._list,
            "mkdir": self._mkdir,
            "delete": self._delete,
            "copy": self._copy,
            "move": self._move,
            "exists": self._exists,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown filesystem action: {action}"}
        return handler(path, kwargs)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write", "list", "mkdir", "delete", "copy", "move", "exists"],
                    },
                    "path": {"type": "string", "description": "Target file or directory path"},
                    "content": {"type": "string", "description": "Content to write (for write action)"},
                    "destination": {"type": "string", "description": "Destination path (for copy/move)"},
                    "encoding": {"type": "string", "description": "File encoding (default: utf-8). Use 'latin-1', 'utf-16', etc. for non-UTF-8 files."},
                },
                "required": ["action", "path"],
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Read and write files. Common actions: read, write, list, exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write", "list", "mkdir", "delete", "copy", "move", "exists"]},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "destination": {"type": "string"},
                },
                "required": ["action", "path"],
            },
        }

    def _read(self, path: str, kwargs: dict[str, Any], encoding: str = "utf-8") -> dict[str, Any]:
        try:
            p = self._path(path)
            if not p.exists():
                return {"error": f"Path does not exist: {path}"}
            if p.is_dir():
                return {"error": f"Path is a directory, not a file: {path}"}
            file_size = p.stat().st_size
            if file_size > _MAX_FILE_SIZE:
                content = p.read_text(encoding=encoding, errors="replace")[:_MAX_FILE_SIZE]
                return {"content": content, "size": file_size, "truncated": True, "max_size": _MAX_FILE_SIZE}
            content = p.read_text(encoding=encoding)
            return {"content": content, "size": file_size}
        except UnicodeDecodeError:
            return {"error": f"Cannot decode file with encoding '{encoding}'. Try a different encoding (e.g., latin-1, utf-16)."}
        except Exception as e:
            return {"error": str(e)}

    def _write(self, path: str, kwargs: dict[str, Any], encoding: str = "utf-8") -> dict[str, Any]:
        content = kwargs.get("content", "")
        try:
            content_bytes = content.encode(encoding)
            if len(content_bytes) > _MAX_FILE_SIZE:
                return {"error": f"Content exceeds maximum size of {_MAX_FILE_SIZE} bytes"}
            p = self._path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding=encoding)
            return {"success": True, "bytes_written": len(content_bytes)}
        except UnicodeEncodeError:
            return {"error": f"Cannot encode content with encoding '{encoding}'. Try a different encoding."}
        except Exception as e:
            return {"error": str(e)}

    def _list(self, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            p = self._path(path)
            if not p.is_dir():
                return {"error": f"Path is not a directory: {path}"}
            entries = []
            for entry in sorted(p.iterdir()):
                entries.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else None,
                })
            return {"entries": entries, "count": len(entries)}
        except Exception as e:
            return {"error": str(e)}

    def _mkdir(self, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            self._path(path).mkdir(parents=True, exist_ok=True)
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    def _delete(self, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            p = self._path(path)
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    def _copy(self, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        destination = kwargs.get("destination", "")
        try:
            src = self._path(path)
            dst = self._path(destination)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    def _move(self, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        destination = kwargs.get("destination", "")
        try:
            shutil.move(self._path(path), self._path(destination))
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    def _exists(self, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        p = self._path(path)
        return {"exists": p.exists(), "is_file": p.is_file(), "is_dir": p.is_dir()}
