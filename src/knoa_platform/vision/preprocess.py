"""Image token estimation used by prompt budgeting."""
from __future__ import annotations

import math


def estimate_image_tokens(width: int, height: int) -> int:
    """Estimate provider tokens using a conservative 112px tile heuristic."""
    if width <= 0 or height <= 0:
        return 0
    tiles = math.ceil(width / 112) * math.ceil(height / 112)
    return max(1, 85 + tiles * 170)
