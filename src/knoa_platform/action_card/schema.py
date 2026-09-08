"""Formal JSON Schema definition and validator for universal Action Cards."""

from __future__ import annotations

import json
from typing import Any

ACTION_CARD_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "KnoaActionCard",
    "description": "Standardized Server-Driven UI schema for universal Knoa clients.",
    "type": "object",
    "required": ["schema_version", "card_id", "title", "level", "status", "source"],
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": ["1.0"],
            "description": "Action card specification version.",
        },
        "card_id": {
            "type": "string",
            "pattern": "^card_[0-9a-zA-Z_-]+$",
            "description": "Globally unique identifier for this card.",
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Header title of the card.",
        },
        "subtitle": {
            "type": ["string", "null"],
            "maxLength": 300,
            "description": "Optional secondary description under the title.",
        },
        "level": {
            "type": "string",
            "enum": ["info", "success", "warning", "critical"],
            "description": "Visual severity badge for the card.",
        },
        "status": {
            "type": "string",
            "enum": ["pending", "approved", "rejected", "expired", "executed"],
            "description": "Current lifecycle state of the card.",
        },
        "source": {
            "type": "object",
            "required": ["plugin_name", "created_at"],
            "properties": {
                "plugin_name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Name of the origin plugin or core system (e.g. 'jira', 'gitlab').",
                },
                "agent_name": {
                    "type": ["string", "null"],
                    "description": "Subagent or specialist that constructed this card (e.g. 'coder').",
                },
                "session_id": {
                    "type": ["string", "null"],
                    "description": "Associated runtime session or conversation turn ID.",
                },
                "created_at": {
                    "type": "number",
                    "description": "Epoch timestamp when the card was created.",
                },
                "expires_at": {
                    "type": ["number", "null"],
                    "description": "Epoch timestamp after which the card should be considered expired.",
                },
            },
            "additionalProperties": False,
        },
        "blocks": {
            "type": "array",
            "description": "Ordered content blocks rendered by universal client.",
            "items": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "markdown",
                            "key_value",
                            "code_diff",
                            "callout",
                            "artifact_link",
                        ],
                    },
                },
                "additionalProperties": True,
            },
        },
        "inputs": {
            "type": "array",
            "description": "Optional interactive form controls for human input.",
            "items": {
                "type": "object",
                "required": ["id", "label", "input_type"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "label": {"type": "string", "minLength": 1},
                    "input_type": {
                        "type": "string",
                        "enum": ["text", "textarea", "select", "switch"],
                    },
                    "placeholder": {"type": "string"},
                    "default_value": {},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["label", "value"],
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                    },
                    "required": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        "actions": {
            "type": "array",
            "description": "Action buttons that user can click to trigger core/MCP callbacks.",
            "items": {
                "type": "object",
                "required": ["id", "label", "style", "action_type"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "label": {"type": "string", "minLength": 1},
                    "style": {
                        "type": "string",
                        "enum": ["primary", "secondary", "danger", "outline"],
                    },
                    "action_type": {
                        "type": "string",
                        "enum": ["invoke_tool", "open_url", "dismiss", "custom"],
                    },
                    "tool_name": {"type": ["string", "null"]},
                    "arguments": {"type": "object"},
                    "confirm_dialog": {
                        "type": ["object", "null"],
                        "properties": {
                            "title": {"type": "string"},
                            "message": {"type": "string"},
                        },
                        "required": ["title", "message"],
                    },
                    "include_form_inputs": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        "metadata": {
            "type": "object",
            "description": "Extensible metadata dictionary for logging or routing.",
        },
    },
    "additionalProperties": False,
}


def validate_action_card(data: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate a card dictionary against ACTION_CARD_JSON_SCHEMA.

    Returns (True, None) if valid, or (False, error_message) if invalid.
    """
    try:
        import jsonschema

        jsonschema.validate(instance=data, schema=ACTION_CARD_JSON_SCHEMA)
        return True, None
    except Exception as exc:
        return False, str(exc)
