"""Skim/model-signature contract: skim must be a strict, safe projection of full.

Rules (see docs/tool-schema-optimization.md):
1. skim.name == full.name
2. skim required set == full required set (never drop/add a required field)
3. skim properties ⊆ full properties (never invent actions)
4. len(skim JSON) <= len(full JSON) (a skim must actually be slimmer,
   unless the full definition is already minimal: <= 300 chars)
"""
from __future__ import annotations

import inspect
import json

import pytest

import knoa_platform.tools as _tools_pkg
from knoa_platform.tools.base import ToolBase


def _instances():
    import importlib
    import pkgutil

    out = []
    for mod_info in pkgutil.iter_modules(_tools_pkg.__path__):
        mod = importlib.import_module(f"knoa_platform.tools.{mod_info.name}")
        for attr, cls in vars(mod).items():
            if (
                isinstance(cls, type)
                and issubclass(cls, ToolBase)
                and cls is not ToolBase
                and attr.endswith("Tool")
                and cls.__module__ == mod.__name__
            ):
                try:
                    inst = cls()
                except TypeError:
                    params = list(inspect.signature(cls.__init__).parameters)[1:]
                    try:
                        inst = cls(*[None] * len(params))
                    except Exception:
                        continue
                out.append(inst)
    return out


@pytest.mark.parametrize("tool", _instances(), ids=lambda t: t.name)
def test_skim_is_safe_projection(tool) -> None:
    full = tool.definition()
    skim = tool.skim_definition()
    assert skim["name"] == full["name"]
    full_schema = full.get("inputSchema") or {}
    skim_schema = skim.get("inputSchema") or {}
    assert (full_schema.get("type"), skim_schema.get("type")) == ("object", "object")
    full_props = set((full_schema.get("properties") or {}).keys())
    skim_props = set((skim_schema.get("properties") or {}).keys())
    assert skim_props <= full_props, f"{tool.name}: skim invents {sorted(skim_props - full_props)}"
    assert set(skim_schema.get("required") or []) == set(
        full_schema.get("required") or []
    ), f"{tool.name}: skim changes required fields"
    full_len = len(json.dumps(full, ensure_ascii=False))
    skim_len = len(json.dumps(skim, ensure_ascii=False))
    if full_len > 300:
        assert skim_len <= full_len, f"{tool.name}: skim ({skim_len}) bigger than full ({full_len})"
