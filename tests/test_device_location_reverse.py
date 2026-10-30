"""Tests for keyless reverse-geocode enrichment of device locations."""
from __future__ import annotations

import pytest

from knoa_platform.location import reverse as reverse_module
from knoa_platform.location.reverse import enrich_device_location


@pytest.fixture(autouse=True)
def _clear_cache():
    reverse_module._cache.clear()
    yield
    reverse_module._cache.clear()


def test_readable_address_passes_through_untouched(monkeypatch) -> None:
    monkeypatch.setattr(
        reverse_module, "_fetch_photon", lambda lat, lon: pytest.fail("must not call network")
    )
    text = "北京市朝阳区建国路88号 (39.9042,116.4074 ±25m)"
    assert enrich_device_location(text) == text


def test_coordinate_only_fix_gets_neighbourhood(monkeypatch) -> None:
    monkeypatch.setattr(
        reverse_module,
        "_fetch_photon",
        lambda lat, lon: {"name": "测试广场", "district": "测试镇", "city": "测试区", "state": "测试市"},
    )
    assert enrich_device_location("未知位置 (10.1232,20.5674 ±30m)") == (
        "未知位置 (10.1232,20.5674 ±30m) (附近：测试广场测试镇测试区测试市)"
    )


def test_lookup_failure_returns_input_unchanged(monkeypatch) -> None:
    def _boom(lat: float, lon: float) -> dict:
        raise TimeoutError("slow network")

    monkeypatch.setattr(reverse_module, "_fetch_photon", _boom)
    text = "未知位置 (10.1232,20.5674 ±30m)"
    assert enrich_device_location(text) == text


def test_second_call_serves_from_cache(monkeypatch) -> None:
    calls: list[tuple[float, float]] = []

    def _fetch(lat: float, lon: float) -> dict:
        calls.append((lat, lon))
        return {"name": "测试广场"}

    monkeypatch.setattr(reverse_module, "_fetch_photon", _fetch)
    enrich_device_location("未知位置 (10.1232,20.5674 ±30m)")
    enrich_device_location("未知位置 (10.1233,20.5673 ±30m)")
    assert len(calls) == 1


def test_empty_or_invalid_input_is_untouched() -> None:
    assert enrich_device_location("") == ""
    assert enrich_device_location("北京市") == "北京市"
    assert enrich_device_location("未知位置 (999,999)") == "未知位置 (999,999)"
