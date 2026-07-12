from src.core.continuity_analyzer import ContinuityAnalyzer
from src.core.geometry_extractor import BeamGeometry


def _beam(name: str, start: tuple[float, float, float], end: tuple[float, float, float]) -> BeamGeometry:
    return BeamGeometry(name, name, "L2", f"{name}_i", f"{name}_j", start, end, "B300x500")


def test_collinear_touching_beams_form_one_group() -> None:
    beams = [_beam("B1", (0, 0, 3), (6, 0, 3)), _beam("B2", (6, 0, 3), (12, 0, 3))]
    groups = ContinuityAnalyzer().analyze(beams)
    assert len(groups) == 1
    assert groups[0].members == ("B1", "B2")


def test_beams_meeting_at_a_corner_are_not_continuous() -> None:
    beams = [_beam("B1", (0, 0, 3), (6, 0, 3)), _beam("B2", (6, 0, 3), (6, 6, 3))]
    assert len(ContinuityAnalyzer().analyze(beams)) == 2


def test_three_span_chain_is_one_ordered_group() -> None:
    beams = [
        _beam("B1", (0, 0, 3), (6, 0, 3)),
        _beam("B2", (6, 0, 3), (12, 0, 3)),
        _beam("B3", (12, 0, 3), (18, 0, 3)),
    ]
    assert ContinuityAnalyzer().analyze(beams)[0].members == ("B1", "B2", "B3")


def test_same_coordinates_on_different_stories_are_not_continuous() -> None:
    lower = _beam("B1", (0, 0, 3), (6, 0, 3))
    upper = BeamGeometry("B2", "B2", "L3", "P1", "P2", (0, 0, 3), (6, 0, 3), "B300x500")
    assert len(ContinuityAnalyzer().analyze([lower, upper])) == 2


def test_isolated_beam_is_its_own_group() -> None:
    beams = [_beam("B1", (0, 0, 3), (6, 0, 3)), _beam("B2", (20, 0, 3), (26, 0, 3))]
    groups = ContinuityAnalyzer().analyze(beams)
    assert [group.members for group in groups] == [("B1",), ("B2",)]


def test_perpendicular_branch_does_not_join_straight_chain() -> None:
    beams = [
        _beam("B1", (0, 0, 3), (6, 0, 3)),
        _beam("B2", (6, 0, 3), (12, 0, 3)),
        _beam("B3", (6, 0, 3), (6, 6, 3)),
    ]
    groups = ContinuityAnalyzer().analyze(beams)
    assert [group.members for group in groups] == [("B1", "B2"), ("B3",)]


def test_beam_exceeding_slope_limit_is_not_continuous() -> None:
    beams = [_beam("B1", (0, 0, 3), (6, 0, 3)), _beam("B2", (6, 0, 3), (12, 0.2, 3))]
    assert len(ContinuityAnalyzer(slope_limit_deg=1.0).analyze(beams)) == 2
