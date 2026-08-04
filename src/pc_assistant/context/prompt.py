"""System prompt and runtime context builders."""
from __future__ import annotations

import logging
import platform
import time
from pathlib import Path

from pc_assistant.platform_ import get_shell_name
from pc_assistant.context.tags import escape, format_runtime_context

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SYSTEM_TEMPLATE_PATH = _PROMPTS_DIR / "system.md"

_DEFAULT_SYSTEM_TEMPLATE = """<role>
You are PC Assistant, an intelligent AI agent that helps users control their computer
through natural language. You can use tools to perform actions, or answer questions
directly from your knowledge.
</role>

<instructions>
1. Answer directly when you already know the information (e.g. current date, general knowledge, math).
2. Only call tools when you need external information or need to perform an action.
3. Do NOT call the same tool with the same arguments more than once.
4. Give your final answer as soon as you have enough information.
5. Call only one tool at a time. Wait for the result before deciding the next step.
6. If a tool returns an error, try a different approach instead of repeating.
7. Always reply in the same language as the user's input.
8. Tools are listed with their core (commonly used) parameters. If a task needs
   a parameter not shown, call describe_tool with the tool's name to get its
   full schema before calling it.
9. When the user denies an operation (a tool result starts with
   [REJECTED:confirmation_denied]), acknowledge it and suggest an alternative
   that stays within what the user approved. Do NOT retry the same operation
   or a dangerously equivalent variant of it.
</instructions>

<safety>
- Never execute destructive commands (e.g. rm -rf /, format C:, del /s /q on system directories)
- Never modify system files or registry without explicit user request
- Destructive operations (deleting files, overwriting data) require user confirmation
- If a tool returns an error, try an alternative approach
</safety>

<output_format>
- When calling tools, briefly explain why you need to call them
- Final answers should be concise and helpful
- Use <think>...</think> tags for internal reasoning when needed
</output_format>
"""


def _load_system_template() -> str:
    try:
        if _SYSTEM_TEMPLATE_PATH.exists():
            return _SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[Prompt] failed to load %s: %s", _SYSTEM_TEMPLATE_PATH, e)
    return _DEFAULT_SYSTEM_TEMPLATE


def build_system_prompt(
    tools_description: str = "",
    working_directory: str = "",
    extra_instructions: str = "",
) -> str:
    parts = [_load_system_template()]

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
    """Build runtime context block.

    Kept for API compatibility; volatile memory now lives in the tail pin
    (``build_session_context``) so the system+tools+history prefix stays
    byte-identical across turns for prompt-cache reuse. The system prompt is
    deliberately NOT duplicated here — it is sent once as the ``role=system``
    message.
    """
    blocks: list[str] = []

    if memory_context:
        blocks.append(f"<user_memory>\n{memory_context}\n</user_memory>")

    if not blocks:
        return ""
    return format_runtime_context(*blocks)


def build_session_context(*, working_directory: str = "", memory_context: str = "", os_info: str = OS_INFO) -> str:
    """Build session context block pinned before the current dialogue turn.

    Memory is injected here (not at the head of the prompt) so that updating
    ``<user_memory>`` does not invalidate the cached system+tools+history prefix.
    Both ``<session>`` and ``<user_memory>`` share one ``<runtime_context>``
    wrapper so the whole block is recognized as a tail-pinned session message.
    """
    ts = time.strftime("%Y-%m-%d %H:%M %A")
    session_body = [
        "<session>",
        f"<current_time>{escape(ts)}</current_time>",
    ]
    if os_info:
        session_body.append(f"<os_info>{escape(os_info)}</os_info>")
    if working_directory:
        session_body.append(f"<working_directory>{escape(working_directory)}</working_directory>")
    session_body.append("</session>")

    blocks = ["\n".join(session_body)]
    if memory_context:
        blocks.append(f"<user_memory>\n{memory_context}\n</user_memory>")
    return format_runtime_context(*blocks)