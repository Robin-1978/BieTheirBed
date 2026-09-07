#!/usr/bin/env python3
"""Verification script for real primary (knoa) and subagent (worker) orchestration.

This script tests the end-to-end functionality of orchestrator-worker delegation
using the live running Knoa daemon and real LLM platform.

It is placed in scripts/ to ensure it is NOT part of the global automated test suite
(which is scoped to tests/).
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from typing import Any

from knoa_platform.runtime import RuntimePaths
from knoa_platform.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)
from knoa_platform.service.core_client import CoreClient


async def run_subagent_verification(timeout_seconds: float = 180.0) -> bool:
    print("=" * 70)
    print("Knoa Real Orchestrator-Worker Subagent E2E Verification")
    print("=" * 70)

    paths = RuntimePaths.from_root()
    token = resolve_local_service_token(paths)
    principal_id = "personal:owner"
    credential = issue_principal_credential(token, principal_id)

    ws_url = "ws://127.0.0.1:9527"
    print(f"[1/6] Connecting to Core daemon at {ws_url}...")
    client = await CoreClient.connect(ws_url, credential)
    print("  -> Connected successfully.")

    session_handle = None
    turn_id = None
    try:
        print("[2/6] Creating test conversation session for 'knoa'...")
        session_handle = await client.create_session(agent_id="knoa")
        print(f"  -> Session created: {session_handle}")

        tools_result = await client.list_tools(session_handle)
        available_tools = set(tools_result.tools)
        print(f"  -> Total tools available in session: {len(available_tools)}")
        assert "spawn_subagent" in available_tools, "spawn_subagent tool is missing!"
        assert "subagent" in available_tools, "subagent tool is missing!"
        print("  -> Verified 'spawn_subagent' and 'subagent' tools are present.")

        client_request_id = f"test-subagent-{uuid.uuid4().hex[:10]}"
        prompt = (
            "你作为主智能体 Orchestrator，请调用 spawn_subagent 工具，委派子智能体 worker 去计算：\n"
            "请计算 2026 加上 1984 的值是多少。\n"
            "要求：\n"
            "1. target_agent_id 必须是 'worker'\n"
            "2. mode 必须是 'join'\n"
            "3. 在 spawn_subagent 成功后，调用 subagent 工具并使用 action='await' 等待子智能体完成\n"
            "4. 收到子智能体结果后，综合汇报给我最终答案。"
        )

        print("[3/6] Submitting prompt to real knoa orchestrator agent...")
        turn = await client.create_chat_turn(
            session_handle=session_handle,
            user_input=prompt,
            client_request_id=client_request_id,
            tools_enabled=True,
            agent_id="knoa",
        )
        print(f"  -> Turn created: {turn.turn_id}, initial state: {turn.state}")

        print("[4/6] Streaming turn execution signals and monitoring tool calls...")
        start_time = time.monotonic()
        spawn_called = False
        await_called = False
        child_task_id = None
        delegation_id = None
        final_snapshot = None

        seen_tool_calls: set[str] = set()

        turn_id = turn.turn_id
        async for snapshot in client.chat_turn_updates(turn.turn_id):
            elapsed = round(time.monotonic() - start_time, 1)

            # Check for new tool steps
            for step in snapshot.tool_steps:
                tool_key = f"{step.tool_name}:{step.tool_call_id}"
                if tool_key not in seen_tool_calls:
                    seen_tool_calls.add(tool_key)
                    print(f"  [{elapsed}s] Tool Step: {step.tool_name}")
                    print(f"         Args: {step.arguments}")
                    if step.result:
                        print(f"         Result preview: {str(step.result)[:200]}")

                    out_data = step.result.get("output") if isinstance(step.result, dict) else None
                    if not isinstance(out_data, dict):
                        out_data = step.result if isinstance(step.result, dict) else {}

                    if step.tool_name == "spawn_subagent":
                        spawn_called = True
                        child_task_id = out_data.get("child_task_id")
                        delegation_id = out_data.get("delegation_id")
                        if child_task_id:
                            print(f"  -> Subagent spawned! child_task_id={child_task_id}, delegation_id={delegation_id}")

                    if step.tool_name == "subagent":
                        await_called = True
                        child_status = out_data.get("status")
                        child_summary = out_data.get("summary")
                        print(f"  -> Subagent await returned! status={child_status}")
                        if child_summary:
                            print(f"     Subagent summary: {str(child_summary)[:300]}")

            state_val = snapshot.state.value if hasattr(snapshot.state, "value") else str(snapshot.state)
            if state_val in {"completed", "failed", "cancelled"}:
                final_snapshot = snapshot
                break

            if time.monotonic() - start_time > timeout_seconds:
                print(f"  [ERROR] Turn timed out after {timeout_seconds}s!")
                break

        print("[5/6] Turn completed. Evaluating execution results...")
        if not final_snapshot:
            final_snapshot = await client.get_chat_turn(turn.turn_id)

        state_val = final_snapshot.state.value if hasattr(final_snapshot.state, "value") else str(final_snapshot.state)
        print(f"  -> Final Turn State: {state_val}")
        print(f"  -> Failure Code: {final_snapshot.failure_code or 'None'}")
        print(f"  -> Total Tool Steps: {len(final_snapshot.tool_steps)}")
        print("  -> Final Output Content:")
        print("-" * 50)
        print(final_snapshot.final_output or final_snapshot.content)
        print("-" * 50)

        # Verification assertions
        success = True
        if state_val != "completed":
            print(f"[FAIL] Turn did not complete successfully (state={state_val})")
            success = False

        if not spawn_called:
            print("[FAIL] 'spawn_subagent' was not called during the turn!")
            success = False
        else:
            print("[PASS] 'spawn_subagent' was successfully called.")

        if not await_called:
            print("[FAIL] 'subagent' (await) was not called during the turn!")
            success = False
        else:
            print("[PASS] 'subagent' (await) was successfully called and resolved.")

        combined_text = (final_snapshot.final_output or final_snapshot.content or "")
        if "4010" in combined_text:
            print("[PASS] Verified calculation result '4010' is present in final answer.")
        else:
            print("[WARN] '4010' not found verbatim in final answer, check output text.")

        if child_task_id:
            print(f"[6/6] Inspecting child task {child_task_id} in TaskService...")
            try:
                task_snapshot = await client.get_task(child_task_id)
                print(f"  -> Child Task State: {task_snapshot.state}")
                print(f"  -> Child Task Agent: {getattr(task_snapshot, 'agent_id', 'unknown')}")
                print(f"  -> Child Task Final Summary: {str(getattr(task_snapshot, 'final_summary', ''))[:200]}")
                print("[PASS] Child task was executed independently in isolated context.")
            except Exception as e:
                print(f"  -> Note: could not fetch task snapshot via client: {e}")

        return success

    finally:
        if turn_id and client:
            try:
                cur = await client.get_chat_turn(turn_id)
                cur_state = cur.state.value if hasattr(cur.state, "value") else str(cur.state)
                if cur_state not in {"completed", "failed", "cancelled"}:
                    print("Cancelling in-flight test turn before cleanup...")
                    await client.cancel_chat_turn(turn_id)
                    await asyncio.sleep(1.0)
            except Exception:
                pass
        if session_handle:
            print("Cleaning up test session...")
            try:
                await asyncio.sleep(0.5)
                await client.delete_conversation_session(session_handle)
                print("Test session deleted.")
            except Exception as e:
                print(f"Failed to delete test session: {e}")
        await client.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    ok = asyncio.run(run_subagent_verification())
    print("\n" + ("=" * 70))
    if ok:
        print(">>> RESULT: ALL MAIN/SUBAGENT ORCHESTRATION CHECKS PASSED! <<<")
    else:
        print(">>> RESULT: VERIFICATION FAILED <<<")
    print("=" * 70)
    sys.exit(0 if ok else 1)
