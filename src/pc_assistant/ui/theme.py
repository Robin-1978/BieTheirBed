from __future__ import annotations

from rich.theme import Theme


# Tokyo Night color palette
COLORS = {
    "primary": "#7aa2f7",
    "success": "#9ece6a",
    "warning": "#e0af68",
    "error": "#f7768e",
    "muted": "#565f89",
    "text": "#c0caf5",
    "bg": "#1a1b26",
    "tool_name": "#7aa2f7",
    "tool_args": "#73daca",
    "tool_result": "#9aa5ce",
    "tool_icon": "#73daca",
    "think": "#3b4261",
    "think_dim": "#3b4261",
    "think_icon": "#3b4261",
    "ai_label": "#7aa2f7",
    "prompt": "#9ece6a",
    "user": "#9ece6a",
    "assistant": "#7aa2f7",
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
    """Get the Tokyo Night theme."""
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
