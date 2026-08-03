"""Catppuccin Mocha color palette for console/Rich rendering."""
from __future__ import annotations

from rich.theme import Theme


COLORS = {
    "primary": "#89b4fa",       # Blue
    "success": "#a6e3a1",       # Green
    "warning": "#f9e2af",       # Yellow
    "error": "#f38ba8",         # Red
    "muted": "#6c7086",         # Overlay0
    "text": "#cdd6f4",          # Text
    "bg": "#1e1e2e",            # Base
    "tool_name": "#89b4fa",     # Blue
    "tool_args": "#94e2d5",     # Teal
    "tool_result": "#bac2de",   # Subtext1
    "tool_icon": "#94e2d5",     # Teal
    "think": "#585b70",         # Surface2
    "think_dim": "#585b70",     # Surface2
    "think_icon": "#585b70",    # Surface2
    "ai_label": "#89b4fa",      # Blue
    "prompt": "#a6e3a1",        # Green
    "user": "#a6e3a1",          # Green
    "assistant": "#89b4fa",     # Blue
}


TOKYO_NIGHT = Theme({
    "primary": f"bold {COLORS['primary']}",
    "success": COLORS['success'],
    "warning": COLORS['warning'],
    "error": f"bold {COLORS['error']}",

    "muted": COLORS['muted'],
    "text": COLORS['text'],

    "user": f"bold {COLORS['user']}",
    "assistant": f"bold {COLORS['assistant']}",

    "tool_name": f"bold {COLORS['tool_name']}",
    "tool_args": COLORS['tool_args'],
    "tool_result": COLORS['tool_result'],
    "tool_icon": f"bold {COLORS['tool_icon']}",

    "think": f"italic {COLORS['think']}",
    "think_dim": f"dim italic {COLORS['think_dim']}",
    "think_icon": COLORS['think_icon'],

    "ai_label": f"bold {COLORS['ai_label']}",
    "prompt": f"bold {COLORS['prompt']}",

    "status_ready": COLORS['success'],
    "status_thinking": COLORS['primary'],
    "status_executing": COLORS['warning'],

    "divider": COLORS['muted'],
    "header": f"bold {COLORS['text']}",
})


def get_theme() -> Theme:
    """Get the Catppuccin Mocha theme."""
    return TOKYO_NIGHT


def color(name: str) -> str:
    """Get a color by name."""
    return COLORS.get(name, COLORS['text'])


def status_color(status: str) -> str:
    """Get color for a status."""
    status_colors = {
        "ready": COLORS['success'],
        "thinking": COLORS['primary'],
        "executing": COLORS['warning'],
    }
    return status_colors.get(status, COLORS['muted'])
