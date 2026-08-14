"""Generated OpenAPI 3.1 contract for the Secure Gateway mobile surface."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from knoa_platform.gateway.protocol import (
    AndroidReleaseResponse,
    AgentListResponse,
    ApprovalResolvedResponse,
    ArtifactResponse,
    ArtifactTranscriptionResponse,
    AuditEventResponse,
    AuditListResponse,
    AuthChallengeRequest,
    AuthCompleteRequest,
    AuthCompleteResponse,
    CancelTaskRequest,
    ChallengeResponse,
    ChatApprovalResolvedResponse,
    ChatTurnListResponse,
    ChatTurnResponse,
    ConversationSessionListResponse,
    ConversationSessionResponse,
    ContinueProductTaskRequest,
    CreateChatTurnRequest,
    CreateProductTaskRequest,
    DeletedResponse,
    DeviceRevokedResponse,
    ErrorResponse,
    HealthResponse,
    HumanInteractionResolvedResponse,
    PairChallengeRequest,
    PairCompleteRequest,
    PairCompleteResponse,
    PauseTaskRequest,
    ProductTaskExecutionListResponse,
    ProductTaskExecutionResponse,
    ProductTaskListResponse,
    ProductTaskResponse,
    ResolveApprovalRequest,
    ResolveHumanInteractionRequest,
    ResumeTaskRequest,
    RuntimeStatusResponse,
    SessionCreatedResponse,
    SessionResponse,
    TaskEventListResponse,
    ToolListResponse,
    MCPResourceCatalogResponse,
    UpdateConversationSessionRequest,
    UpdateProductTaskRequest,
)

_MODELS: tuple[type[BaseModel], ...] = (
    AndroidReleaseResponse,
    AgentListResponse,
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
    CreateProductTaskRequest,
    UpdateProductTaskRequest,
    ContinueProductTaskRequest,
    CreateChatTurnRequest,
    UpdateConversationSessionRequest,
    ConversationSessionResponse,
    ConversationSessionListResponse,
    ChatTurnResponse,
    ChatTurnListResponse,
    ChatApprovalResolvedResponse,
    HumanInteractionResolvedResponse,
    ProductTaskResponse,
    ProductTaskListResponse,
    ProductTaskExecutionResponse,
    ProductTaskExecutionListResponse,
    DeletedResponse,
    DeviceRevokedResponse,
    CancelTaskRequest,
    PauseTaskRequest,
    ResumeTaskRequest,
    TaskEventListResponse,
    ResolveApprovalRequest,
    ResolveHumanInteractionRequest,
    ApprovalResolvedResponse,
    AuditEventResponse,
    AuditListResponse,
    ArtifactResponse,
    ArtifactTranscriptionResponse,
    RuntimeStatusResponse,
    ToolListResponse,
    MCPResourceCatalogResponse,
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
            "/v1/agents": {
                "get": {
                    "operationId": "listAgents",
                    "security": bearer,
                    "responses": {
                        "200": _json_response("Enabled Agents", AgentListResponse),
                        **_errors("401", "429"),
                    },
                }
            },
            "/v1/mcp/resources": {
                "get": {
                    "operationId": "listMcpResources",
                    "security": bearer,
                    "responses": {
                        "200": _json_response("MCP Resource catalog", MCPResourceCatalogResponse),
                        **_errors("401", "429", "500"),
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
            "/v1/conversations/sessions": {
                "get": {
                    "operationId": "listConversationSessions",
                    "security": bearer,
                    "parameters": [
                        _query("include_archived", {"type": "boolean"}),
                        _query("limit", {"type": "integer", "minimum": 1, "maximum": 200}),
                        _query("cursor", {"type": "string", "maxLength": 512}),
                    ],
                    "responses": {
                        "200": _json_response("Conversation sessions", ConversationSessionListResponse),
                        **_errors("400", "401", "429", "503"),
                    },
                }
            },
            "/v1/conversations/sessions/{session_handle}": {
                "get": {
                    "operationId": "getConversationSession",
                    "security": bearer,
                    "parameters": [{
                        "name": "session_handle", "in": "path", "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 256},
                    }],
                    "responses": {
                        "200": _json_response("Conversation session", ConversationSessionResponse),
                        **_errors("401", "404", "429", "503"),
                    },
                },
                "patch": {
                    "operationId": "updateConversationSession",
                    "security": bearer,
                    "parameters": [{
                        "name": "session_handle", "in": "path", "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 256},
                    }],
                    "requestBody": _json_body(UpdateConversationSessionRequest),
                    "responses": {
                        "200": _json_response("Updated conversation session", ConversationSessionResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                },
                "delete": {
                    "operationId": "deleteConversationSession",
                    "security": bearer,
                    "parameters": [{
                        "name": "session_handle", "in": "path", "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 256},
                    }],
                    "responses": {
                        "200": _json_response("Deleted conversation session", DeletedResponse),
                        **_errors("401", "404", "422", "429", "503"),
                    },
                },
            },
            "/v1/conversations/sessions/{session_handle}/turns": {
                "post": {
                    "operationId": "createChatTurn",
                    "security": bearer,
                    "parameters": [{
                        "name": "session_handle",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 256},
                    }],
                    "requestBody": _json_body(CreateChatTurnRequest),
                    "responses": {
                        "202": _json_response("ChatTurn accepted", ChatTurnResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                },
                "get": {
                    "operationId": "listChatTurns",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "session_handle",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1, "maxLength": 256},
                        },
                        _query("limit", {"type": "integer", "minimum": 1, "maximum": 500}),
                        _query("cursor", {"type": "string", "maxLength": 512}),
                    ],
                    "responses": {
                        "200": _json_response("Conversation history", ChatTurnListResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                },
            },
            "/v1/conversations/turns/{turn_id}": {
                "get": {
                    "operationId": "getChatTurn",
                    "security": bearer,
                    "parameters": [{
                        "name": "turn_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    }],
                    "responses": {
                        "200": _json_response("ChatTurn snapshot", ChatTurnResponse),
                        **_errors("401", "404", "429", "503"),
                    },
                }
            },
            "/v1/conversations/turns/{turn_id}/stream": {
                "get": {
                    "operationId": "streamChatTurn",
                    "security": bearer,
                    "parameters": [{
                        "name": "turn_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    }],
                    "responses": {
                        "200": {
                            "description": "Coalesced ChatTurn snapshots",
                            "content": {"text/event-stream": {"schema": {"type": "string"}}},
                        },
                        **_errors("401", "404", "429", "503"),
                    },
                }
            },
            "/v1/conversations/turns/{turn_id}/cancel": {
                "post": {
                    "operationId": "cancelChatTurn",
                    "security": bearer,
                    "parameters": [{
                        "name": "turn_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    }],
                    "responses": {
                        "200": _json_response("ChatTurn cancellation", ChatTurnResponse),
                        **_errors("401", "404", "429", "503"),
                    },
                }
            },
            "/v1/conversations/turns/{turn_id}/retry": {
                "post": {
                    "operationId": "retryChatTurn",
                    "security": bearer,
                    "parameters": [{
                        "name": "turn_id", "in": "path", "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    }],
                    "responses": {
                        "202": _json_response("Retried ChatTurn", ChatTurnResponse),
                        **_errors("401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/conversations/approvals/{approval_id}/resolve": {
                "post": {
                    "operationId": "resolveChatApproval",
                    "security": bearer,
                    "parameters": [{
                        "name": "approval_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    }],
                    "requestBody": _json_body(ResolveApprovalRequest),
                    "responses": {
                        "200": _json_response("Chat approval resolved", ChatApprovalResolvedResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                }
            },
            "/v1/interactions/{interaction_id}/resolve": {
                "post": {
                    "operationId": "resolveInteraction",
                    "security": bearer,
                    "parameters": [{
                        "name": "interaction_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    }],
                    "requestBody": _json_body(ResolveHumanInteractionRequest),
                    "responses": {
                        "200": _json_response(
                            "Human interaction resolved",
                            HumanInteractionResolvedResponse,
                        ),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks": {
                "post": {
                    "operationId": "createTask",
                    "security": bearer,
                    "requestBody": _json_body(CreateProductTaskRequest),
                    "responses": {
                        "201": _json_response("Task created", ProductTaskResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                },
                "get": {
                    "operationId": "listTasks",
                    "security": bearer,
                    "parameters": [
                        _query(
                            "state",
                            {
                                "type": "string",
                                "enum": ["active", "paused", "archived"],
                            },
                        ),
                        _query("include_archived", {"type": "boolean"}),
                        _query(
                            "limit",
                            {"type": "integer", "minimum": 1, "maximum": 200},
                        ),
                    ],
                    "responses": {
                        "200": _json_response("Owned Tasks", ProductTaskListResponse),
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
                        "200": _json_response("Owned Task", ProductTaskResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                },
                "patch": {
                    "operationId": "updateTask",
                    "security": bearer,
                    "parameters": [{
                        "name": "task_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "requestBody": _json_body(UpdateProductTaskRequest),
                    "responses": {
                        "200": _json_response("Updated Task", ProductTaskResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                },
                "delete": {
                    "operationId": "deleteTask",
                    "security": bearer,
                    "parameters": [{
                        "name": "task_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "responses": {
                        "200": _json_response("Task deleted", DeletedResponse),
                        **_errors("400", "401", "404", "409", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/execute": {
                "post": {
                    "operationId": "executeTask",
                    "security": bearer,
                    "parameters": [{
                        "name": "task_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "responses": {
                        "202": _json_response("Execution accepted", ProductTaskExecutionResponse),
                        **_errors("400", "401", "404", "409", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/continue": {
                "post": {
                    "operationId": "continueTask",
                    "security": bearer,
                    "parameters": [{
                        "name": "task_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "requestBody": _json_body(ContinueProductTaskRequest),
                    "responses": {
                        "202": _json_response("Follow-up execution accepted", ProductTaskExecutionResponse),
                        **_errors("400", "401", "404", "409", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/executions": {
                "get": {
                    "operationId": "listTaskExecutions",
                    "security": bearer,
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "maxLength": 128},
                        },
                        _query("limit", {"type": "integer", "minimum": 1, "maximum": 200}),
                    ],
                    "responses": {
                        "200": _json_response("Task executions", ProductTaskExecutionListResponse),
                        **_errors("400", "401", "404", "429", "503"),
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
                    "responses": {
                        "200": _json_response("Paused Task", ProductTaskResponse),
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
                    "responses": {
                        "200": _json_response("Active Task", ProductTaskResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/archive": {
                "post": {
                    "operationId": "archiveTask",
                    "security": bearer,
                    "parameters": [{
                        "name": "task_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "responses": {
                        "200": _json_response("Archived Task", ProductTaskResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/tasks/{task_id}/restore": {
                "post": {
                    "operationId": "restoreTask",
                    "security": bearer,
                    "parameters": [{
                        "name": "task_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "responses": {
                        "200": _json_response("Restored Task", ProductTaskResponse),
                        **_errors("400", "401", "404", "422", "429", "503"),
                    },
                }
            },
            "/v1/task-executions/{execution_id}": {
                "get": {
                    "operationId": "getTaskExecution",
                    "security": bearer,
                    "parameters": [{
                        "name": "execution_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "responses": {
                        "200": _json_response("Task execution", ProductTaskExecutionResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                },
                "delete": {
                    "operationId": "deleteTaskExecution",
                    "security": bearer,
                    "parameters": [{
                        "name": "execution_id", "in": "path", "required": True,
                        "schema": {"type": "string", "maxLength": 128},
                    }],
                    "responses": {
                        "200": _json_response("Task execution deleted", DeletedResponse),
                        **_errors("400", "401", "404", "409", "429", "503"),
                    },
                },
            },
            "/v1/task-executions/{execution_id}/events": {
                "get": {
                    "operationId": "listTaskExecutionEvents",
                    "security": bearer,
                    "parameters": [
                        {"name": "execution_id", "in": "path", "required": True,
                         "schema": {"type": "string", "maxLength": 128}},
                        _query("after_seq", {"type": "integer", "minimum": 0}),
                    ],
                    "responses": {
                        "200": _json_response("Execution event timeline", TaskEventListResponse),
                        **_errors("400", "401", "404", "429", "503"),
                    },
                }
            },
            "/v1/task-executions/{execution_id}/cancel": {
                "post": {
                    "operationId": "cancelTaskExecution", "security": bearer,
                    "parameters": [{"name": "execution_id", "in": "path", "required": True,
                                    "schema": {"type": "string", "maxLength": 128}}],
                    "requestBody": _json_body(CancelTaskRequest),
                    "responses": {"200": {"description": "Cancellation result"},
                                  **_errors("400", "401", "404", "422", "429", "503")},
                }
            },
            "/v1/task-executions/{execution_id}/pause": {
                "post": {
                    "operationId": "pauseTaskExecution", "security": bearer,
                    "parameters": [{"name": "execution_id", "in": "path", "required": True,
                                    "schema": {"type": "string", "maxLength": 128}}],
                    "requestBody": _json_body(PauseTaskRequest),
                    "responses": {"200": {"description": "Pause result"},
                                  **_errors("400", "401", "404", "422", "429", "503")},
                }
            },
            "/v1/task-executions/{execution_id}/resume": {
                "post": {
                    "operationId": "resumeTaskExecution", "security": bearer,
                    "parameters": [{"name": "execution_id", "in": "path", "required": True,
                                    "schema": {"type": "string", "maxLength": 128}}],
                    "requestBody": _json_body(ResumeTaskRequest),
                    "responses": {"200": {"description": "Resume result"},
                                  **_errors("400", "401", "404", "422", "429", "503")},
                }
            },
            "/v1/task-executions/{execution_id}/rerun": {
                "post": {
                    "operationId": "rerunTaskExecution", "security": bearer,
                    "parameters": [{"name": "execution_id", "in": "path", "required": True,
                                    "schema": {"type": "string", "maxLength": 128}}],
                    "responses": {
                        "202": _json_response("Rerun accepted", ProductTaskExecutionResponse),
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
            "/v1/mobile/releases/android/latest": {
                "get": {
                    "operationId": "getLatestAndroidRelease",
                    "security": bearer,
                    "responses": {
                        "200": _json_response(
                            "Latest personal Android release",
                            AndroidReleaseResponse,
                        ),
                        **_errors("401", "404", "429"),
                    },
                }
            },
            "/releases/android/{version_code}/{sha256}/knoa.apk": {
                "get": {
                    "operationId": "downloadAndroidRelease",
                    "parameters": [
                        {
                            "name": "version_code",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1},
                        },
                        {
                            "name": "sha256",
                            "in": "path",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "pattern": "^[0-9a-f]{64}$",
                            },
                        },
                        {
                            "name": "Range",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Complete Android APK",
                            "content": {
                                "application/vnd.android.package-archive": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        "206": {
                            "description": "Android APK byte range",
                            "content": {
                                "application/vnd.android.package-archive": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        **_errors("400", "404", "416"),
                    },
                }
            },
            "/v1/device/audit": {
                "get": {
                    "operationId": "listDeviceAudit",
                    "security": bearer,
                    "parameters": [
                        _query("after_id", {"type": "integer", "minimum": 0}),
                        _query(
                            "limit",
                            {"type": "integer", "minimum": 1, "maximum": 200},
                        ),
                    ],
                    "responses": {
                        "200": _json_response("Device audit events", AuditListResponse),
                        **_errors("400", "401", "429"),
                    },
                }
            },
            "/v1/device": {
                "delete": {
                    "operationId": "revokeCurrentDevice",
                    "security": bearer,
                    "responses": {
                        "200": _json_response(
                            "Current device revoked",
                            DeviceRevokedResponse,
                        ),
                        **_errors("401", "404", "429"),
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
