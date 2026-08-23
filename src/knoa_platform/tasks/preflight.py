"""Core-owned operational preflight for every Product Task launch path."""
from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from knoa_platform.agent_runtime.contracts import HealthStatus, RuntimeScope
from knoa_platform.runtime import RuntimePaths
from knoa_platform.tasks.models import (
    TaskDefinitionRecord,
    TaskPreflightCheck,
)


class TaskLaunchPreflightEvaluator:
    """Evaluate configuration, runtime, credential, workspace and tool readiness."""

    def __init__(
        self,
        *,
        current_configuration: Callable[[], Any],
        configuration_state: Callable[[], Any],
        provider_secret_status: Callable[[str], dict[str, object]],
        runtime_health: Callable[[], Awaitable[HealthStatus]],
        tool_count: Callable[[RuntimeScope], int],
        runtime_root: Path,
    ) -> None:
        self._current_configuration = current_configuration
        self._configuration_state = configuration_state
        self._provider_secret_status = provider_secret_status
        self._runtime_health = runtime_health
        self._tool_count = tool_count
        self._runtime_root = runtime_root

    @staticmethod
    def _check(
        check_id: str,
        status: str,
        detail: str,
        recommended_action: str = "none",
    ) -> TaskPreflightCheck:
        return TaskPreflightCheck(
            check_id=check_id,
            status=status,
            detail=detail,
            recommended_action=recommended_action,
        )

    async def __call__(
        self,
        task: TaskDefinitionRecord,
    ) -> tuple[TaskPreflightCheck, ...]:
        checks: list[TaskPreflightCheck] = []
        try:
            revision = await asyncio.to_thread(self._current_configuration)
            control = await asyncio.to_thread(self._configuration_state)
            agent = revision.document.agents.agents.get(task.agent_id)
            if agent is None:
                checks.append(self._check(
                    "agent_config", "blocked",
                    "任务使用的 Agent 不存在，请重新选择 Agent", "configure",
                ))
            elif not agent.enabled:
                checks.append(self._check(
                    "agent_config", "blocked",
                    "任务使用的 Agent 已停用，请重新选择 Agent", "configure",
                ))
            else:
                checks.append(self._check(
                    "agent_config", "ready", "任务使用的 Agent 已配置"
                ))
                if agent.kind == "codex":
                    executable = agent.command[0] if agent.command else ""
                    binary_ready = bool(executable and shutil.which(executable))
                    checks.append(self._check(
                        "runtime_binary",
                        "ready" if binary_ready else "blocked",
                        (
                            "Codex Runtime 命令可用"
                            if binary_ready
                            else "找不到 Codex Runtime 命令，请在 Node 上安装或修正 Agent 配置"
                        ),
                        "none" if binary_ready else "configure",
                    ))
                    paths = RuntimePaths.from_root(self._runtime_root)
                    workspace = (
                        paths.resolve(agent.cwd, default_parent=paths.root)
                        if agent.cwd
                        else paths.resolve(
                            f"agents/{task.agent_id}/workspace",
                            default_parent=paths.root,
                        )
                    )
                    if workspace.is_dir():
                        workspace_status = ("ready", "Codex 工作目录可用", "none")
                    elif workspace.parent.is_dir() and os.access(workspace.parent, os.W_OK):
                        workspace_status = (
                            "warning",
                            "Codex 工作目录尚未创建，执行时会自动创建",
                            "none",
                        )
                    else:
                        workspace_status = (
                            "blocked",
                            "Codex 工作目录不可用，请检查路径和权限",
                            "configure",
                        )
                    checks.append(self._check("workspace", *workspace_status))

                binding = agent.model_binding
                if binding.ownership == "platform":
                    model = revision.document.models.get(binding.model)
                    provider = (
                        None
                        if model is None
                        else revision.document.providers.get(model.provider)
                    )
                    if model is None or provider is None:
                        checks.append(self._check(
                            "model", "blocked",
                            "任务使用的模型配置不完整，请在 Console 检查模型和 Provider",
                            "configure",
                        ))
                    else:
                        checks.append(self._check(
                            "model", "ready", f"模型已配置：{binding.model}"
                        ))
                        if provider.requires_api_key is True:
                            credential_ready = False
                            if provider.api_key_ref:
                                try:
                                    status = await asyncio.to_thread(
                                        self._provider_secret_status,
                                        provider.api_key_ref,
                                    )
                                    credential_ready = bool(status.get("configured"))
                                except Exception:
                                    credential_ready = False
                            elif provider.api_key_env:
                                credential_ready = bool(os.environ.get(provider.api_key_env))
                            checks.append(self._check(
                                "credentials",
                                "ready" if credential_ready else "blocked",
                                (
                                    "模型凭据已配置"
                                    if credential_ready
                                    else "模型凭据未配置，请在 Console 设置 API Key"
                                ),
                                "none" if credential_ready else "configure",
                            ))

            if control.apply_status == "failed":
                config_check = (
                    "blocked",
                    "Node 配置应用失败，请在 Console 修复配置后重试",
                    "configure",
                )
            elif control.apply_status == "applying":
                config_check = (
                    "warning",
                    "Node 配置正在应用，执行可能使用上一版本配置",
                    "retry",
                )
            else:
                config_check = ("ready", "Node 配置已应用", "none")
            checks.append(self._check("config", *config_check))
        except Exception:
            checks.append(self._check(
                "config", "blocked",
                "无法读取 Node 配置，请检查 Node 状态后重试", "retry",
            ))

        runtime_ready = False
        try:
            health = await self._runtime_health()
            runtime_ready = health.healthy
        except Exception:
            runtime_ready = False
        checks.append(self._check(
            "runtime",
            "ready" if runtime_ready else "blocked",
            (
                "Agent Runtime 可用"
                if runtime_ready
                else "Agent Runtime 当前不可用，请检查 Node 状态后重试"
            ),
            "none" if runtime_ready else "retry",
        ))
        if task.tools_enabled and runtime_ready:
            try:
                scope = RuntimeScope(
                    principal_id=task.principal_id,
                    session_handle=task.session_handle,
                )
                count = await asyncio.to_thread(self._tool_count, scope)
                checks.append(self._check(
                    "tools",
                    "ready" if count else "warning",
                    (
                        f"已发现 {count} 项可用工具能力"
                        if count
                        else "当前没有可用工具，任务仍可执行但可能无法完成外部操作"
                    ),
                    "none" if count else "configure",
                ))
            except Exception:
                checks.append(self._check(
                    "tools", "blocked",
                    "无法读取工具能力，请检查 Agent Runtime 后重试", "retry",
                ))
        return tuple(checks)
