import pytest

from src.core.geometry_extractor import BeamGeometry
from src.core.merge_comparison import compare_merge_angles


def _beam(name: str, start: tuple[float, float, float], end: tuple[float, float, float]) -> BeamGeometry:
    return BeamGeometry(name, name, "L1", f"{name}-I", f"{name}-J", start, end, "B300x600", 600)


def test_comparison_lists_only_groups_created_by_larger_angle() -> None:
    beams = [_beam("B1", (0, 0, 0), (10, 0, 0)), _beam("B2", (10, 0, 0), (20, 2, 0))]
    differences = compare_merge_angles(beams, 3, 15)
    assert len(differences) == 1
    assert differences[0].source_objects == ("B1", "B2")
    assert differences[0].lower_angle_groups == (("B1",), ("B2",))


def test_comparison_rejects_invalid_angle_order() -> None:
    with pytest.raises(ValueError):
        compare_merge_angles([], 15, 3)
