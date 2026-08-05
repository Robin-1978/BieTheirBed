"""Layer 2 (screen understanding + precise control) tests: grid overlay,
accessibility backend, ui/screen tools, and the verifier post-verify rule."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.harness.verifier import Verifier
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.registry import ToolRegistry
from pc_assistant.tools.screen import ScreenTool
from pc_assistant.tools.ui import UITool
from pc_assistant.vision import a11y, grid
from pc_assistant.vision import preprocess
from pc_assistant.vision.coordinates import CoordinateTransform


def _run(coro):
    return asyncio.run(coro)


# ==================================================================
# vision/grid.py
# ==================================================================


class TestGrid:
    def test_cell_label(self):
        assert grid.cell_label(0, 0) == "A1"
        assert grid.cell_label(1, 2) == "B3"

    def test_grid_dimensions_clamp(self):
        assert grid.grid_dimensions(0, 0) == (10, 10)
        assert grid.grid_dimensions(None, None) == (10, 10)
        assert grid.grid_dimensions(4, 6) == (4, 6)
        assert grid.grid_dimensions(200, 300) == (26, 26)

    def test_cell_for_point(self):
        # 1000x1000, 10 cols/rows -> each cell 100px.
        assert grid.cell_for_point(0, 0, width=1000, height=1000) == "A1"
        assert grid.cell_for_point(250, 350, width=1000, height=1000) == "C4"
        assert grid.cell_for_point(999, 999, width=1000, height=1000) == "J10"
        assert grid.cell_for_point(1000, 0, width=1000, height=1000) is None

    def test_point_for_cell_roundtrip(self):
        assert grid.point_for_cell("A1", width=1000, height=1000) == (50, 50)
        assert grid.point_for_cell("J10", width=1000, height=1000) == (950, 950)
        assert grid.point_for_cell("B4", width=1000, height=1000) == (150, 350)
        assert grid.point_for_cell("ZZ", width=1000, height=1000) is None
        assert grid.point_for_cell("", width=1000, height=1000) is None

    def test_cell_roundtrip_consistency(self):
        for label in ("A1", "C4", "J10", "E7"):
            x, y = grid.point_for_cell(label, width=800, height=600)
            assert grid.cell_for_point(x, y, width=800, height=600) == label

    def test_draw_grid_returns_image(self):
        from PIL import Image

        img = Image.new("RGB", (100, 100), "white")
        out = grid.draw_grid(img, cols=4, rows=4)
        assert isinstance(out, Image.Image)
        assert out.size == img.size


class TestCoordinateTransform:
    def test_scaled_crop_with_negative_desktop_origin(self):
        transform = CoordinateTransform(
            image_width=500,
            image_height=250,
            desktop_x=-1920,
            desktop_y=100,
            desktop_width=1000,
            desktop_height=500,
        )
        assert transform.to_desktop(250, 125) == (-1420, 350)

    def test_rotation_90(self):
        transform = CoordinateTransform(100, 50, 0, 0, 200, 100, rotation=90)
        assert transform.to_desktop(50, 25) == (100, 50)

    def test_rejects_out_of_bounds(self):
        transform = CoordinateTransform(100, 100, 0, 0, 100, 100)
        with pytest.raises(ValueError):
            transform.to_desktop(100, 10)


# ==================================================================
# vision/a11y.py
# ==================================================================


class FakeNode:
    """Duck-typed a11y node with the pyatspi-style API."""

    def __init__(self, name, role, x=0, y=0, w=10, h=10, children=None):
        self.name = name
        self._role = role
        self.geom = (x, y, w, h)
        self.children = children or []

    @property
    def role(self):
        return self._role

    def getExtents(self, coord_type):
        return self.geom

    def getChildCount(self):
        return len(self.children)

    def __getitem__(self, i):
        return self.children[i]


class TestA11yWalk:
    def test_walk_flattens_tree_with_bbox(self):
        root = FakeNode("Window", "window", w=100, h=100, children=[
            FakeNode("File", "menu", x=5, y=5, w=50, h=20, children=[
                FakeNode("Save", "button", x=6, y=7, w=30, h=10),
            ]),
        ])
        elements = list(a11y.walk(root))
        names = [e["name"] for e in elements]
        assert names == ["Window", "File", "Save"]
        save = elements[2]
        assert save["x"] == 6 and save["y"] == 7
        assert save["width"] == 30 and save["height"] == 10
        assert "Save" in save["path"]

    def test_find_elements_by_name_and_role(self):
        elements = [
            {"name": "Search box", "role": "text", "width": 100, "height": 20},
            {"name": "Save", "role": "button", "width": 40, "height": 15},
            {"name": "saved projects", "role": "label", "width": 60, "height": 10},
        ]
        assert a11y.find_elements(elements, name="save") == [elements[1], elements[2]]
        assert a11y.find_elements(elements, role="button") == [elements[1]]
        assert a11y.find_elements(elements, name="save", role="button") == [elements[1]]

    def test_read_bbox_from_box_attr(self):
        from types import SimpleNamespace

        node = SimpleNamespace(box=SimpleNamespace(left=1, top=2, width=30, height=40))
        assert a11y.read_bbox(node) == (1, 2, 30, 40)

    def test_role_prefers_pyatspi_role_name(self):
        node = FakeNode("Save", "legacy-role")
        node.getRoleName = lambda: "push button"
        assert a11y.node_role(node) == "push button"

    def test_breadth_first_walk_is_fair_across_applications(self):
        huge = FakeNode(
            "Shell",
            "application",
            children=[FakeNode(f"shell-{i}", "panel") for i in range(20)],
        )
        editor = FakeNode(
            "Editor",
            "application",
            children=[FakeNode("Save", "button")],
        )

        elements = a11y.walk_forest_breadth_first([huge, editor], max_elements=4)

        assert [element["name"] for element in elements[:2]] == ["Shell", "Editor"]
        assert any(element["name"] == "Save" for element in elements)

    def test_read_bbox_from_pyatspi_component(self, monkeypatch):
        import sys
        from types import SimpleNamespace

        extents = SimpleNamespace(x=1, y=2, width=30, height=40)
        component = SimpleNamespace(getExtents=lambda _coords: extents)
        node = SimpleNamespace(queryComponent=lambda: component)
        monkeypatch.setitem(sys.modules, "pyatspi", SimpleNamespace(DESKTOP_COORDS=0))

        assert a11y.read_bbox(node) == (1, 2, 30, 40)

    def test_list_elements_no_backend(self, monkeypatch):
        # Backend absence is a contract; do not depend on the test host's
        # optional AT-SPI installation state.
        monkeypatch.setattr(a11y.LinuxAtspiBackend, "available", lambda self: False)
        elements, error = a11y.list_elements(platform="linux", ui_backend="atspi")
        assert error and not elements


# ==================================================================
# tools/ui.py (semantic layer)
# ==================================================================


def _fake_element(name="Save", x=10, y=20, w=100, h=30):
    return {"name": name, "role": "button", "x": x, "y": y, "width": w, "height": h}


class TestUITool:
    @pytest.mark.asyncio
    async def test_list_graceful_without_backend(self):
        tool = UITool(ui_backend="auto")
        res = await tool.execute(action="list")
        assert "error" in res or "elements" in res

    @pytest.mark.asyncio
    async def test_list_uses_backend_elements(self):
        with patch("pc_assistant.tools.ui.a11y.list_elements", return_value=([_fake_element()], "")):
            res = await UITool(ui_backend="atspi").execute(action="list")
        assert res["count"] == 1

    @pytest.mark.asyncio
    async def test_find_returns_bbox_and_center(self):
        with patch("pc_assistant.tools.ui.a11y.list_elements", return_value=([_fake_element()], "")):
            res = await UITool(ui_backend="atspi").execute(action="find", name="Save")
        assert res["found"] is True
        assert res["element_ref"]["snapshot_id"]
        assert res["bbox"] == {"x": 10, "y": 20, "width": 100, "height": 30}
        assert res["center"] == (60, 35)

    @pytest.mark.asyncio
    async def test_click_clicks_element_center(self):
        with patch("pc_assistant.tools.ui.a11y.list_elements", return_value=([_fake_element()], "")):
            tool = UITool(ui_backend="atspi")
            found = await tool.execute(action="find", name="Save")
            with patch("pyautogui.click") as mock_click:
                res = await tool.execute(action="click", element_ref=found["element_ref"])
        assert res["success"] is True
        mock_click.assert_called_once_with(60, 35)

    @pytest.mark.asyncio
    async def test_click_not_found(self):
        with patch("pc_assistant.tools.ui.a11y.list_elements", return_value=([], "")):
            res = await UITool(ui_backend="atspi").execute(action="find", name="Nope")
        assert "No element found" in res["error"]

    @pytest.mark.asyncio
    async def test_type_clicks_then_writes(self):
        with patch("pc_assistant.tools.ui.a11y.list_elements", return_value=([_fake_element()], "")):
            tool = UITool(ui_backend="atspi")
            found = await tool.execute(action="find", name="Save")
            with patch("pyautogui.click") as mock_click, patch("pyautogui.write") as mock_write:
                res = await tool.execute(action="type", element_ref=found["element_ref"], text="hello")
        assert res["success"] is True
        mock_click.assert_called_once_with(60, 35)
        mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_rejects_ambiguous_partial_name(self):
        elements = [_fake_element("Save"), _fake_element("Save As")]
        with patch("pc_assistant.tools.ui.a11y.list_elements", return_value=(elements, "")):
            res = await UITool(ui_backend="atspi").execute(action="find", name="Sav")
        assert "Ambiguous" in res["error"]

    @pytest.mark.asyncio
    async def test_click_rejects_stale_reference(self):
        now = [10.0]
        with patch("pc_assistant.tools.ui.a11y.list_elements", return_value=([_fake_element()], "")):
            tool = UITool(ui_backend="atspi", snapshot_ttl_seconds=1, clock=lambda: now[0])
            found = await tool.execute(action="find", name="Save")
            now[0] = 12.0
            res = await tool.execute(action="click", element_ref=found["element_ref"])
        assert "stale" in res["error"].lower()


# ==================================================================
# tools/screen.py (visual layer)
# ==================================================================


def _fake_block(width=100, height=50):
    return {
        "type": "image",
        "image_url": "data:image/jpeg;base64,AAAA",
        "media_type": "image/jpeg",
        "width": width,
        "height": height,
    }


class TestScreenTool:
    @pytest.mark.asyncio
    async def test_look_defaults_to_lossless_png_without_grid(self, tmp_path):
        with patch("pc_assistant.tools.screen.preprocess.capture_block", return_value=_fake_block()) as capture:
            res = await ScreenTool(artifact_dir=tmp_path).execute(action="look")

        assert Path(res["path"]).suffix == ".png"
        assert res["artifact"]["media_type"] == "image/png"
        assert "grid" not in res
        assert capture.call_args.kwargs["grid"] is False

    @pytest.mark.asyncio
    async def test_look_returns_image_and_grid_metadata(self, tmp_path):
        with patch("pc_assistant.tools.screen.preprocess.capture_block", return_value=_fake_block(100, 50)):
            res = await ScreenTool(grid_enabled=True, artifact_dir=tmp_path).execute(action="look", grid=True, cols=10, rows=10)
        assert res["success"] is True
        assert res["image"]["type"] == "image"
        assert res["grid"]["enabled"] is True
        assert res["screen_size"] == {"width": 100, "height": 50}
        assert res["artifact"]["kind"] == "image"
        assert Path(res["path"]).is_file()

    @pytest.mark.asyncio
    async def test_verify_has_verification_note(self, tmp_path):
        with patch("pc_assistant.tools.screen.preprocess.capture_block", return_value=_fake_block()):
            res = await ScreenTool(artifact_dir=tmp_path).execute(action="verify")
        assert "verification" in res

    @pytest.mark.asyncio
    async def test_capture_unavailable_errors(self):
        with patch("pc_assistant.tools.screen.preprocess.capture_block", return_value=None):
            res = await ScreenTool().execute(action="look")
        assert "error" in res

    @pytest.mark.asyncio
    async def test_capture_storage_failure_is_reported(self, tmp_path):
        bad_block = _fake_block()
        bad_block["image_url"] = "data:image/jpeg;base64,not-valid!"
        with patch("pc_assistant.tools.screen.preprocess.capture_block", return_value=bad_block):
            res = await ScreenTool(artifact_dir=tmp_path).execute(action="look")
        assert "Failed to store screen capture" in res["error"]

    @pytest.mark.asyncio
    async def test_info_reports_monitors(self):
        class FakeSCT:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
            monitors = [{"left": 0, "top": 0, "width": 200, "height": 100}]

        with patch("mss.mss", return_value=FakeSCT()):
            res = await ScreenTool().execute(action="info")
        assert res["success"] is True


# ==================================================================
# vision/preprocess.capture_block
# ==================================================================


class TestCaptureBlock:
    def test_pipline_capture_block_full_screen(self):
        block = preprocess.capture_block(None, max_side=320, quality=50, grid=False)
        assert block is not None
        assert block["type"] == "image"
        assert block.get("width", 0) > 0
        assert block["image_url"].startswith("data:image/png;base64,")

    def test_capture_region_block(self):
        block = preprocess.capture_block({"x": 0, "y": 0, "width": 64, "height": 64}, max_side=128)
        assert block is not None
        assert block["width"] == 64
        assert block["height"] == 64


# ==================================================================
# verifier post-verify strategy
# ==================================================================


class _GPUTool(ToolBase):
    name = "mouse"

    async def execute(self, **kw):
        return "ok"

    def schema(self):
        return {"name": "mouse", "parameters": {"type": "object", "properties": {}}}


def _make_verifier(*, enabled, callback=None):
    registry = ToolRegistry()
    registry.register(_GPUTool())
    return Verifier(
        safety=SafetyChecker(),
        registry=registry,
        audit=AuditLogger(),
        verify_enabled=enabled,
        post_verify_callback=callback,
    )


class TestPostVerify:
    @pytest.mark.asyncio
    async def test_callback_fires_only_after_explicit_post_verify(self):
        calls = []

        async def cb(tool_name, arguments):
            calls.append((tool_name, arguments.get("action")))
            return "screen confirmed"

        async def confirm(_tool_name, _arguments):
            return True

        verifier = _make_verifier(enabled=True, callback=cb)
        verdict = await verifier.verify(
            "mouse", {"action": "click"}, confirm_callback=confirm
        )
        assert verdict.accepted
        assert calls == []
        await verifier.post_verify("mouse", {"action": "click"})
        assert calls == [("mouse", "click")]
        verified = verifier._audit.query(action="tool_call_verified")
        assert len(verified) == 1
        assert "screen confirmed" in verified[0]["reason"]

    @pytest.mark.asyncio
    async def test_no_callback_when_disabled(self):
        calls = []

        async def cb(tool_name, arguments):
            calls.append(tool_name)
            return "x"

        verifier = _make_verifier(enabled=False, callback=cb)
        await verifier.verify("mouse", {"action": "click"})
        await verifier.post_verify("mouse", {"action": "click"})
        assert calls == []
        assert verifier._audit.query(action="tool_call_verified") == []

    @pytest.mark.asyncio
    async def test_no_callback_for_non_risky_action(self):
        calls = []

        async def cb(tool_name, arguments):
            calls.append(tool_name)
            return "x"

        verifier = _make_verifier(enabled=True, callback=cb)
        await verifier.verify("mouse", {"action": "position"})
        await verifier.post_verify("mouse", {"action": "position"})
        # "position" is read-only -> no post-verify.
        assert calls == []

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_fail_verdict(self):
        async def cb(tool_name, arguments):
            raise RuntimeError("capture failed")

        async def confirm(_tool_name, _arguments):
            return True

        verifier = _make_verifier(enabled=True, callback=cb)
        verdict = await verifier.verify(
            "mouse", {"action": "click"}, confirm_callback=confirm
        )
        assert verdict.accepted
        await verifier.post_verify("mouse", {"action": "click"})
        verified = verifier._audit.query(action="tool_call_verified")
        assert len(verified) == 1
        assert "post-verify failed" in verified[0]["reason"]
