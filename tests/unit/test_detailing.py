import pytest
import sys
from types import SimpleNamespace

from src.design.detailing import (
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamRegionDemand,
    BeamSection,
    ContinuityOption,
    DetailingError,
    FaceRebarMode,
    ProjectDetailingPolicy,
    ReinforcementMode,
    coordinate_adjacent_spans,
)
from src.design.etabs_result import EtabsResultProvider


def _input(
    name: str, width: float, depth: float, demands, *, smf: bool = True, length: float = 6000
) -> BeamDetailingInput:
    section = BeamSection(name, width, depth, 40, 35, 420, 420, 20, length, smf)
    return BeamDetailingInput(section, tuple(demands))


def _demand(name: str, top: float, bottom: float, av: float, at: float, al: float) -> BeamRegionDemand:
    return BeamRegionDemand(name, {"left": 0, "middle": 3000, "right": 6000}[name], top, bottom, av, at, al)


def test_project_policy_rejects_diameter_outside_full_catalog() -> None:
    with pytest.raises(DetailingError):
        ProjectDetailingPolicy(enabled_diameters_mm=(10, 13))


def test_station_envelope_can_override_asflex_from_same_station_calculation() -> None:
    demand = BeamRegionDemand("left", 0, 2000, 1500, 0.5, 0.1, 800, as_flex_top_override_mm2=1750)
    assert demand.as_flex_top_mm2 == 1750
    assert demand.as_flex_bottom_mm2 == 1100


def test_case_one_selects_stirrups_first_and_keeps_shallow_face_na() -> None:
    data = _input(
        "CASE-1",
        500,
        700,
        (
            _demand("left", 2750, 1800, 1.20, 0.40, 800),
            _demand("middle", 1300, 2200, 0.75, 0.00, 0),
            _demand("right", 2850, 1800, 1.10, 0.35, 800),
        ),
    )
    result = BeamDetailingEngine().design(data)
    assert result.mode is ReinforcementMode.NORMAL
    assert result.face_rebar.mode is FaceRebarMode.NOT_APPLICABLE
    assert all(region.stirrup.legs == 4 for region in result.regions)
    assert result.regions[0].stirrup.notation == "T10@150"
    assert result.regions[1].stirrup.notation == "T10@200"
    assert result.regions[0].top.provided_area_mm2 >= 2750
    assert result.regions[1].bottom.provided_area_mm2 >= 2200


def test_multilayer_longitudinal_layout_never_leaves_one_bar_in_last_layer() -> None:
    data = _input(
        "PRACTICAL-LAYERS",
        350,
        700,
        (
            _demand("left", 2700, 1000, 0.20, 0.00, 0),
            _demand("middle", 2700, 1000, 0.20, 0.00, 0),
            _demand("right", 2700, 1000, 0.20, 0.00, 0),
        ),
    )
    result = BeamDetailingEngine().design(data)
    assert result.regions[0].top.notation == "6T25 4/2"
    assert result.regions[0].top.bars[0].layers == (4, 2)
    assert all(value >= 2 for value in result.regions[0].top.bars[0].layers)


def test_case_two_triggers_span_wide_torsion_face_mode() -> None:
    data = _input(
        "CASE-2",
        450,
        1000,
        (
            _demand("left", 7500, 6000, 2.30, 1.00, 4000),
            _demand("middle", 4800, 6000, 1.00, 0.55, 2600),
            _demand("right", 7000, 5600, 2.10, 0.90, 3300),
        ),
    )
    result = BeamDetailingEngine().design(data)
    assert result.mode is ReinforcementMode.FACE_TORSION
    assert result.face_rebar.mode is FaceRebarMode.TORSION_AND_SKIN
    assert result.face_rebar.notation == "7T20"
    assert result.face_rebar.provided_each_side_mm2 >= 2000
    for region in result.regions:
        assert region.top.provided_area_mm2 >= region.as_flex_top_mm2
        assert region.bottom.provided_area_mm2 >= region.as_flex_bottom_mm2
        assert region.stirrup.legs == 4


def test_deep_non_torsion_beam_gets_default_t10_skin_bars() -> None:
    data = _input(
        "DEEP-SKIN",
        500,
        1000,
        (
            _demand("left", 1000, 1000, 0.20, 0.00, 0),
            _demand("middle", 1000, 1000, 0.20, 0.00, 0),
            _demand("right", 1000, 1000, 0.20, 0.00, 0),
        ),
        smf=False,
    )
    result = BeamDetailingEngine().design(data)
    assert result.face_rebar.mode is FaceRebarMode.SKIN
    assert result.face_rebar.notation == "3T10"


def test_adjacent_span_option_coordinates_shared_support_only() -> None:
    first = BeamDetailingEngine().design(
        _input(
            "SPAN-1",
            500,
            700,
            (
                _demand("left", 1000, 1000, 0.2, 0, 0),
                _demand("middle", 1000, 1000, 0.2, 0, 0),
                _demand("right", 2800, 1000, 0.2, 0, 0),
            ),
        )
    )
    second = BeamDetailingEngine().design(
        _input(
            "SPAN-2",
            500,
            700,
            (
                _demand("left", 1400, 1000, 0.2, 0, 0),
                _demand("middle", 1000, 1000, 0.2, 0, 0),
                _demand("right", 1000, 1000, 0.2, 0, 0),
            ),
        )
    )
    first_out, second_out = coordinate_adjacent_spans(first, second, ContinuityOption.OPTION_1_STRICT_EQUAL)
    assert first_out.regions[2].top.bars[0].count == second_out.regions[0].top.bars[0].count
    assert first_out.regions[1].top.bars[0].count == first.regions[1].top.bars[0].count


def test_etabs23_total_result_adapter_preserves_total_min_and_provided(monkeypatch) -> None:
    fake_module = SimpleNamespace(eItemType=SimpleNamespace(Objects="Objects"))
    monkeypatch.setitem(sys.modules, "ETABSv1", fake_module)

    arrays = (
        0,
        1,
        ["B1"],
        [100.0],
        ["TOP"],
        [1200.0],
        [1100.0],
        [900.0],
        [-1.0],
        ["BOT"],
        [800.0],
        [700.0],
        [600.0],
        [-1.0],
        ["V"],
        [1.2],
        [1.0],
        [0.8],
        [-1.0],
        ["TL"],
        [200.0],
        ["TT"],
        [0.4],
        [""],
        [""],
    )

    class FakeDesign:
        def GetSummaryResultsBeam_2(self, *args):
            return arrays

    result = EtabsResultProvider(SimpleNamespace(DesignConcrete=FakeDesign())).get_beam_design_total("B1")
    station = result.stations[0]
    assert station.as_total_top_mm2 == 1200
    assert station.as_total_bottom_mm2 == 800
    assert station.as_flex_top_mm2 == 1100
    assert station.top_min_area == 900
    assert station.top_provided_area == -1
