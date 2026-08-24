from knoa_platform.artifacts import artifact_refs_from_tool_output


def _artifact(artifact_id: str = "artifact-a") -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "kind": "file",
        "name": "report.txt",
        "media_type": "text/plain",
        "size": 4,
        "direction": "outbound",
        "ownership": "generated",
        "retention": "temporary",
        "status": "available",
        "visibility": "user",
    }


def test_extracts_only_explicit_user_outbound_artifact() -> None:
    first = _artifact()
    inbound = {**_artifact("artifact-in"), "direction": "inbound"}
    hidden = {**_artifact("artifact-hidden"), "visibility": "agent"}

    refs = artifact_refs_from_tool_output({"artifact": first})
    assert [ref.artifact_id for ref in refs] == ["artifact-a"]
    assert artifact_refs_from_tool_output({"artifact": inbound}) == ()
    assert artifact_refs_from_tool_output({"artifact": hidden}) == ()
    assert artifact_refs_from_tool_output({"artifact": {"artifact_id": "invalid"}}) == ()
    assert artifact_refs_from_tool_output(
        {"nested": {"artifact": _artifact("artifact-nested")}}
    ) == ()


def test_non_mapping_tool_output_has_no_artifacts() -> None:
    assert artifact_refs_from_tool_output("not structured") == ()


def test_extracts_artifact_from_standard_codex_mcp_result_envelope() -> None:
    refs = artifact_refs_from_tool_output(
        {
            "content": [{"type": "text", "text": "bounded result"}],
            "structuredContent": {
                "call_id": "mcp-2",
                "output": {
                    "success": True,
                    "artifact": _artifact("artifact-codex"),
                },
            },
        }
    )

    assert [ref.artifact_id for ref in refs] == ["artifact-codex"]
