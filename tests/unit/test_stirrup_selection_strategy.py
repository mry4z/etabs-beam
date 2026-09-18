import pytest
from types import SimpleNamespace

from src.design.detailing import (
    BeamDetailingEngine,
    BeamRegionDemand,
    BeamSection,
    BeamDetailingProfile,
    ProjectDetailingPolicy,
    StirrupDesign,
)


def _section(width: float, *, secondary: bool = False) -> BeamSection:
    return BeamSection(
        f"B{width:g}",
        width,
        800,
        40,
        25,
        420,
        420,
        25,
        7700,
        BeamDetailingProfile.NON_SWAY,
        beam_role="SB" if secondary else "PB",
    )


def _demand(name: str = "middle", av: float = 1.0, at: float = 0.0) -> BeamRegionDemand:
    return BeamRegionDemand(name, 0, 1200, 1700, av, at, 0)


@pytest.mark.parametrize(
    ("width", "legs"),
    [(399, 2), (400, 4), (500, 4), (549, 4), (550, 6)],
)
def test_width_bands_start_with_required_preferred_legs(width: int, legs: int) -> None:
    selected = BeamDetailingEngine()._select_stirrup(_section(width), _demand(av=0.5), 25)
    assert selected.legs == legs


def test_spacing_can_decrease_without_escalating_diameter_or_legs() -> None:
    selected = BeamDetailingEngine()._select_stirrup(_section(400), _demand(av=2.0), 25)
    assert selected.diameter_mm == 10
    assert selected.legs == 4
    assert selected.spacing_mm == 150


def test_spacing_failure_changes_diameter_before_preferred_legs() -> None:
    selected = BeamDetailingEngine()._select_stirrup(_section(400), _demand(av=4.0), 25)
    assert selected.diameter_mm == 12
    assert selected.legs == 4
    assert selected.spacing_mm in (100, 110, 130, 150, 170, 200)


def test_all_diameter_spacing_pairs_fail_closed_into_extra_legs() -> None:
    selected = BeamDetailingEngine()._select_stirrup(_section(400), _demand(av=20.0), 25)
    assert selected.legs > 4
    assert selected.spacing_mm in (100, 110, 130, 150, 170, 200)


def _controlled_fallback_engine() -> BeamDetailingEngine:
    """Use synthetic catalogue areas to distinguish fallback sort keys."""

    class Catalog:
        def area(self, diameter: int) -> float:
            # Keep both diameters in the same fallback leg tier.  A
            # spacing-first fallback would choose @200 with ten legs, while
            # the required ordering must keep the six-leg @100 candidate.
            return 100.0

    engine = BeamDetailingEngine()
    engine.project = SimpleNamespace(
        catalog=Catalog(),
        allowed_spacings_for=lambda section, region: (100, 200),
        ordered_stirrup_diameters=lambda: (10, 12),
        minimum_stirrup_diameter_for=lambda section: 10,
        preferred_stirrup_legs=lambda section: 4,
        smf_end_max_spacing_mm=100,
    )
    engine.code = SimpleNamespace(
        minimum_transverse_area_per_spacing=lambda section: 0.0,
        maximum_stirrup_spacing=lambda *args, **kwargs: (1000.0, "TEST", (), False),
        minimum_legs=lambda section, region: 2,
    )
    return engine


def test_stirrup_fallback_minimizes_legs_then_diameter_then_spacing() -> None:
    engine = _controlled_fallback_engine()
    selected = engine._select_stirrup(_section(400), _demand(av=5.0), 25)
    assert (selected.legs, selected.diameter_mm, selected.spacing_mm) == (6, 10, 100)


def test_secondary_fallback_uses_same_legs_diameter_spacing_order() -> None:
    engine = _controlled_fallback_engine()
    selected = engine._select_secondary_span_stirrup(
        _section(400, secondary=True),
        (_demand("left", 5.0), _demand("middle", 5.0), _demand("right", 5.0)),
        (25, 25, 25),
    )
    assert (selected.legs, selected.diameter_mm, selected.spacing_mm) == (6, 10, 100)


def test_row_solver_preference_uses_diameter_before_spacing() -> None:
    engine = BeamDetailingEngine()

    def candidate(diameter: int, spacing: int) -> StirrupDesign:
        return StirrupDesign(
            diameter,
            spacing,
            4,
            1.0,
            1.0,
            1000.0,
            "TEST",
        )

    selected = engine._preferred_stirrup(
        _section(400),
        ((12, 200, candidate(12, 200)), (10, 100, candidate(10, 100))),
    )
    assert (selected.diameter_mm, selected.spacing_mm) == (10, 100)


def test_secondary_uses_one_spacing_diameter_and_leg_specification() -> None:
    section = _section(500, secondary=True)
    selected = BeamDetailingEngine()._select_secondary_span_stirrup(
        section,
        (_demand("left", 2.0), _demand("middle", 0.4), _demand("right", 1.5)),
        (25, 25, 25),
    )
    assert selected.legs == 4
    assert selected.diameter_mm == 10
    assert selected.spacing_mm == 150


def test_policy_defaults_and_metadata_strategy_are_explicit() -> None:
    policy = ProjectDetailingPolicy()
    assert policy.allowed_spacings_mm == (100, 110, 130, 150, 170, 200)
    assert policy.smf_allowed_spacings_mm == (100, 110, 130, 150, 170, 200)
    assert "SPACING_THEN_DIAMETER_THEN_LEGS" in policy.version
