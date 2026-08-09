# Knoa Canonical Tool Definition

> Status: implemented
>
> Date: 2026-08-09

## Decision

Knoa uses the stable subset of the MCP Tool definition as its only internal
tool-description format:

```json
{
  "name": "read_file",
  "description": "Read text from a local file.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"}
    },
    "required": ["path"],
    "additionalProperties": false
  }
}
```

`outputSchema` is supported when a tool has a stable structured result. MCP
annotations are intentionally not part of the authority model.

## Runtime flow

```text
Built-in Tool.definition() ─┐
                            ├─ canonical MCP-compatible ToolDefinition
MCP discovery ──────────────┘
                                      │
                              ToolRegistry validation
                                      │
                          ReActContext / ModelStepRequest
                                      │
                 ┌────────────────────┴────────────────────┐
                 │                                         │
        OpenAI provider adapter                   Anthropic provider adapter
        inputSchema → parameters                  inputSchema → input_schema
```

Built-in tools do not become MCP processes. They implement the same definition
contract but retain direct in-Core execution. MCP tools retain remote/local MCP
transport execution. Both enter the same `ToolStep` authorization and commit
boundary.

## Invariants

1. `name` must match the registered tool name.
2. `inputSchema` is required, must be a JSON Schema object and must describe an
   object.
3. Missing `additionalProperties` is normalized to `false` before model
   injection and execution validation.
4. `outputSchema`, when present, must be valid JSON Schema.
5. Legacy OpenAI-shaped `parameters` definitions are rejected at registration.
6. Core transports only canonical definitions; provider-specific shapes exist
   only inside provider adapters.
7. Full and skim definitions use the same contract and tool name.
8. `effect`, `risk`, `capabilities` and confirmation remain separate local
   policy. MCP metadata cannot grant authority.

## Full and skim definitions

`definition()` is the complete description returned by `tool_help`.
`skim_definition()` may omit rarely used properties to reduce model context,
but it cannot introduce actions that do not exist in the full definition.

The commit boundary validates actual arguments against the full
`inputSchema`, so a compact model-visible definition never weakens execution
validation.

## Provider adapters

OpenAI-compatible APIs receive:

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "Read text from a local file.",
    "parameters": {"type": "object"}
  }
}
```

Anthropic receives:

```json
{
  "name": "read_file",
  "description": "Read text from a local file.",
  "input_schema": {"type": "object"}
}
```

These are serialization details, not Core domain models.
