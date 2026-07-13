from pathlib import Path

from src.core.geometry_extractor import BeamGeometry
from src.core.merge_comparison import compare_merge_angles
from src.drawing.dxf_export import (
    export_beam_groups_csv,
    export_beam_groups_dxf,
    export_merge_angle_csv,
    export_merge_angle_dxf,
)


def test_export_merge_comparison_dxf_contains_colored_entities(tmp_path: Path) -> None:
    beams = [
        BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (10, 0, 0), "B", 600),
        BeamGeometry("B2", "B2", "L1", "P2", "P3", (10, 0, 0), (20, 2, 0), "B", 600),
    ]
    differences = compare_merge_angles(beams, 3, 15)
    path = export_merge_angle_dxf(differences, tmp_path / "check.dxf")
    content = path.read_text(encoding="ascii")
    assert "MERGE15_NEW" in content
    assert "MERGE3_GROUP" in content
    assert "15D-001" in content
    assert content.endswith("0\nEOF\n")
    csv_path = export_merge_angle_csv(differences, tmp_path / "check.csv")
    assert "15D-001" in csv_path.read_text(encoding="utf-8-sig")


def test_export_all_beam_groups_uses_one_polyline_and_primary_colors(tmp_path: Path) -> None:
    source = [
        BeamGeometry("B1", "B1", "F1", "C1", "P2", (0, 0, 0), (10, 0, 0), "B", 600),
        BeamGeometry("B2", "B2", "F1", "P2", "P3", (10, 0, 0), (20, 1, 0), "B", 600),
    ]
    logical = BeamGeometry(
        "M", "M", "F1", "C1", "P3", (0, 0, 0), (20, 1, 0), "B", 600,
        source_names=("B1", "B2"), path_points=((0, 0, 0), (10, 0, 0), (20, 1, 0)),
    )
    path = export_beam_groups_dxf([logical], source, {"C1"}, tmp_path / "all.dxf")
    content = path.read_text(encoding="ascii")
    assert content.count("\nPOLYLINE\n") == 1
    assert "BEAM_PRIMARY" in content
    assert "F1-PB1-S1" in content
    csv_path = export_beam_groups_csv([logical], source, {"C1"}, tmp_path / "all.csv")
    assert "F1-PB1-S1" in csv_path.read_text(encoding="utf-8-sig")
