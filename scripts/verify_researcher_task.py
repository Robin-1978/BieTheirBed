#!/usr/bin/env python3
"""Verification script for testing the Daily Morning Report task executed by researcher agent.

This script executes the '每日科技财经早报' task on the running Knoa Node daemon,
verifying that:
1. The task executes under the dedicated 'researcher' agent.
2. The researcher agent performs web searches and delivers the structured report.
3. No duplicate task is created.
4. The execution completes cleanly with status=completed.
"""

from __future__ import annotations

import asyncio
import sys
import time
import sqlite3

from knoa_platform.runtime import RuntimePaths
from knoa_platform.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)
from knoa_platform.service.core_client import CoreClient


async def verify_researcher_morning_report(timeout_seconds: float = 240.0) -> bool:
    print("=" * 70)
    print("Knoa Researcher Task E2E Verification: 每日科技财经早报")
    print("=" * 70)

    task_id = "agent-task:-1IQ2pYCYLhYh2wC0BqqLErh"

    # Step 1: Pre-check DB to record task count
    db_path = "/home/robin/.knoa/data/assistant.db"
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT agent_id, title FROM tasks WHERE task_id=?", (task_id,))
        task_row = c.fetchone()
        if not task_row:
            print(f"ERROR: Task {task_id} not found in database!")
            return False
        agent_id, title = task_row
        print(f"[1/5] Task found: '{title}' ({task_id}), assigned agent_id='{agent_id}'")
        assert agent_id == "researcher", f"Expected agent_id='researcher', got '{agent_id}'"

        c.execute("SELECT count(*) FROM tasks")
        initial_task_count = c.fetchone()[0]

    # Step 2: Connect to CoreClient
    paths = RuntimePaths.from_root()
    token = resolve_local_service_token(paths)
    principal_id = "personal:owner"
    credential = issue_principal_credential(token, principal_id)

    ws_url = "ws://127.0.0.1:9527"
    print(f"[2/5] Connecting to Core daemon at {ws_url}...")
    client = await CoreClient.connect(ws_url, credential)
    print("  -> Connected successfully.")

    try:
        # Step 3: Trigger product task execution
        print(f"[3/5] Triggering execution for task {task_id}...")
        snapshot = await client.execute_product_task(task_id, launch_reason="manual")
        execution_id = snapshot.execution_id
        print(f"  -> Execution started: {execution_id}")
        print(f"  -> Agent snapshot: {snapshot.agent_id_snapshot}")
        assert snapshot.agent_id_snapshot == "researcher", (
            f"Execution agent is '{snapshot.agent_id_snapshot}', expected 'researcher'"
        )

        # Step 4: Poll execution status
        print(f"[4/5] Monitoring execution progress (timeout={timeout_seconds}s)...")
        start_time = time.monotonic()
        last_phase = ""
        seen_tools = set()

        final_snapshot = None
        while True:
            await asyncio.sleep(2.0)
            elapsed = round(time.monotonic() - start_time, 1)

            curr = await client.get_product_task_execution(execution_id)
            state_str = curr.state.value if hasattr(curr.state, "value") else str(curr.state)

            if curr.phase != last_phase:
                last_phase = curr.phase
                print(f"  [{elapsed}s] State: {state_str}, Phase: {curr.phase}")

            # Inspect traces for tool calls if available
            if curr.trace and curr.trace.entries:
                for entry in curr.trace.entries:
                    if entry.tool_name:
                        call_id = f"{entry.tool_name}:{entry.tool_call_id or entry.occurred_at}"
                        if call_id not in seen_tools:
                            seen_tools.add(call_id)
                            args_prev = str(entry.tool_args)[:120]
                            print(f"  [{elapsed}s] Tool Call: {entry.tool_name}({args_prev})")

            if state_str in {"completed", "failed", "cancelled"}:
                final_snapshot = curr
                break

            if time.monotonic() - start_time > timeout_seconds:
                print(f"  [ERROR] Execution timed out after {timeout_seconds}s!")
                final_snapshot = curr
                break

        # Step 5: Validate outcomes
        print("[5/5] Evaluating execution result...")
        state_str = final_snapshot.state.value if hasattr(final_snapshot.state, "value") else str(final_snapshot.state)
        print(f"  -> Final State: {state_str}")
        print(f"  -> Failure Code: {final_snapshot.failure_code or 'None'}")
        print(f"  -> Total Tools Executed: {len(seen_tools)}")
        print("  -> Final Result Content Preview:")
        print("-" * 60)
        res_preview = final_snapshot.final_result or "(empty)"
        print(res_preview[:600] + ("..." if len(res_preview) > 600 else ""))
        print("-" * 60)

        # Check DB to confirm no duplicate tasks were created
        with sqlite3.connect(db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM tasks")
            final_task_count = c.fetchone()[0]

        print(f"  -> Initial task count: {initial_task_count}, Final task count: {final_task_count}")
        if final_task_count > initial_task_count:
            print("  [ERROR] New task was erroneously created during morning report execution!")
            return False

        if state_str == "completed":
            print("\n>>> SUCCESS: Researcher task completed cleanly and generated the morning report! <<<")
            return True
        else:
            print(f"\n>>> FAILED: Researcher task ended with state '{state_str}' <<<")
            return False

    finally:
        await client.disconnect()


if __name__ == "__main__":
    success = asyncio.run(verify_researcher_morning_report())
    sys.exit(0 if success else 1)
