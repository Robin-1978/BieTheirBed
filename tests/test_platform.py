from __future__ import annotations

import pytest

from pc_assistant import platform_


class TestGetPlatform:
    @pytest.mark.parametrize("system_value,expected", [
        ("Windows", "windows"),
        ("Linux", "linux"),
        ("Darwin", "macos"),
    ])
    def test_known_platforms(self, monkeypatch, system_value, expected):
        monkeypatch.setattr(platform_.platform, "system", lambda: system_value)
        assert platform_.get_platform() == expected

    def test_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "FreeBSD")
        with pytest.raises(RuntimeError, match="Unsupported platform"):
            platform_.get_platform()


class TestGetShellCommand:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Windows")
        assert platform_.get_shell_command() == ("powershell", "-Command")

    def test_linux(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Linux")
        assert platform_.get_shell_command() == ("/bin/bash", "-c")

    def test_macos(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Darwin")
        assert platform_.get_shell_command() == ("/bin/zsh", "-c")


class TestGetShellName:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Windows")
        assert platform_.get_shell_name() == "PowerShell"

    def test_linux(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Linux")
        assert platform_.get_shell_name() == "bash"

    def test_macos(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Darwin")
        assert platform_.get_shell_name() == "zsh"


class TestGetPathSeparator:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Windows")
        assert platform_.get_path_separator() == "\\"

    def test_linux(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Linux")
        assert platform_.get_path_separator() == "/"

    def test_macos(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Darwin")
        assert platform_.get_path_separator() == "/"


class TestNormalizePath:
    def test_windows_forward_to_back(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Windows")
        assert platform_.normalize_path("C:/Users/test/file.txt") == "C:\\Users\\test\\file.txt"

    def test_windows_already_back(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Windows")
        assert platform_.normalize_path("C:\\Users\\test\\file.txt") == "C:\\Users\\test\\file.txt"

    def test_linux_back_to_forward(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Linux")
        assert platform_.normalize_path("home\\user\\file.txt") == "home/user/file.txt"

    def test_linux_already_forward(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Linux")
        assert platform_.normalize_path("/home/user/file.txt") == "/home/user/file.txt"

    def test_macos_back_to_forward(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Darwin")
        assert platform_.normalize_path("Users\\test\\file.txt") == "Users/test/file.txt"

    def test_macos_already_forward(self, monkeypatch):
        monkeypatch.setattr(platform_.platform, "system", lambda: "Darwin")
        assert platform_.normalize_path("/Users/test/file.txt") == "/Users/test/file.txt"
