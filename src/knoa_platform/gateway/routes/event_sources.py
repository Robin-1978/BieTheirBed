"""Friendly Event Source facade over Product Tasks, Triggers and Hosted ingress."""
from __future__ import annotations

import secrets
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import (
    CreateEventSourceRequest,
    SetEventSourceStateRequest,
    TestEventSourceRequest,
)
from knoa_platform.tasks import TaskDefinitionState, TaskLaunchKind, TaskLaunchPolicy


class EventSourceRoutes:
    async def _event_trigger(self, principal_id: str, session_handle: str):
        triggers = await self._core.list_triggers(principal_id, limit=100)
        for trigger in triggers:
            if trigger.session_handle == session_handle:
                return trigger
        raise LookupError("Event source Trigger is missing")

    @staticmethod
    def _event_source_payload(task, trigger, projection=None) -> dict[str, Any]:
        kind = "mcp_resource" if task.launch_policy.event_source.startswith("mcp:") else "webhook"
        return {
            "source_id": task.task_id,
            "kind": kind,
            "display_name": task.title,
            "task_id": task.task_id,
            "state": task.state.value,
            "health": "healthy" if trigger is not None else "missing",
            "last_event_at": None if trigger is None else trigger.last_event_at,
            "event_count": 0 if trigger is None else trigger.event_count,
            "public_url": "" if projection is None else projection.public_url,
            "route_id": "" if projection is None else projection.route_id,
            "secret_version": 0 if projection is None else projection.secret_version,
            "source_config": task.launch_policy.source_config,
        }

    async def _list_event_sources(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        principal = authenticated.device.principal_id
        try:
            tasks = await self._core.list_product_tasks(
                principal, include_archived=False, limit=200
            )
            triggers = await self._core.list_triggers(principal, limit=100)
            by_session = {item.session_handle: item for item in triggers}
            projections = {item.task_id: item for item in self._event_sources.list(principal, limit=200)}
        except Exception as exc:
            return self._core_error(exc)
        sources = [
            self._event_source_payload(task, by_session.get(task.session_handle), projections.get(task.task_id))
            for task in tasks
            if task.launch_policy.kind is TaskLaunchKind.EVENT
        ]
        return JSONResponse({"event_sources": sources})

    async def _create_event_source(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse_body(request, CreateEventSourceRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        principal = authenticated.device.principal_id
        if parsed.kind == "mcp_resource":
            if not parsed.mcp_server_id or not parsed.resource_uri_prefix:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            event_source = f"mcp:{parsed.mcp_server_id}"
            source_config = {
                "resource_uri_prefix": parsed.resource_uri_prefix,
                "include_root": parsed.include_root,
                "include_descendants": parsed.include_descendants,
            }
        else:
            event_source = "webhook"
            source_config = {}
        task = None
        try:
            session = await self._core.create_session(principal, activate=False, agent_id=parsed.agent_id)
            result = await self._core.create_product_task(
                principal,
                session,
                parsed.goal,
                client_request_id=parsed.client_request_id,
                title=parsed.title,
                tools_enabled=parsed.tools_enabled,
                priority=parsed.priority,
                launch_policy=TaskLaunchPolicy(
                    kind=TaskLaunchKind.EVENT,
                    event_source=event_source,
                    source_config=source_config,
                ),
                notification_policy=parsed.notification_policy or None,
                agent_id=parsed.agent_id,
            )
            task = result.task
            trigger = await self._event_trigger(principal, session)
            route: dict[str, Any] = {}
            if parsed.kind == "webhook":
                route = await self._node_hub.provision_webhook_route(
                    principal_id=principal,
                    task_id=task.task_id,
                    trigger_id=trigger.trigger_id,
                    display_name=task.title,
                )
            projection = self._event_sources.put(
                principal, task.task_id, trigger.trigger_id,
                kind=parsed.kind,
                route_id=str(route.get("route_id", "")),
                public_url=str(route.get("public_url", "")),
                secret_version=int(route.get("secret_version", 0)),
            )
        except (ValidationError, ValueError):
            if task is not None:
                try:
                    await self._core.delete_product_task(principal, task.task_id)
                except Exception:
                    pass
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            if task is not None:
                try:
                    await self._core.delete_product_task(principal, task.task_id)
                except Exception:
                    pass
            return self._core_error(exc)
        body = self._event_source_payload(task, trigger, projection)
        if route.get("secret"):
            body["secret"] = route["secret"]
            body["signing_example"] = route.get("signing_example", {})
        return JSONResponse({"event_source": body}, status_code=201)

    async def _get_event_source(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        source_id = self._path_identifier(request, "source_id")
        if source_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        principal = authenticated.device.principal_id
        try:
            task = await self._core.get_product_task(principal, source_id)
            if task.launch_policy.kind is not TaskLaunchKind.EVENT:
                raise LookupError
            trigger = await self._event_trigger(principal, task.session_handle)
            try:
                projection = self._event_sources.get(principal, source_id)
            except LookupError:
                projection = None
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"event_source": self._event_source_payload(task, trigger, projection)})

    async def _set_event_source_state(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        source_id = self._path_identifier(request, "source_id")
        parsed = await self._parse_body(request, SetEventSourceStateRequest)
        if source_id is None or isinstance(parsed, JSONResponse):
            return parsed if isinstance(parsed, JSONResponse) else JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            task = await self._core.set_product_task_state(
                authenticated.device.principal_id,
                source_id,
                TaskDefinitionState.ACTIVE if parsed.state == "active" else TaskDefinitionState.PAUSED,
            )
            trigger = await self._event_trigger(authenticated.device.principal_id, task.session_handle)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"event_source": self._event_source_payload(task, trigger)})

    async def _test_event_source(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        source_id = self._path_identifier(request, "source_id")
        parsed = await self._parse_body(request, TestEventSourceRequest)
        if source_id is None or isinstance(parsed, JSONResponse):
            return parsed if isinstance(parsed, JSONResponse) else JSONResponse({"error": "invalid_request"}, status_code=400)
        principal = authenticated.device.principal_id
        try:
            task = await self._core.get_product_task(principal, source_id)
            trigger = await self._event_trigger(principal, task.session_handle)
            event = await self._core.fire_trigger(
                principal,
                trigger.trigger_id,
                parsed.external_event_id or f"test-{secrets.token_urlsafe(12)}",
                {"test": True, **parsed.payload},
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"event": event.model_dump(mode="json")}, status_code=202)

    async def _event_source_events(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        source_id = self._path_identifier(request, "source_id")
        if source_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            limit = min(200, max(1, int(request.query_params.get("limit", "50"))))
            task = await self._core.get_product_task(authenticated.device.principal_id, source_id)
            trigger = await self._event_trigger(authenticated.device.principal_id, task.session_handle)
            events = await self._core.list_trigger_events(
                authenticated.device.principal_id, trigger.trigger_id, limit=limit
            )
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"events": [item.model_dump(mode="json") for item in events]})

    async def _rotate_event_source_secret(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=10)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        source_id = self._path_identifier(request, "source_id")
        if source_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        principal = authenticated.device.principal_id
        try:
            projection = self._event_sources.get(principal, source_id)
            if projection.kind != "webhook" or not projection.route_id:
                return JSONResponse({"error": "unsupported"}, status_code=422)
            rotated = await self._node_hub.rotate_webhook_secret(projection.route_id)
            self._event_sources.update_secret_version(principal, source_id, int(rotated["secret_version"]))
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(rotated)

    async def _delete_event_source(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=15)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        source_id = self._path_identifier(request, "source_id")
        if source_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        principal = authenticated.device.principal_id
        try:
            try:
                projection = self._event_sources.get(principal, source_id)
            except LookupError:
                projection = None
            if projection is not None and projection.route_id:
                await self._node_hub.delete_webhook_route(projection.route_id)
            await self._core.delete_product_task(principal, source_id)
            self._event_sources.delete(principal, source_id)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"deleted": True})
