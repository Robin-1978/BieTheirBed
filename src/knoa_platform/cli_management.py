"""Generic non-interactive client commands for durable Knoa work."""
from __future__ import annotations

import json
import uuid
from typing import Any

from knoa_platform.config import AppConfig
from knoa_platform.service.core_lifecycle import get_core_client


def enabled_agents(config: AppConfig) -> tuple[str, ...]:
    return tuple(
        agent_id
        for agent_id, agent in config.agents.items()
        if agent.enabled
    )


async def run_client_command(config: AppConfig, command: str, **values: Any) -> int:
    if command == "agents":
        for agent_id in enabled_agents(config):
            marker = " *" if agent_id == config.default_agent else ""
            print(f"{agent_id}{marker}")
        return 0

    client = await get_core_client(config)
    try:
        if command == "tasks":
            tasks = await client.list_product_tasks(limit=values.get("limit", 50))
            for task in tasks:
                print(
                    f"{task.task_id}\t{task.state}\t{task.agent_id}\t"
                    f"{task.execution_count}\t{task.title}"
                )
        elif command == "task":
            task = await client.get_product_task(values["task_id"])
            print(task.model_dump_json(indent=2))
        elif command == "executions":
            executions = await client.list_product_task_executions(values["task_id"])
            for execution in executions:
                print(
                    f"{execution.execution_id}\t{execution.state}\t"
                    f"{execution.launch_reason}\t{execution.agent_id_snapshot}"
                )
        elif command == "execution":
            execution = await client.get_product_task_execution(values["execution_id"])
            print(execution.model_dump_json(indent=2))
        elif command in {"approve", "deny"}:
            result = await client.resolve_approval(
                values["approval_id"],
                approved=command == "approve",
            )
            print(result.model_dump_json(indent=2))
        elif command == "resolve":
            value = json.loads(values["value"])
            result = await client.resolve_interaction(values["interaction_id"], value)
            print(result.model_dump_json(indent=2))
        elif command == "follow-up":
            execution = await client.continue_product_task(
                values["task_id"],
                input=values["input"],
                client_request_id=str(uuid.uuid4()),
            )
            print(execution.model_dump_json(indent=2))
        else:  # pragma: no cover - argparse constrains this
            raise ValueError(f"Unsupported client command: {command}")
    finally:
        await client.disconnect()
    return 0
