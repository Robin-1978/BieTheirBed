"""System prompt and runtime context builders."""
from __future__ import annotations

import platform
import time

from pc_assistant.platform_ import get_shell_name
from pc_assistant.context.tags import format_runtime_context, format_session_context


def build_system_prompt(
    tools_description: str = "",
    working_directory: str = "",
    extra_instructions: str = "",
) -> str:
    parts = [
        "<role>",
        "You are PC Assistant, an intelligent AI agent that helps users control their computer "
        "through natural language. You can use tools to perform actions, or answer questions "
        "directly from your knowledge.",
        "</role>",
        "",
        "<instructions>",
        "1. Answer directly when you already know the information (e.g. current date, general knowledge, math).",
        "2. Only call tools when you need external information or need to perform an action.",
        "3. Do NOT call the same tool with the same arguments more than once.",
        "4. Give your final answer as soon as you have enough information.",
        "5. Call only one tool at a time. Wait for the result before deciding the next step.",
        "6. If a tool returns an error, try a different approach instead of repeating.",
        "7. Always reply in the same language as the user's input.",
        "</instructions>",
        "",
        "<safety>",
        "- Never execute destructive commands (e.g. rm -rf /, format C:, del /s /q on system directories)",
        "- Never modify system files or registry without explicit user request",
        "- Destructive operations (deleting files, overwriting data) require user confirmation",
        "- If a tool returns an error, try an alternative approach",
        "</safety>",
        "",
        "<output_format>",
        "- When calling tools, briefly explain why you need to call them",
        "- Final answers should be concise and helpful",
        "- Use <think>...</think> tags for internal reasoning when needed",
        "</output_format>",
    ]

    if tools_description:
        parts.extend([
            "",
            "<available_tools>",
            tools_description,
            "</available_tools>",
        ])

    if extra_instructions:
        parts.extend(["", extra_instructions])

    return "\n".join(parts)


OS_INFO = f"{platform.system()} {platform.release()} ({platform.machine()}) | Shell: {get_shell_name()}"


def build_runtime_context(
    working_directory: str = "",
    memory_context: str = "",
    *,
    system_prompt: str = "",
) -> str:
    """Build runtime context block injected before dialogue history."""
    blocks: list[str] = []

    if memory_context:
        blocks.append(f"<user_memory>\n{memory_context}\n</user_memory>")

    if system_prompt:
        blocks.append(f"<system_rules>\n{system_prompt}\n</system_rules>")

    if not blocks:
        return ""
    return format_runtime_context(*blocks)


def build_session_context(*, working_directory: str = "") -> str:
    """Build session context block pinned before the current dialogue turn."""
    ts = time.strftime("%Y-%m-%d %H:%M %A")
    return format_session_context(ts, working_dir=working_directory, os_info=OS_INFO)