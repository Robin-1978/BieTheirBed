"""One-shot CLI consumer for the strict Core API."""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import sys
import time
from pathlib import Path

from knoa_platform.config import AppConfig
from knoa_platform.artifacts.delivery import save_download
from knoa_platform.service.core_api import ArtifactInputRef
from knoa_platform.service.core_lifecycle import get_core_client
from knoa_platform.tasks import TaskEvent


async def run_core_ask(
    config: AppConfig,
    question: str,
    *,
    json_output: bool = False,
    no_tools: bool = False,
    attachments: list[str] | None = None,
) -> int:
    if question == "-":
        question = sys.stdin.read().strip()
        if not question:
            print("Error: no input from stdin", file=sys.stderr)
            return 1

    started = time.monotonic()
    tool_calls = 0
    answer_parts: list[str] = []
    final_answer: str | None = None
    artifacts: list[dict[str, str]] = []
    warnings: list[str] = []
    error = ""
    client = None
    try:
        client = await get_core_client(
            config,
            approval_handler=_confirm_in_terminal,
        )
        health = await client.health()
        if not health.healthy:
            error = health.detail or "No configured model is available"
        else:
            session_handle = await client.create_session()
            refs: list[ArtifactInputRef] = []
            for path in attachments or []:
                refs.append(
                    await _upload_attachment(client, session_handle, path)
                )
            async for event in client.execute_task(
                session_handle,
                question,
                tuple(refs),
                tools_enabled=not no_tools,
            ):
                if event.event_type == "content_delta":
                    answer_parts.append(event.payload.content)
                elif event.event_type == "final_output":
                    final_answer = event.payload.content
                elif event.event_type == "completed" and event.payload.content:
                    final_answer = event.payload.content
                elif event.event_type == "tool_call":
                    tool_calls += 1
                elif event.event_type == "artifact" and event.payload.artifact:
                    try:
                        downloaded = await client.download_artifact(
                            session_handle,
                            event.payload.artifact.artifact_id,
                        )
                        target = await asyncio.to_thread(
                            save_download,
                            downloaded,
                            Path(config.runtime_root) / "downloads",
                        )
                    except Exception as exc:
                        warnings.append(f"Artifact download failed: {exc}")
                    else:
                        artifacts.append(
                            {
                                "artifact_id": downloaded.artifact.artifact_id,
                                "path": str(target),
                            }
                        )
                elif event.event_type in {"failed", "cancelled"}:
                    error = event.payload.content or event.event_type
    except Exception as exc:
        error = str(exc)
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    answer = (
        final_answer
        if final_answer is not None
        else "".join(answer_parts)
    ).strip()
    elapsed = time.monotonic() - started
    model = config.resolve_model()
    metrics = {
        "elapsed_seconds": round(elapsed, 3),
        "tool_calls": tool_calls,
        "model": model.alias,
        "provider": model.provider_name,
    }
    if json_output:
        print(
            json.dumps(
                {
                    "question": question,
                    "answer": answer if not error else None,
                    "artifacts": artifacts,
                    "warnings": warnings,
                    "metrics": metrics,
                    "error": error or None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Question: {question}")
        print(f"Answer: {answer if not error else 'ERROR: ' + error}")
        if artifacts:
            print("Artifacts:")
            for artifact in artifacts:
                print(f"  {artifact['artifact_id']}: {artifact['path']}")
        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"  {warning}")
        print("---")
        print(f"Time: {elapsed:.2f}s")
        print(f"Tool calls: {tool_calls}")
    return 0 if not error else 1


async def _upload_attachment(client, session_handle: str, path: str) -> ArtifactInputRef:
    source = Path(path).expanduser().resolve()
    data = await asyncio.to_thread(source.read_bytes)
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    if not media_type.startswith("image/"):
        raise ValueError(f"Attachment is not an image: {source.name}")
    data_url = f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
    artifact = await client.upload_artifact(
        session_handle,
        data_url,
        media_type=media_type,
        caption=source.name,
    )
    return ArtifactInputRef(artifact_id=artifact.artifact_id, caption=source.name)


async def _confirm_in_terminal(event: TaskEvent) -> bool:
    payload = event.payload
    details = "\n".join(
        f"  {key}: {value}" for key, value in payload.tool_args.items()
    )
    print(f"\nConfirmation required: {payload.tool_name}", file=sys.stderr)
    if details:
        print(details, file=sys.stderr)
    print(f"  reason: {payload.reason}", file=sys.stderr)
    try:
        answer = await asyncio.to_thread(input, "Proceed? (y/n): ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in {"y", "yes"}
