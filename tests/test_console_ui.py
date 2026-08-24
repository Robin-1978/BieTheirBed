from __future__ import annotations

import re
import subprocess

from knoa_platform.console_ui import hub_console_html, node_console_html


def _script(page: str) -> str:
    match = re.search(r"<script>(.*)</script>", page, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_embedded_console_javascript_is_syntax_valid() -> None:
    pages = (
        hub_console_html("csrf-token", "https://hub.example.com"),
        node_console_html("csrf-token"),
    )
    for page in pages:
        result = subprocess.run(
            ["node", "--check", "-"],
            input=_script(page),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_node_console_owns_configuration_and_secret_management() -> None:
    page = node_console_html("csrf-token")

    assert "/v1/console/config/publish" in page
    assert "/v1/console/secrets/" in page
    assert "/v1/console/extensions" in page
    assert "/v1/console/capabilities/confirm" in page
    assert "校验并热发布" in page
    assert "API Key" in page
    assert "新增自定义 Agent" in page
    assert "允许创建受治理 Child Task" in page
    assert 'id="agentTools"' in page
    assert 'id="delegationTargets"' in page
    assert "一个 Provider 可以提供多个 Model" in page
    assert "不会复制 Endpoint 或 API Key" in page
    assert "Provider 连接" in page
    assert "新增并热生效" in page
    assert "保存并热生效" in page
    assert "publishCurrent(`Add Provider" in page
    assert 'id="newModelProvider"' in page
    assert 'id="shareModelSelect"' in page
    assert 'const alias=el("shareModelSelect").value' in page
    assert 'el("shareModelSelect").onchange=renderSharing' in page
    assert "发布高级 JSON 更改" in page
    assert "新增 Provider 草稿" not in page
    for tab in ("overview", "models", "agents", "extensions", "sharing", "system"):
        assert f'data-console-tab="{tab}"' in page
        assert f'data-console-panel="{tab}"' in page

    assert "Capability / Skill / MCP" in page
    assert 'id="skillInventory"' in page
    assert 'id="mcpInventory"' in page


def test_node_console_overview_leads_with_three_state_summary() -> None:
    page = node_console_html("csrf-token")

    # The default status answers with the three user-facing classes and keeps
    # transport/runtime detail collapsed in the advanced section.
    assert "状态：${summary}" in page
    assert "正常：Node 已连接" in page
    assert "需要处理：" in page
    assert "阻塞：" in page
    assert 'id="statusAdvanced"' in page
    assert 'id="statusDetail"' in page
    assert "连接与链路技术细节" in page
    # Raw identifiers are not part of the default status line anymore.
    assert 'body.node.display_name||body.node.node_id' not in page


def test_node_console_script_only_references_rendered_elements() -> None:
    page = node_console_html("csrf-token")
    rendered_ids = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', page))
    referenced_ids = set(re.findall(r'el\("([A-Za-z0-9_-]+)"\)', _script(page)))

    assert referenced_ids <= rendered_ids
