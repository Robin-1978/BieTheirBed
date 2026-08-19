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
    assert "校验并热发布" in page
    assert "API Key" in page
    assert "新建自定义 Knoa Agent ID" in page
    assert "允许创建受治理 Child Task" in page
    assert 'id="agentTools"' in page
    assert 'id="delegationTargets"' in page
    assert "一个 Provider 可以提供多个 Model" in page
    assert "共享的是模型调用能力" in page
