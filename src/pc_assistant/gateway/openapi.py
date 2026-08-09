"""Generated OpenAPI 3.1 contract for the Secure Gateway mobile surface."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from pc_assistant.gateway.protocol import (
    ApprovalResolvedResponse,
    ArtifactTranscriptionResponse,
    ArtifactResponse,
    AuthChallengeRequest,
    AuthCompleteRequest,
    AuthCompleteResponse,
    CancelTaskRequest,
    ChallengeResponse,
    CreateTaskRequest,
    ErrorResponse,
    HealthResponse,
    PairChallengeRequest,
    PairCompleteRequest,
    PairCompleteResponse,
    PauseTaskRequest,
    ResolveApprovalRequest,
    ResumeTaskRequest,
    RetryTaskRequest,
    RuntimeStatusResponse,
    SessionCreatedResponse,
    SessionResponse,
    TaskAcceptedResponse,
    TaskCommandResponse,
    TaskListResponse,
    TaskResponse,
    ToolListResponse,
)


_MODELS: tuple[type[BaseModel], ...] = (
    ErrorResponse,
    HealthResponse,
    PairChallengeRequest,
    PairCompleteRequest,
    AuthChallengeRequest,
    AuthCompleteRequest,
    ChallengeResponse,
    PairCompleteResponse,
    AuthCompleteResponse,
    SessionResponse,
    SessionCreatedResponse,
    CreateTaskRequest,
    TaskAcceptedResponse,
    TaskResponse,
    TaskListResponse,
    CancelTaskRequest,
    PauseTaskRequest,
    ResumeTaskRequest,
    RetryTaskRequest,
    TaskCommandResponse,
    ResolveApprovalRequest,
    ApprovalResolvedResponse,
    ArtifactResponse,
    ArtifactTranscriptionResponse,
    RuntimeStatusResponse,
    ToolListResponse,
)


def _ref(model: type[BaseModel]) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{model.__name__}"}


def _json_body(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "required": True,
        "content": {"application/json": {"schema": _ref(model)}},
    }


def _json_response(
    description: str,
    model: type[BaseModel],
) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": _ref(model)}},
    }


def _errors(*statuses: str) -> dict[str, Any]:
    return {
        status: _json_response("Request rejected", ErrorResponse)
        for status in statuses
    }


def _query(name: str, schema: dict[str, Any], *, required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": required,
        "schema": schema,
    }


@lru_cache(maxsize=1)
def gateway_openapi_schema() -> dict[str, Any]:
    """Build the one authoritative mobile API contract from Pydantic models."""
    _mapping, bundled = models_json_schema(
        [(model, "validation") for model in _MODELS],
        ref_template="#/components/schemas/{model}",
        title="Knoa Secure Gateway v1",
    )
    schemas = bundled.get("$defs", {})
    bearer = [{"gatewaySession": []}]
    session_query = _query(
        "session_handle",
        {"type": "string", "minLength": 1, "maxLength": 256},
        required=True,
    )
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Knoa Secure Gateway",
            "version": "1.0.0",
            "description": (
                "Allow-listed mobile protocol. Core methods and internal objects "
                "are never exposed directly."
            ),
        },
        "paths": {
            "/health": {
                "get": {
                    "operationId": "gatewayHealth",
                    "responses": {"200": _json_response("Healthy", HealthResponse)},
                }
            },
            "/v1/pair/challenge": {
                "post": {
                    "operationId": "beginPairing",
                    "requestBody": _json_body(PairChallengeRequest),
                    "responses": {
                        "200": _json_response("Pairing challenge", ChallengeResponse),
                        **_errors("400", "401", "415", "429"),
                    },
                }
            },
            "/v1/pair/complete": {
                "post": {
                    "operationId": "completePairing",
                    "requestBody": _json_body(PairCompleteRequest),
                    "responses": {
                        "201": _json_response("Paired device", PairCompleteResponse),
                        **_errors("400", "401", "415", "429"),
                    },
                }
            },
            "/v1/auth/challenge": {
                "post": {
                    "operationId": "beginAuthentication",
                    "requestBody": _json_body(AuthChallengeRequest),
                    "responses": {
                        "200": _json_response("Authentication challenge", ChallengeResponse),
                        **_errors("400", "401", "415", "429"),
                    },
                }
            },
            "/v1/auth/complete": {
                "post": {
                    "operationId": "completeAuthentication",
                    "requestBody": _json_body(AuthCompleteRequest),
                    "responses": {
                        "200": _json_response("Gateway session", AuthCompleteResponse),
                        **_errors("400", "401", "415", "429"),
                    },
                }
            },
            "/v1/session": {
                "get": {
                    "operationId": "getGatewaySession",
                    "security": bearer,
                    "responses": {
                        "200": _json_response("Gateway session", SessionResponse),
                        **_errors("401", "429"),
                    },
                }
            },
            "/v1/sessions": {
                "post": {
                    "operationId": "createCoreSession",
                    "security": bearer,
                    "responses": {
                        "201": _json_response("Core session", SessionCreatedResponse),
                        **_errors("401", "429", "503"),
                    },
                }
            },
            "/v1/tasks": {
                "post": {
                    "operationId": "createTask",
                    "security": bearer,
                    "requestBody": _json_body(CreateTaskRequest),
                    "responses": {
                        "202": _json_response("Task accepted", TaskAcceptedResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                },
                "get": {
                    "operationId": "listTasks",
                    "security": bearer,
                    "parameters": [
                        _query("session_handle", {"type": "string", "maxLength": 256}),
                        _query(
                            "state",
                            {
                                "type": "string",
                                "enum": [
                                    "queued",
                                    "running",
                                    "waiting_approval",
                                    "paused",
                                    "completed",
                                    "failed",
                                    "cancelled",
                                ],
                            },
                        ),
                        _query(
                            "limit",
                            {"type": "integer", "minimum": 1, "maximum": 100},
                        ),
                        _query("cursor", {"type": "string", "maxLength": 512}),
                    ],
                    "responses": {
                        "200": _json_response("Owned Tasks", TaskListResponse),
                        **_errors("400", "401", "429", "503"),
                    },
                },
            },
            "/v1/tasks/{task_id}": {
                "get": {
                    "operationId": "getTask",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        }
                    ],
                    "responses": {
                        "200": _json_response("Owned Task", TaskResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/cancel": {
                "post": {
                    "operationId": "cancelTask",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        }
                    ],
                    "requestBody": _json_body(CancelTaskRequest),
                    "responses": {
                        "200": {
                            "description": "Cancellation result",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["accepted"],
                                        "properties": {
                                            "accepted": {"type": "boolean"},
                                            "state": {"type": ["string", "null"]},
                                        },
                                    }
                                }
                            },
                        },
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/pause": {
                "post": {
                    "operationId": "pauseTask",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        }
                    ],
                    "requestBody": _json_body(PauseTaskRequest),
                    "responses": {
                        "200": _json_response("Pause result", TaskCommandResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/resume": {
                "post": {
                    "operationId": "resumeTask",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        }
                    ],
                    "requestBody": _json_body(ResumeTaskRequest),
                    "responses": {
                        "200": _json_response("Resume result", TaskCommandResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/retry": {
                "post": {
                    "operationId": "retryTask",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        }
                    ],
                    "requestBody": _json_body(RetryTaskRequest),
                    "responses": {
                        "202": _json_response("Retry accepted", TaskAcceptedResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/approvals/{approval_id}/resolve": {
                "post": {
                    "operationId": "resolveApproval",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "approval_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        }
                    ],
                    "requestBody": _json_body(ResolveApprovalRequest),
                    "responses": {
                        "200": _json_response("Approval resolved", ApprovalResolvedResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/events": {
                "get": {
                    "operationId": "streamTaskEvents",
                    "security": bearer,
                    "parameters": [
                        _query(
                            "after_id",
                            {"type": "integer", "minimum": 0},
                        ),
                        {
                            "name": "Last-Event-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 0},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Resumable principal Task event stream",
                            "content": {
                                "text/event-stream": {"schema": {"type": "string"}}
                            },
                        },
                        **_errors("400", "401", "429"),
                    },
                }
            },
            "/v1/artifacts": {
                "post": {
                    "operationId": "uploadArtifact",
                    "security": bearer,
                    "parameters": [
                        session_query,
                        _query("name", {"type": "string", "maxLength": 160}),
                        _query("caption", {"type": "string", "maxLength": 1000}),
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/octet-stream": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    },
                    "responses": {
                        "201": _json_response("Stored Artifact", ArtifactResponse),
                        **_errors("400", "401", "413", "415", "429", "503"),
                    },
                }
            },
            "/v1/artifacts/{artifact_id}": {
                "get": {
                    "operationId": "downloadArtifact",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "artifact_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        },
                        session_query,
                    ],
                    "responses": {
                        "200": {
                            "description": "Artifact bytes",
                            "content": {
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        **_errors("400", "401", "404", "413", "429", "503"),
                    },
                }
            },
            "/v1/artifacts/{artifact_id}/transcribe": {
                "post": {
                    "operationId": "transcribeArtifact",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "artifact_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        },
                        session_query,
                    ],
                    "responses": {
                        "200": _json_response(
                            "Artifact transcription",
                            ArtifactTranscriptionResponse,
                        ),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/runtime/status": {
                "get": {
                    "operationId": "getRuntimeStatus",
                    "security": bearer,
                    "parameters": [session_query],
                    "responses": {
                        "200": _json_response("Runtime status", RuntimeStatusResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                }
            },
            "/v1/tools": {
                "get": {
                    "operationId": "listTools",
                    "security": bearer,
                    "parameters": [session_query],
                    "responses": {
                        "200": _json_response("Tool inventory", ToolListResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "gatewaySession": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "opaque",
                }
            },
            "schemas": schemas,
        },
    }
