"""Explicit transforms from observation-image coordinates to desktop pixels."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoordinateTransform:
    image_width: int
    image_height: int
    desktop_x: int
    desktop_y: int
    desktop_width: int
    desktop_height: int
    rotation: int = 0

    def to_desktop(self, x: float, y: float) -> tuple[int, int]:
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("Image dimensions must be positive")
        if not (0 <= x < self.image_width and 0 <= y < self.image_height):
            raise ValueError("Image coordinate is outside the observation")
        rotation = self.rotation % 360
        nx = x / self.image_width
        ny = y / self.image_height
        if rotation == 0:
            rx, ry = nx, ny
        elif rotation == 90:
            rx, ry = 1 - ny, nx
        elif rotation == 180:
            rx, ry = 1 - nx, 1 - ny
        elif rotation == 270:
            rx, ry = ny, 1 - nx
        else:
            raise ValueError("Rotation must be 0, 90, 180, or 270 degrees")
        return (
            round(self.desktop_x + rx * self.desktop_width),
            round(self.desktop_y + ry * self.desktop_height),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "image_width": self.image_width,
            "image_height": self.image_height,
            "desktop_x": self.desktop_x,
            "desktop_y": self.desktop_y,
            "desktop_width": self.desktop_width,
            "desktop_height": self.desktop_height,
            "rotation": self.rotation,
        }
