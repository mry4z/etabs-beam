from pathlib import Path

from src.core.beam_line_span_analyzer import BeamLine, BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.core.model_writeback import FrameState, RenameAction, WritebackPlan
from src.core.topology_audit import build_requires_split_records, write_requires_split_audit
from src.drawing.dxf_export import export_requires_split_dxf


def _beam() -> BeamGeometry:
    return BeamGeometry("302", "302", "F1", "P1", "P3", (0, 0, 0), (20, 0, 0), "B", 600)


def _result() -> BeamLineSpanResult:
    line = BeamLine("F1", "PB", 1, "B", ("302",), ((0, 0, 0), (20, 0, 0)), False)
    spans = (
        BeamSpan("F1", "PB", 1, 1, ("302",), ((0, 0, 0), (10, 0, 0)), False, False),
        BeamSpan("F1", "PB", 1, 2, ("302",), ((10, 0, 0), (20, 0, 0)), False, False),
    )
    return BeamLineSpanResult((line,), spans, ())


def test_requires_split_audit_collapses_duplicate_actions_and_writes_outputs(tmp_path: Path) -> None:
    state = FrameState("302", "guid-302", "P1", "P3", "302", "F1", "B")
    plan = WritebackPlan(
        renames=(
            RenameAction("302", "guid-302", "", "F1-PB1-1", 1, "REQUIRES_SPLIT", "multi-span"),
            RenameAction("302", "guid-302", "", "F1-PB1-2", 1, "REQUIRES_SPLIT", "multi-span"),
        )
    )
    records = build_requires_split_records(_result(), [_beam()], {"302": state}, plan)
    assert len(records) == 1
    assert records[0].span_ids == ("F1-PB1-1", "F1-PB1-2")
    json_path, csv_path = write_requires_split_audit(records, tmp_path, "requires_split")
    dxf_path = export_requires_split_dxf(records, tmp_path / "requires_split.dxf")
    assert json_path.is_file() and csv_path.is_file() and dxf_path.is_file()
    assert "AUDIT_REQUIRES_SPLIT" in dxf_path.read_text(encoding="ascii")


def test_requires_split_audit_excludes_section_review_actions() -> None:
    state = FrameState("302", "guid-302", "P1", "P3", "302", "F1", "B")
    plan = WritebackPlan(
        renames=(
            RenameAction(
                "302",
                "guid-302",
                "",
                "F1-PB1-1",
                1,
                "REQUIRES_SECTION_REVIEW",
                "mixed sections",
            ),
        )
    )

    assert build_requires_split_records(_result(), [_beam()], {"302": state}, plan) == ()
