"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import (
    CancelTaskRequest,
    ContinueProductTaskRequest,
    CreateProductTaskRequest,
    PauseTaskRequest,
    ProductTaskListQuery,
    ResolveApprovalRequest,
    ResumeTaskRequest,
    TaskExecutionListQuery,
    UpdateProductTaskRequest,
)
from knoa_platform.runtime import RuntimePaths
from knoa_platform.tasks import TaskDefinitionState, TaskLaunchKind

logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024


class TaskRoutes:

    async def _create_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse_body(request, CreateProductTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        principal_id = authenticated.device.principal_id
        immediate = parsed.launch_policy.kind is TaskLaunchKind.IMMEDIATE
        try:
            session_handle = await self._core.create_session(
                principal_id,
                activate=False,
                agent_id=parsed.agent_id,
            )
            # Create-and-run must not bypass preflight: the definition is
            # created without launching, checked, and only then started.
            result = await self._core.create_product_task(
                principal_id,
                session_handle,
                parsed.goal,
                client_request_id=parsed.client_request_id,
                title=parsed.title,
                attachments=parsed.attachments,
                tools_enabled=parsed.tools_enabled,
                priority=parsed.priority,
                launch_policy=parsed.launch_policy,
                notification_policy=parsed.notification_policy or None,
                agent_id=parsed.agent_id,
                auto_launch=not immediate,
            )
        except Exception as exc:
            return self._core_error(exc)
        if not immediate:
            return JSONResponse(
                {
                    "task": result.task.model_dump(mode="json"),
                    "execution": (
                        None
                        if result.execution is None
                        else result.execution.model_dump(mode="json")
                    ),
                },
                status_code=201,
            )
        try:
            existing = await self._core.list_product_task_executions(
                principal_id,
                result.task.task_id,
                limit=1,
            )
        except Exception as exc:
            return self._core_error(exc)
        if existing:
            # Idempotent replay of a create whose execution already started
            # (e.g. the first response was lost in transit).
            return JSONResponse(
                {
                    "task": result.task.model_dump(mode="json"),
                    "execution": existing[0].model_dump(mode="json"),
                },
                status_code=201,
            )
        preflight = await self._preflight_task_response(principal_id, result.task.task_id)
        if preflight.status_code != 200:
            return preflight
        preflight_body = json.loads(preflight.body)
        if not preflight_body.get("ready", False):
            # The definition stays so the user can fix the environment and
            # start it from the task page once preflight passes.
            return JSONResponse(
                {
                    "error": "preflight_blocked",
                    "message": "任务已创建，但执行前检查未通过",
                    "preflight": preflight_body,
                    "task": result.task.model_dump(mode="json"),
                },
                status_code=409,
            )
        try:
            execution = await self._core.execute_product_task(
                principal_id,
                result.task.task_id,
                launch_reason="created",
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "task": result.task.model_dump(mode="json"),
                "execution": execution.model_dump(mode="json"),
            },
            status_code=201,
        )

    async def _list_tasks(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = ProductTaskListQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            tasks = await self._core.list_product_tasks(
                authenticated.device.principal_id,
                state=query.state,
                include_archived=query.include_archived,
                limit=query.limit,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "tasks": [task.model_dump(mode="json") for task in tasks],
            }
        )

    async def _get_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            task = await self._core.get_product_task(
                authenticated.device.principal_id,
                task_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"task": task.model_dump(mode="json")})

    async def _preflight_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return await self._preflight_task_response(
            authenticated.device.principal_id,
            task_id,
        )

    async def _preflight_task_response(
        self,
        principal_id: str,
        task_id: str,
    ) -> JSONResponse:
        try:
            task = await self._core.get_product_task(
                principal_id,
                task_id,
            )
        except Exception as exc:
            return self._core_error(exc)

        checks: list[dict[str, str]] = []
        if task.state is TaskDefinitionState.ACTIVE:
            checks.append({
                "check_id": "task_state",
                "status": "ready",
                "detail": "任务可以执行",
                "recommended_action": "none",
            })
        elif task.state is TaskDefinitionState.PAUSED:
            checks.append({
                "check_id": "task_state",
                "status": "blocked",
                "detail": "任务当前未启用，请先恢复任务",
                "recommended_action": "resume",
            })
        else:
            checks.append({
                "check_id": "task_state",
                "status": "blocked",
                "detail": "任务已归档，请先恢复任务",
                "recommended_action": "resume",
            })

        if task.goal.strip():
            checks.append({
                "check_id": "goal",
                "status": "ready",
                "detail": "执行目标已设置",
                "recommended_action": "none",
            })
        else:
            checks.append({
                "check_id": "goal",
                "status": "blocked",
                "detail": "执行目标为空，请先编辑任务",
                "recommended_action": "configure",
            })

        try:
            revision, control, _generations = await self._core.get_config_current(
                principal_id,
            )
            agent = revision.document.agents.agents.get(task.agent_id)
            if agent is None:
                checks.append({
                    "check_id": "agent_config",
                    "status": "blocked",
                    "detail": "任务使用的 Agent 不存在，请重新选择 Agent",
                    "recommended_action": "configure",
                })
            elif not agent.enabled:
                checks.append({
                    "check_id": "agent_config",
                    "status": "blocked",
                    "detail": "任务使用的 Agent 已停用，请重新选择 Agent",
                    "recommended_action": "configure",
                })
            else:
                checks.append({
                    "check_id": "agent_config",
                    "status": "ready",
                    "detail": "任务使用的 Agent 已配置",
                    "recommended_action": "none",
                })
                if agent.kind == "codex":
                    executable = agent.command[0] if agent.command else ""
                    if executable and shutil.which(executable):
                        checks.append({
                            "check_id": "runtime_binary",
                            "status": "ready",
                            "detail": "Codex Runtime 命令可用",
                            "recommended_action": "none",
                        })
                    else:
                        checks.append({
                            "check_id": "runtime_binary",
                            "status": "blocked",
                            "detail": "找不到 Codex Runtime 命令，请在 Node 上安装或修正 Agent 配置",
                            "recommended_action": "configure",
                        })
                    paths = RuntimePaths.from_root(self._config.runtime_root)
                    workspace = (
                        paths.resolve(agent.cwd, default_parent=paths.root)
                        if agent.cwd
                        else paths.resolve(
                            f"agents/{task.agent_id}/workspace",
                            default_parent=paths.root,
                        )
                    )
                    if workspace.is_dir():
                        checks.append({
                            "check_id": "workspace",
                            "status": "ready",
                            "detail": "Codex 工作目录可用",
                            "recommended_action": "none",
                        })
                    elif workspace.parent.is_dir() and os.access(workspace.parent, os.W_OK):
                        checks.append({
                            "check_id": "workspace",
                            "status": "warning",
                            "detail": "Codex 工作目录尚未创建，执行时会自动创建",
                            "recommended_action": "none",
                        })
                    else:
                        checks.append({
                            "check_id": "workspace",
                            "status": "blocked",
                            "detail": "Codex 工作目录不可用，请检查路径和权限",
                            "recommended_action": "configure",
                        })
                binding = agent.model_binding
                if binding.ownership == "platform":
                    model = revision.document.models.get(binding.model)
                    provider = (
                        None
                        if model is None
                        else revision.document.providers.get(model.provider)
                    )
                    if model is None or provider is None:
                        checks.append({
                            "check_id": "model",
                            "status": "blocked",
                            "detail": "任务使用的模型配置不完整，请在 Console 检查模型和 Provider",
                            "recommended_action": "configure",
                        })
                    else:
                        checks.append({
                            "check_id": "model",
                            "status": "ready",
                            "detail": f"模型已配置：{binding.model}",
                            "recommended_action": "none",
                        })
                        if provider.requires_api_key is True:
                            credential_ready = False
                            if provider.api_key_ref:
                                try:
                                    secret_status = await asyncio.to_thread(
                                        self._provider_secrets.status,
                                        provider.api_key_ref,
                                    )
                                    credential_ready = bool(secret_status.get("configured"))
                                except Exception:
                                    credential_ready = False
                            elif provider.api_key_env:
                                credential_ready = bool(os.environ.get(provider.api_key_env))
                            if not credential_ready:
                                checks.append({
                                    "check_id": "credentials",
                                    "status": "blocked",
                                    "detail": "模型凭据未配置，请在 Console 设置 API Key",
                                    "recommended_action": "configure",
                                })
                            else:
                                checks.append({
                                    "check_id": "credentials",
                                    "status": "ready",
                                    "detail": "模型凭据已配置",
                                    "recommended_action": "none",
                                })
            if control.apply_status == "failed":
                checks.append({
                    "check_id": "config",
                    "status": "blocked",
                    "detail": "Node 配置应用失败，请在 Console 修复配置后重试",
                    "recommended_action": "configure",
                })
            elif control.apply_status == "applying":
                checks.append({
                    "check_id": "config",
                    "status": "warning",
                    "detail": "Node 配置正在应用，执行可能使用上一版本配置",
                    "recommended_action": "retry",
                })
            else:
                checks.append({
                    "check_id": "config",
                    "status": "ready",
                    "detail": "Node 配置已应用",
                    "recommended_action": "none",
                })
        except Exception:
            checks.append({
                "check_id": "config",
                "status": "blocked",
                "detail": "无法读取 Node 配置，请检查 Node 状态后重试",
                "recommended_action": "retry",
            })

        runtime_ready = False
        runtime_detail = "Agent Runtime 当前不可用，请检查 Node 状态后重试"
        try:
            runtime = await self._core.status(
                principal_id,
                task.session_handle,
            )
            runtime_ready = bool(runtime.connected)
            if runtime_ready:
                runtime_detail = "Agent Runtime 可用"
        except Exception:
            runtime_ready = False
        checks.append({
            "check_id": "runtime",
            "status": "ready" if runtime_ready else "blocked",
            "detail": runtime_detail,
            "recommended_action": "none" if runtime_ready else "retry",
        })
        if task.tools_enabled and runtime_ready:
            try:
                tools = await self._core.list_tools(
                    principal_id,
                    task.session_handle,
                )
                if tools.tools:
                    checks.append({
                        "check_id": "tools",
                        "status": "ready",
                        "detail": f"已发现 {len(tools.tools)} 项可用工具能力",
                        "recommended_action": "none",
                    })
                else:
                    checks.append({
                        "check_id": "tools",
                        "status": "warning",
                        "detail": "当前没有可用工具，任务仍可执行但可能无法完成外部操作",
                        "recommended_action": "configure",
                    })
            except Exception:
                checks.append({
                    "check_id": "tools",
                    "status": "blocked",
                    "detail": "无法读取工具能力，请检查 Agent Runtime 后重试",
                    "recommended_action": "retry",
                })
        return JSONResponse({
            "task_id": task.task_id,
            "ready": not any(item["status"] == "blocked" for item in checks),
            "checks": checks,
        })

    async def _update_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, UpdateProductTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        changes = parsed.model_dump(exclude_none=True)
        try:
            task = await self._core.update_product_task(
                authenticated.device.principal_id,
                task_id,
                **changes,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"task": task.model_dump(mode="json")})

    async def _delete_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=15)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            await self._core.delete_product_task(
                authenticated.device.principal_id,
                task_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"deleted": True})

    async def _set_task_definition_state(
        self,
        request: Request,
        state: TaskDefinitionState,
    ) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            task = await self._core.set_product_task_state(
                authenticated.device.principal_id,
                task_id,
                state,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"task": task.model_dump(mode="json")})

    async def _pause_task_definition(self, request: Request) -> JSONResponse:
        return await self._set_task_definition_state(request, TaskDefinitionState.PAUSED)

    async def _resume_task_definition(self, request: Request) -> JSONResponse:
        return await self._set_task_definition_state(request, TaskDefinitionState.ACTIVE)

    async def _archive_task(self, request: Request) -> JSONResponse:
        return await self._set_task_definition_state(request, TaskDefinitionState.ARCHIVED)

    async def _restore_task(self, request: Request) -> JSONResponse:
        return await self._set_task_definition_state(request, TaskDefinitionState.ACTIVE)

    async def _execute_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        preflight = await self._preflight_task(request)
        if preflight.status_code != 200:
            return preflight
        preflight_body = json.loads(preflight.body)
        if not preflight_body.get("ready", False):
            return JSONResponse(
                {
                    "error": "preflight_blocked",
                    "message": "任务尚未满足执行条件",
                    "preflight": preflight_body,
                },
                status_code=409,
            )
        try:
            execution = await self._core.execute_product_task(
                authenticated.device.principal_id,
                task_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"execution": execution.model_dump(mode="json")},
            status_code=202,
        )

    async def _continue_task(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, ContinueProductTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            parsed.require_content()
            execution = await self._core.continue_product_task(
                authenticated.device.principal_id,
                task_id,
                parsed.input,
                parsed.attachments,
                client_request_id=parsed.client_request_id,
            )
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"execution": execution.model_dump(mode="json")},
            status_code=202,
        )

    async def _list_task_executions(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        task_id = self._path_identifier(request, "task_id")
        if task_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = TaskExecutionListQuery.model_validate(dict(request.query_params))
            executions = await self._core.list_product_task_executions(
                authenticated.device.principal_id,
                task_id,
                limit=query.limit,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"executions": [item.model_dump(mode="json") for item in executions]}
        )

    async def _get_task_execution(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        execution_id = self._path_identifier(request, "execution_id")
        if execution_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            execution = await self._core.get_product_task_execution(
                authenticated.device.principal_id,
                execution_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"execution": execution.model_dump(mode="json")})

    async def _delete_task_execution(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=15)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        execution_id = self._path_identifier(request, "execution_id")
        if execution_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            await self._core.delete_product_task_execution(
                authenticated.device.principal_id,
                execution_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"deleted": True})

    async def _cancel_task_execution(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        execution_id = self._path_identifier(request, "execution_id")
        if execution_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, CancelTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.cancel_task(
                authenticated.device.principal_id,
                execution_id,
                reason=parsed.reason,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(result.result.model_dump(mode="json"))

    async def _pause_task_execution(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        execution_id = self._path_identifier(request, "execution_id")
        if execution_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, PauseTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.pause_task(
                authenticated.device.principal_id,
                execution_id,
                reason=parsed.reason,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(result.result.model_dump(mode="json"))

    async def _resume_task_execution(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        execution_id = self._path_identifier(request, "execution_id")
        if execution_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, ResumeTaskRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.resume_task(
                authenticated.device.principal_id,
                execution_id,
                reason=parsed.reason,
                acknowledge_outcome_unknown=parsed.acknowledge_outcome_unknown,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"accepted": True, "state": result.state.value})

    async def _rerun_task_execution(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        execution_id = self._path_identifier(request, "execution_id")
        if execution_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            execution = await self._core.rerun_product_task_execution(
                authenticated.device.principal_id,
                execution_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"execution": execution.model_dump(mode="json")},
            status_code=202,
        )

    async def _resolve_approval(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        approval_id = self._path_identifier(request, "approval_id")
        if approval_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, ResolveApprovalRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.resolve_approval(
                authenticated.device.principal_id,
                approval_id,
                approved=parsed.approved,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "approval_id": result.approval_id,
                "resolved": result.resolved,
                "state": result.state.value,
            }
        )
