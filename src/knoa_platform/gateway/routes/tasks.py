"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import logging

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
from knoa_platform.tasks import TaskDefinitionState

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
        try:
            session_handle = await self._core.create_session(
                authenticated.device.principal_id,
                activate=False,
                agent_id=parsed.agent_id,
            )
            result = await self._core.create_product_task(
                authenticated.device.principal_id,
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
            )
        except Exception as exc:
            return self._core_error(exc)
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
