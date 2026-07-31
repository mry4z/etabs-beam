import pytest

from src.design.bar_layout_capacity import (
    ACI318_14_SINGLE_LAYER_MAX_BARS,
    BEAM_WIDTHS_MM,
    CLEAR_COVERS_TO_STIRRUP_OUTER_MM,
    LONGITUDINAL_BAR_DIAMETERS_MM,
    MAX_AGGREGATE_SIZES_MM,
    STIRRUP_DIAMETERS_MM,
    calculate_single_layer_max_bars,
    minimum_clear_bar_spacing_mm,
    single_layer_max_bars,
)
from src.design.detailing import BeamDetailingEngine, BeamSection


def test_lookup_contains_every_standard_combination() -> None:
    assert (
        len(ACI318_14_SINGLE_LAYER_MAX_BARS)
        == (
            len(BEAM_WIDTHS_MM)
            * len(CLEAR_COVERS_TO_STIRRUP_OUTER_MM)
            * len(STIRRUP_DIAMETERS_MM)
            * len(MAX_AGGREGATE_SIZES_MM)
            * len(LONGITUDINAL_BAR_DIAMETERS_MM)
        )
        == 720
    )


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ((500, 40, 10, 20, 10), 11),
        ((500, 40, 10, 20, 12), 11),
        ((500, 40, 10, 20, 16), 10),
        ((500, 40, 10, 20, 20), 9),
        ((500, 40, 10, 20, 25), 8),
        ((250, 60, 12, 25, 25), 2),
        ((200, 75, 16, 20, 16), 1),
        ((200, 75, 16, 20, 20), 0),
    ],
)
def test_selected_capacity_values(key: tuple[int, int, int, int, int], expected: int) -> None:
    assert ACI318_14_SINGLE_LAYER_MAX_BARS[key] == expected
    assert single_layer_max_bars(*key) == expected


def test_every_cached_value_matches_formula() -> None:
    for key, cached in ACI318_14_SINGLE_LAYER_MAX_BARS.items():
        assert cached == calculate_single_layer_max_bars(*key)


def test_aci_minimum_clear_spacing_controls() -> None:
    assert minimum_clear_bar_spacing_mm(10, 20) == pytest.approx(80 / 3)
    assert minimum_clear_bar_spacing_mm(25, 20) == pytest.approx(80 / 3)
    assert minimum_clear_bar_spacing_mm(40, 20) == 40


def test_nonstandard_combination_uses_formula_fallback() -> None:
    assert single_layer_max_bars(450, 45, 10, 20, 25) == (calculate_single_layer_max_bars(450, 45, 10, 20, 25))


def test_detailing_engine_uses_shared_lookup() -> None:
    section = BeamSection(
        name="B500x800",
        width_mm=500,
        depth_mm=800,
        clear_cover_to_stirrup_outer_mm=40,
        max_aggregate_size_mm=20,
        fc_mpa=30,
        fy_mpa=420,
        fyt_mpa=420,
        length_mm=6000,
    )
    assert BeamDetailingEngine()._capacity_per_layer(section, 10, 25) == 8


def test_etabs_centroid_cover_controls_effective_depth_and_layout() -> None:
    section = BeamSection(
        name="B300x600",
        width_mm=300,
        depth_mm=600,
        clear_cover_to_stirrup_outer_mm=None,
        max_aggregate_size_mm=25,
        fc_mpa=30,
        fy_mpa=420,
        fyt_mpa=420,
        length_mm=6000,
        etabs_longitudinal_cover_mm=50,
        etabs_longitudinal_cover_source="ETABS GetRebarBeam fixture",
    )

    assert section.cover_calculation_basis == "ETABS_LONGITUDINAL_CENTROID"
    assert section.effective_depth_mm(10, 25) == 550
    assert section.clear_cover_for_layout_mm(10, 25) == 27.5
    assert section.stirrup_centerline_cover_mm(10, 25) == 32.5
    assert BeamDetailingEngine()._capacity_per_layer(section, 10, 25) == 4


@pytest.mark.parametrize(
    "values",
    [
        (0, 40, 10, 20, 25),
        (500, 0, 10, 20, 25),
        (500, 40, 0, 20, 25),
        (500, 40, 10, 0, 25),
        (500, 40, 10, 20, 0),
    ],
)
def test_nonpositive_dimensions_are_rejected(values: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        calculate_single_layer_max_bars(*values)
