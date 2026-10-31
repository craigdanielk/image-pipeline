"""Per-canvas safe zones.

Safe-zone geometry is channel data, not renderer data: the caller passes it
with a citation (e.g. a channel-spec registry row) and the render receipt
records both. `SafeZone.artboard_bounds()` is the explicit, recorded choice of
"no channel safe zone; artboard edges only" — it is never applied implicitly.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .errors import CompositionError

ARTBOARD_BOUNDS_ONLY = "artboard-bounds-only"


@dataclass(frozen=True)
class SafeZone:
    top: int
    right: int
    bottom: int
    left: int
    source: str

    @classmethod
    def artboard_bounds(cls) -> "SafeZone":
        return cls(0, 0, 0, 0, ARTBOARD_BOUNDS_ONLY)

    @classmethod
    def from_percent(cls, width: int, height: int, *, top_pct: float, right_pct: float, bottom_pct: float,
                     left_pct: float, source: str) -> "SafeZone":
        # Insets round outward (ceil) so the usable area never exceeds the cited zone.
        return cls(math.ceil(height * top_pct / 100), math.ceil(width * right_pct / 100),
                   math.ceil(height * bottom_pct / 100), math.ceil(width * left_pct / 100), source)

    def rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        if min(self.top, self.right, self.bottom, self.left) < 0 or not self.source:
            raise CompositionError("safe_zone_invalid", "safe zone insets must be >= 0 and carry a source")
        if self.left + self.right >= width or self.top + self.bottom >= height:
            raise CompositionError("safe_zone_invalid", "safe zone leaves no usable area")
        return (self.left, self.top, width - self.right, height - self.bottom)

    def as_record(self, width: int, height: int) -> dict:
        x0, y0, x1, y1 = self.rect(width, height)
        return {**asdict(self), "usable_rect": [x0, y0, x1, y1]}
