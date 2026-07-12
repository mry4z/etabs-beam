"""Small, ETABS-independent geometry helpers."""

from __future__ import annotations

from math import acos, degrees, sqrt
from typing import TypeAlias

Point3D: TypeAlias = tuple[float, float, float]


def distance(a: Point3D, b: Point3D) -> float:
    return sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def length(a: Point3D, b: Point3D) -> float:
    return distance(a, b)


def unit_plan_vector(start: Point3D, end: Point3D) -> tuple[float, float] | None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    magnitude = sqrt(dx * dx + dy * dy)
    if magnitude == 0:
        return None
    return dx / magnitude, dy / magnitude


def plan_angle_deg(a_start: Point3D, a_end: Point3D, b_start: Point3D, b_end: Point3D) -> float:
    """Return the smaller unsigned angle between two plan directions (0-90°)."""
    a = unit_plan_vector(a_start, a_end)
    b = unit_plan_vector(b_start, b_end)
    if a is None or b is None:
        return 180.0
    dot = max(-1.0, min(1.0, abs(a[0] * b[0] + a[1] * b[1])))
    return degrees(acos(dot))
