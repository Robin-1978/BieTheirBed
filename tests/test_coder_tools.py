from __future__ import annotations

from pathlib import Path
import pytest

from knoa_platform.config import AppConfig
from knoa_platform.tools.edit_file import EditFileTool
from knoa_platform.tools.glob_files import GlobFilesTool
from knoa_platform.tools.grep_search import GrepSearchTool


@pytest.mark.asyncio
async def test_edit_file_exact_replacement(tmp_path: Path) -> None:
    test_file = tmp_path / "sample.py"
    test_file.write_text("def hello():\n    print('old')\n    return 42\n", encoding="utf-8")

    tool = EditFileTool()
    res = await tool.execute(
        path=str(test_file),
        old_string="    print('old')",
        new_string="    print('new')",
    )

    assert res.get("success") is True
    assert res.get("replacements") == 1
    assert "print('new')" in test_file.read_text(encoding="utf-8")
    assert "print('old')" not in test_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_file_rejects_ambiguous_matches(tmp_path: Path) -> None:
    test_file = tmp_path / "ambiguous.py"
    test_file.write_text("item = 1\nitem = 1\n", encoding="utf-8")

    tool = EditFileTool()
    # When multiple occurrences exist without replace_all=True, it must fail safely
    res = await tool.execute(
        path=str(test_file),
        old_string="item = 1",
        new_string="item = 2",
    )
    assert "error" in res
    assert "Provide more unique surrounding context" in res["error"]

    # When replace_all=True, it should replace all occurrences
    res_all = await tool.execute(
        path=str(test_file),
        old_string="item = 1",
        new_string="item = 2",
        replace_all=True,
    )
    assert res_all.get("success") is True
    assert res_all.get("replacements") == 2
    assert test_file.read_text(encoding="utf-8") == "item = 2\nitem = 2\n"


@pytest.mark.asyncio
async def test_edit_file_not_found_or_identical(tmp_path: Path) -> None:
    test_file = tmp_path / "demo.txt"
    test_file.write_text("hello world\n", encoding="utf-8")

    tool = EditFileTool()
    # Identical string rejection
    res_same = await tool.execute(
        path=str(test_file),
        old_string="hello",
        new_string="hello",
    )
    assert "must be different" in res_same.get("error", "")

    # Not found rejection
    res_missing = await tool.execute(
        path=str(test_file),
        old_string="missing string",
        new_string="found",
    )
    assert "old_string not found" in res_missing.get("error", "")


@pytest.mark.asyncio
async def test_grep_search_finds_lines_and_filters_glob(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    py_file = tmp_path / "sub" / "code.py"
    txt_file = tmp_path / "sub" / "readme.txt"
    ignored_file = tmp_path / "node_modules" / "sub" / "ignored.py"
    (tmp_path / "node_modules" / "sub").mkdir(parents=True)

    py_file.write_text("def find_me_func():\n    return 'target_val'\n", encoding="utf-8")
    txt_file.write_text("This also has target_val in text\n", encoding="utf-8")
    ignored_file.write_text("Should ignore target_val here\n", encoding="utf-8")

    tool = GrepSearchTool()

    # Search with glob="*.py"
    res = await tool.execute(
        pattern="target_val",
        path=str(tmp_path),
        glob="*.py",
    )

    assert res.get("matches_count") == 1
    matches = res.get("matches", [])
    assert len(matches) == 1
    assert matches[0]["file"] == "sub/code.py"
    assert matches[0]["line"] == 2
    assert "target_val" in matches[0]["content"]


@pytest.mark.asyncio
async def test_glob_files_discovers_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "main.py").write_text("# main", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "helper.ts").write_text("// helper", encoding="utf-8")
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / ".git" / "hooks" / "pre-commit.py").write_text("# git", encoding="utf-8")

    tool = GlobFilesTool()
    res = await tool.execute(
        pattern="**/*.py",
        path=str(tmp_path),
    )

    files = res.get("files", [])
    assert "src/pkg/main.py" in files
    # .git must be ignored
    assert not any(".git" in f for f in files)


def test_coder_agent_registered_in_catalog() -> None:
    config = AppConfig()
    catalog = config.node_agent_catalog()

    assert "coder" in catalog.agents
    coder = catalog.agents["coder"]
    assert coder.enabled is True
    assert coder.visibility == "delegate"
    assert "read_file" in coder.allowed_platform_tools
    assert "edit_file" in coder.allowed_platform_tools
    assert "write_file" in coder.allowed_platform_tools
    assert "grep_search" in coder.allowed_platform_tools
    assert "glob_files" in coder.allowed_platform_tools
    assert "run_command" in coder.allowed_platform_tools

    # Test researcher agent is registered and has minimal toolset
    assert "researcher" in catalog.agents
    researcher = catalog.agents["researcher"]
    assert researcher.enabled is True
    assert researcher.visibility == "delegate"
    assert "web_search" in researcher.allowed_platform_tools
    assert "web_fetch" in researcher.allowed_platform_tools
    assert "read_artifact" in researcher.allowed_platform_tools
    assert "run_command" not in researcher.allowed_platform_tools
    assert "write_file" not in researcher.allowed_platform_tools
    assert researcher.platform_capability_ceiling == frozenset({"network"})

    # Test worker agent has full composite toolset with strict delegation disabled
    assert "worker" in catalog.agents
    worker = catalog.agents["worker"]
    assert worker.enabled is True
    assert worker.visibility == "delegate"
    assert "*" in worker.allowed_platform_tools
    assert "*" in worker.platform_capability_ceiling
    assert worker.delegation.allowed is False  # Never allow recursive delegation

    # Main knoa agent must have coder, researcher, and worker in delegation targets
    knoa = catalog.agents["knoa"]
    assert "coder" in knoa.delegation.targets
    assert "researcher" in knoa.delegation.targets
    assert "worker" in knoa.delegation.targets
