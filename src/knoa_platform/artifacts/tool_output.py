"""Extract user-deliverable artifacts from bounded Tool outputs."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from knoa_platform.artifacts.models import ArtifactRef


def artifact_refs_from_tool_output(output: Any) -> tuple[ArtifactRef, ...]:
    """Return explicit top-level artifact references from a Tool result.

    Tool outputs are business data, so this intentionally does not recursively
    scan arbitrary JSON for keys named ``artifact``.
    """

    if not isinstance(output, dict):
        return ()
    candidate = output.get("artifact")
    # Codex App Server preserves MCP's standard result envelope.  Accept the
    # one explicit structuredContent.output.artifact path emitted by the Knoa
    # capability server without recursively scanning arbitrary business data.
    if not isinstance(candidate, dict):
        structured = output.get("structuredContent") or output.get(
            "structured_content"
        )
        inner = structured.get("output") if isinstance(structured, dict) else None
        candidate = inner.get("artifact") if isinstance(inner, dict) else None
    if not isinstance(candidate, dict):
        return ()

    bounded = {
        field_name: candidate[field_name]
        for field_name in ArtifactRef.model_fields
        if field_name in candidate
    }
    try:
        ref = ArtifactRef.model_validate(bounded)
    except ValidationError:
        return ()
    if ref.direction != "outbound" or ref.visibility != "user":
        return ()
    return (ref,)
