from src.core.beam_merger import BeamMerger
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation


def _beam(name: str, start: tuple[float, float, float], end: tuple[float, float, float]) -> BeamGeometry:
    return BeamGeometry(name, name, "L1", f"{name}-I", f"{name}-J", start, end, "B300x600", 600)


def _station(location: float, top: float) -> DesignStation:
    return DesignStation(location, "C", top, "C", top, "C", 1, "C", 2, "C", 3)


def test_merges_collinear_segments_and_offsets_design_stations() -> None:
    beams = [_beam("B1", (0, 0, 0), (10, 0, 0)), _beam("B2", (10, 0, 0), (20, 1, 0))]
    designs = {
        "B1": BeamDesignResult("B1", (_station(0, 10), _station(10, 20))),
        "B2": BeamDesignResult("B2", (_station(0, 30), _station(10, 40))),
    }
    result = BeamMerger(angle_limit_deg=10).merge(beams, designs)
    assert len(result.beams) == 1
    merged = result.beams[0]
    assert merged.source_names == ("B1", "B2")
    assert merged.length > 20
    assert [round(station.location, 3) for station in result.designs[merged.name].stations] == [0, 10, 20]


def test_merger_handles_reversed_second_segment() -> None:
    beams = [_beam("B1", (0, 0, 0), (10, 0, 0)), _beam("B2", (20, 0, 0), (10, 0, 0))]
    result = BeamMerger().merge(beams, {})
    assert len(result.beams) == 1
    assert result.beams[0].start == (0, 0, 0)
    assert result.beams[0].end == (20, 0, 0)


def test_merger_keeps_branch_as_original_objects_and_records_warning() -> None:
    beams = [
        _beam("B1", (-10, 0, 0), (0, 0, 0)),
        _beam("B2", (0, 0, 0), (10, 0, 0)),
        _beam("B3", (0, 0, 0), (20, 1, 0)),
    ]
    result = BeamMerger(angle_limit_deg=10).merge(beams, {})
    assert len(result.beams) == 3
    assert any(record.category == "合并拓扑" for record in result.records)


def test_merger_marks_partial_result_when_one_segment_has_no_design_stations() -> None:
    beams = [_beam("B1", (0, 0, 0), (10, 0, 0)), _beam("B2", (10, 0, 0), (20, 0, 0))]
    designs = {
        "B1": BeamDesignResult("B1", (_station(5, 10),)),
        "B2": BeamDesignResult(
            "B2", status=DesignResultStatus.NO_RESULTS, status_message="ETABS 未返回混凝土梁设计站点。"
        ),
    }
    result = BeamMerger().merge(beams, designs)
    assert result.designs[result.beams[0].name].status == DesignResultStatus.PARTIAL
