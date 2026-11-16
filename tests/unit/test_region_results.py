from dataclasses import replace

from src.design.provider import BeamDesignResult, DesignStation
from src.design.region_results import (
    DRAWING_DETAILING_POLICY_ID,
    DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE,
    V3_DETAILING_POLICY_ID,
    build_region_controls,
)


def _station(location: float, top: float, top_min: float | None = 0.0) -> DesignStation:
    return DesignStation(
        location,
        "T",
        top,
        "B",
        100.0,
        "V",
        0.1,
        "TL",
        10.0,
        "TV",
        0.02,
        top_total_area=top,
        top_min_area=top_min,
        bottom_total_area=100.0,
    )


def test_drawing_upper_middle_top_uses_inclusive_parent_quarter_and_keeps_duplicates() -> None:
    design = BeamDesignResult(
        "part",
        (
            _station(49.0, 900.0),
            _station(50.0, 20.0, 60.0),
            _station(50.0, 25.0, 25.0),
            _station(250.0, 40.0, 35.0),
            _station(251.0, 800.0),
        ),
    )

    controls = build_region_controls(
        design,
        300.0,
        80.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=800.0,
        segment_start_mm=250.0,
    )

    assert controls is not None
    middle = controls["middle"]
    assert middle["middle_top_window_start_parent_mm"] == 300.0
    assert middle["middle_top_window_end_parent_mm"] == 500.0
    assert middle["middle_top_window_station_count"] == 3
    assert middle["top_total_mm2"] == 40.0
    assert middle["top_total_station_mm"] == 250.0
    assert middle["as_min_top_mm2"] == 60.0
    assert middle["top_demand_mm2"] == 60.0
    assert middle["top_demand_station_mm"] == 50.0
    assert middle["top_demand_source"] == "ETABS_TOP_AS_MIN"
    assert middle["top_flex_mm2"] == 35.0


def test_drawing_quarter_includes_duplicate_upper_boundary_with_floating_roundoff() -> None:
    parent_length = 7802.563681252465
    segment_start = 1000.0
    upper = parent_length * 5.0 / 8.0
    local_location = 4876.602300782793 - segment_start
    design = BeamDesignResult(
        "part",
        (
            _station(local_location, 100.0),
            _station(local_location, 200.0),
        ),
    )

    controls = build_region_controls(
        design,
        parent_length - segment_start,
        80.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=parent_length,
        segment_start_mm=segment_start,
    )

    assert controls is not None
    middle = controls["middle"]
    assert upper == 4876.602300782791
    assert segment_start + local_location == 4876.602300782793
    assert middle["middle_top_window_end_parent_mm"] == upper
    assert middle["middle_top_window_station_count"] == 2
    assert middle["top_total_mm2"] == 200.0


def test_drawing_upper_middle_top_uses_segment_envelope_outside_parent_quarter() -> None:
    design = BeamDesignResult("part", (_station(0.0, 100.0), _station(100.0, 200.0)))

    controls = build_region_controls(
        design,
        100.0,
        80.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=800.0,
        segment_start_mm=0.0,
    )

    assert controls is not None
    middle = controls["middle"]
    assert middle["top_total_mm2"] == 200.0
    assert middle["top_total_station_mm"] == 100.0
    assert middle["middle_top_window_station_count"] == 2
    assert middle["middle_top_demand_basis"] == "SEGMENT_FULL_RANGE_ENVELOPE"
    assert middle["middle_top_window_start_parent_mm"] is None


def test_drawing_upper_middle_top_still_blocks_empty_intersection() -> None:
    design = BeamDesignResult("part", (_station(0.0, 100.0), _station(400.0, 200.0)))
    controls = build_region_controls(design, 400.0, 80.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=800.0, segment_start_mm=200.0)
    assert controls["middle"]["middle_top_window_station_count"] == 0
    assert controls["middle"]["top_total_mm2"] is None
    assert controls["middle"]["middle_top_demand_basis"] == "PARENT_CENTRAL_WINDOW"


def test_drawing_quarter_invalid_top_total_is_not_masked_by_zero_minimum() -> None:
    design = BeamDesignResult(
        "span",
        (
            _station(0.0, 100.0),
            _station(400.0, -1.0, 0.0),
            _station(800.0, 100.0),
        ),
    )

    controls = build_region_controls(
        design,
        800.0,
        80.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=800.0,
    )

    assert controls is not None
    middle = controls["middle"]
    assert middle["top_total_mm2"] is None
    assert middle["as_min_top_mm2"] == 0.0
    assert middle["top_demand_mm2"] is None
    assert "TopTotal@400.0: -1.0" in middle["middle_top_window_invalid_reason"]


def test_drawing_quarter_marks_nonfinite_and_missing_raw_top_total() -> None:
    nan_station = replace(_station(400.0, 100.0, 0.0), top_total_area=float("nan"))
    missing_station = replace(_station(500.0, 100.0, 0.0), top_total_area=None)
    design = BeamDesignResult(
        "span",
        (_station(0.0, 100.0), nan_station, missing_station, _station(800.0, 100.0)),
    )

    controls = build_region_controls(
        design,
        800.0,
        80.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=800.0,
    )

    assert controls is not None
    middle = controls["middle"]
    assert middle["top_demand_mm2"] is None
    assert "TopTotal@400.0: nan" in middle["middle_top_window_invalid_reason"]
    assert "TopTotal@500.0: None" in middle["middle_top_window_invalid_reason"]


def test_drawing_quarter_keeps_missing_optional_etabs_minimum_explicit() -> None:
    design = BeamDesignResult(
        "span",
        (
            _station(0.0, 100.0),
            _station(400.0, 250.0, None),
            _station(800.0, 100.0),
        ),
    )

    controls = build_region_controls(
        design,
        800.0,
        80.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=800.0,
    )

    assert controls is not None
    middle = controls["middle"]
    assert middle["top_total_mm2"] == 250.0
    assert middle["as_min_top_mm2"] is None
    assert middle["top_demand_mm2"] == 250.0


def test_pb60_research_opt_in_preserves_available_fields_when_parent_middle_is_empty() -> None:
    parent_length = 3937.132845296518
    segment_length = 2161.9935175666014
    stations = (
        _station(400.0, 3602.659724103668, 1181.9584953455621),
        _station(720.6645058555363, 3116.608964919642, 1181.9584953455621),
        _station(720.6645058555363, 3095.7877798722993, 1181.9584953455621),
        _station(1280.9967587832994, 2310.651221156021, 1181.9584953455621),
        _station(1441.329011711065, 2100.746362815889, 1181.9584953455621),
        _station(1441.329011711065, 2080.318622347832, 1181.9584953455621),
        _station(2161.9935175666014, 1231.1292156531722, 901.1735223451125),
    )
    design = BeamDesignResult("GF-FG-PB60-1.1", stations)

    assert build_region_controls(
        design,
        segment_length,
        950.0,
        detailing_policy_id=V3_DETAILING_POLICY_ID,
        parent_length_mm=parent_length,
        parent_end_depths_mm=(950.0, 950.0),
    ) is None

    controls = build_region_controls(
        design,
        segment_length,
        950.0,
        detailing_policy_id=V3_DETAILING_POLICY_ID,
        parent_length_mm=parent_length,
        parent_end_depths_mm=(950.0, 950.0),
        projection_mode=DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE,
    )

    assert controls is not None
    assert controls["left"]["source_station_count"] == 6
    assert controls["right"]["source_station_count"] == 1
    middle = controls["middle"]
    assert middle["region_evidence"]["status"] == "UNKNOWN"
    assert middle["region_evidence"]["missing_reason"] == "NO_STATION_IN_PARENT_2H_REGION"
    assert middle["region_evidence"]["station_count"] == 0
    assert middle["middle_top_window_station_count"] == 1
    assert middle["middle_top_window_station_count"] == len(stations[6:])
    assert middle["top_total_mm2"] == stations[6].top_total_area
    assert middle["top_total_station_mm"] == stations[6].location
    assert middle["field_evidence"]["top_total_mm2"]["basis"] == "PARENT_CENTRAL_WINDOW"
    assert middle["field_evidence"]["top_total_mm2"]["station_count"] == 1
    for field in ("bottom_total_mm2", "av_over_s", "al_mm2", "at_over_s"):
        assert middle[field] is None
        assert middle["field_evidence"][field]["status"] == "UNKNOWN"
        assert middle["field_evidence"][field]["missing_reason"] == "NO_STATION_IN_PARENT_2H_REGION"


def test_research_evidence_marks_nonintersecting_parent_regions_as_segment_envelopes() -> None:
    design = BeamDesignResult("part", (_station(100.0, 60.0),))

    controls = build_region_controls(
        design,
        200.0,
        950.0,
        detailing_policy_id=V3_DETAILING_POLICY_ID,
        parent_length_mm=3937.132845296518,
        segment_start_mm=2200.0,
        parent_end_depths_mm=(950.0, 950.0),
        projection_mode=DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE,
    )

    assert controls is not None
    assert controls["left"]["top_total_mm2"] == 60.0
    assert controls["middle"]["bottom_total_mm2"] == 100.0
    for region in ("left", "middle"):
        evidence = controls[region]["region_evidence"]
        assert evidence["basis"] == "SEGMENT_FULL_RANGE_ENVELOPE"
        assert evidence["basis_reason"] == "REGION_NOT_INTERSECTING_SEGMENT_FULL_RANGE_ENVELOPE"


def test_partial_research_zero_width_local_middle_uses_full_span_but_quarter_top_stays_strict() -> None:
    stations = (
        replace(_station(0.0, 20.0), bottom_total_area=110.0),
        replace(_station(1000.0, 30.0), bottom_total_area=220.0),
    )
    design = BeamDesignResult("short-span", stations)

    partial = build_region_controls(
        design,
        1000.0,
        300.0,
        detailing_policy_id=V3_DETAILING_POLICY_ID,
        parent_length_mm=1000.0,
        projection_mode=DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE,
    )
    legacy = build_region_controls(
        design,
        1000.0,
        300.0,
        detailing_policy_id=V3_DETAILING_POLICY_ID,
        parent_length_mm=1000.0,
    )

    assert partial is not None and legacy is not None
    middle = partial["middle"]
    assert middle["region_evidence"]["geometry_status"] == "NOT_APPLICABLE"
    assert middle["region_evidence"]["geometry_interval_mm"] == [500.0, 500.0]
    assert middle["region_evidence"]["basis"] == "SEGMENT_FULL_RANGE_ENVELOPE"
    assert middle["region_evidence"]["station_count"] == 2
    assert middle["region_evidence"]["station_locations_mm"] == [0.0, 1000.0]
    assert middle["bottom_total_mm2"] == 220.0
    assert middle["bottom_total_station_mm"] == 1000.0
    assert middle["field_evidence"]["bottom_total_mm2"]["basis"] == "SEGMENT_FULL_RANGE_ENVELOPE"
    assert middle["middle_top_window_station_count"] == 0
    assert middle["top_total_mm2"] is None
    assert middle["field_evidence"]["top_total_mm2"]["status"] == "UNKNOWN"
    assert middle["field_evidence"]["top_total_mm2"]["basis"] == "PARENT_CENTRAL_WINDOW"
    assert legacy["middle"]["bottom_total_mm2"] == 110.0
