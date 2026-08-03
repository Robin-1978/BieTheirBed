"""Theme system with multiple built-in palettes.

Each theme is a dict of named colors. ``get_palette()`` returns the active
palette based on ``AppConfig.ui_theme``.  The TCSS file uses CSS variables
injected at runtime via ``ChatApp.get_css_variables()``.
"""
from __future__ import annotations

from typing import Any

from rich.theme import Theme


# ── Palette definitions ───────────────────────────────────────────────

PALETTES: dict[str, dict[str, str]] = {
    "catppuccin": {
        "primary": "#89b4fa",
        "success": "#a6e3a1",
        "warning": "#f9e2af",
        "error": "#f38ba8",
        "muted": "#6c7086",
        "text": "#cdd6f4",
        "subtext": "#bac2de",
        "bg": "#1e1e2e",
        "surface0": "#313244",
        "surface1": "#45475a",
        "surface2": "#585b70",
        "mantle": "#181825",
        "tool": "#94e2d5",
        "think": "#585b70",
        "user": "#a6e3a1",
        "assistant": "#89b4fa",
        "mauve": "#cba6f7",
    },
    "tokyo_night": {
        "primary": "#7aa2f7",
        "success": "#9ece6a",
        "warning": "#e0af68",
        "error": "#f7768e",
        "muted": "#565f89",
        "text": "#c0caf5",
        "subtext": "#9aa5ce",
        "bg": "#1a1b26",
        "surface0": "#24283b",
        "surface1": "#3b4261",
        "surface2": "#414868",
        "mantle": "#16161e",
        "tool": "#73daca",
        "think": "#3b4261",
        "user": "#9ece6a",
        "assistant": "#7aa2f7",
        "mauve": "#bb9af7",
    },
    "dracula": {
        "primary": "#bd93f9",
        "success": "#50fa7b",
        "warning": "#f1fa8c",
        "error": "#ff5555",
        "muted": "#6272a4",
        "text": "#f8f8f2",
        "subtext": "#bfbfbf",
        "bg": "#282a36",
        "surface0": "#44475a",
        "surface1": "#4d4f68",
        "surface2": "#585b70",
        "mantle": "#21222c",
        "tool": "#8be9fd",
        "think": "#44475a",
        "user": "#50fa7b",
        "assistant": "#bd93f9",
        "mauve": "#ff79c6",
    },
    "nord": {
        "primary": "#88c0d0",
        "success": "#a3be8c",
        "warning": "#ebcb8b",
        "error": "#bf616a",
        "muted": "#4c566a",
        "text": "#eceff4",
        "subtext": "#d8dee9",
        "bg": "#2e3440",
        "surface0": "#3b4252",
        "surface1": "#434c5e",
        "surface2": "#4c566a",
        "mantle": "#272c36",
        "tool": "#8fbcbb",
        "think": "#434c5e",
        "user": "#a3be8c",
        "assistant": "#88c0d0",
        "mauve": "#b48ead",
    },
    "gruvbox": {
        "primary": "#83a598",
        "success": "#b8bb26",
        "warning": "#fabd2f",
        "error": "#fb4934",
        "muted": "#665c54",
        "text": "#ebdbb2",
        "subtext": "#d5c4a1",
        "bg": "#282828",
        "surface0": "#3c3836",
        "surface1": "#504945",
        "surface2": "#665c54",
        "mantle": "#1d2021",
        "tool": "#8ec07c",
        "think": "#504945",
        "user": "#b8bb26",
        "assistant": "#83a598",
        "mauve": "#d3869b",
    },
}

AVAILABLE_THEMES = list(PALETTES.keys())

_current_theme: str = "catppuccin"


def set_theme(name: str) -> bool:
    """Set the active theme. Returns False if theme not found."""
    global _current_theme
    if name not in PALETTES:
        return False
    _current_theme = name
    return True


def get_palette(name: str | None = None) -> dict[str, str]:
    """Get a color palette by name (or the current active palette)."""
    key = name or _current_theme
    return PALETTES.get(key, PALETTES["catppuccin"])


def get_theme_name() -> str:
    return _current_theme


# ── Rich Theme (for console mode) ────────────────────────────────────

def _build_rich_theme(palette: dict[str, str]) -> Theme:
    return Theme({
        "primary": f"bold {palette['primary']}",
        "success": palette["success"],
        "warning": palette["warning"],
        "error": f"bold {palette['error']}",
        "muted": palette["muted"],
        "text": palette["text"],
        "user": f"bold {palette['user']}",
        "assistant": f"bold {palette['assistant']}",
        "tool_name": f"bold {palette['primary']}",
        "tool_args": palette["tool"],
        "tool_result": palette["subtext"],
        "tool_icon": f"bold {palette['tool']}",
        "think": f"italic {palette['think']}",
        "think_dim": f"dim italic {palette['think']}",
        "think_icon": palette["think"],
        "ai_label": f"bold {palette['assistant']}",
        "prompt": f"bold {palette['user']}",
        "status_ready": palette["success"],
        "status_thinking": palette["primary"],
        "status_executing": palette["warning"],
        "divider": palette["muted"],
        "header": f"bold {palette['text']}",
    })


TOKYO_NIGHT = _build_rich_theme(get_palette())


def get_rich_theme() -> Theme:
    """Get a Rich Theme for the current palette."""
    return _build_rich_theme(get_palette())


# ── Backward compat helpers ───────────────────────────────────────────

COLORS = get_palette()


def color(name: str) -> str:
    """Get a color by name."""
    p = get_palette()
    return p.get(name, p["text"])


def status_color(status: str) -> str:
    """Get color for a status."""
    p = get_palette()
    status_colors = {
        "ready": p["success"],
        "thinking": p["primary"],
        "executing": p["warning"],
    }
    return status_colors.get(status, p["muted"])
