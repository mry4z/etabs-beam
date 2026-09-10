import csv
import re
from pathlib import Path

from src.core.geometry_extractor import BeamGeometry
from src.core.beam_line_span_analyzer import BeamSpan
from src.core.merge_comparison import compare_merge_angles
from src.drawing.dxf_export import (
    export_beam_groups_csv,
    export_beam_groups_dxf,
    export_merge_angle_csv,
    export_merge_angle_dxf,
    export_structural_spans_csv,
    export_structural_spans_dxf,
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
        "M",
        "M",
        "F1",
        "C1",
        "P3",
        (0, 0, 0),
        (20, 1, 0),
        "B",
        600,
        source_names=("B1", "B2"),
        path_points=((0, 0, 0), (10, 0, 0), (20, 1, 0)),
    )
    path = export_beam_groups_dxf([logical], source, {"C1"}, tmp_path / "all.dxf")
    content = path.read_text(encoding="ascii")
    assert content.count("\nPOLYLINE\n") == 1
    assert "BEAM_PRIMARY" in content
    assert "F1-PB1-S1" in content
    csv_path = export_beam_groups_csv([logical], source, {"C1"}, tmp_path / "all.csv")
    assert "F1-PB1-S1" in csv_path.read_text(encoding="utf-8-sig")


def test_beam_group_dxf_and_csv_share_spatial_numbering_for_shuffled_inputs(tmp_path: Path) -> None:
    early = BeamGeometry("Z-BEAM", "Z-BEAM", "F1", "C1", "P2", (0, 0, 0), (50, 0, 0), "B", 600)
    late = BeamGeometry("A-BEAM", "A-BEAM", "F1", "C2", "P3", (100, 0, 0), (200, 0, 0), "B", 600)
    source = [early, late]

    dxf = export_beam_groups_dxf([early, late], source, {"C1", "C2"}, tmp_path / "ordered.dxf")
    csv_path = export_beam_groups_csv([early, late], source, {"C1", "C2"}, tmp_path / "ordered.csv")
    shuffled_dxf = export_beam_groups_dxf([late, early], source, {"C1", "C2"}, tmp_path / "shuffled.dxf")
    shuffled_csv = export_beam_groups_csv([late, early], source, {"C1", "C2"}, tmp_path / "shuffled.csv")

    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    shuffled_rows = list(csv.DictReader(shuffled_csv.read_text(encoding="utf-8-sig").splitlines()))
    row_projection = [(row["梁编号"], row["源对象"], row["路径点坐标"]) for row in rows]
    assert row_projection == [
        ("F1-PB1-S1", "Z-BEAM", "0.000,0.000,0.000;50.000,0.000,0.000"),
        ("F1-PB2-S1", "A-BEAM", "100.000,0.000,0.000;200.000,0.000,0.000"),
    ]
    assert row_projection == [
        (row["梁编号"], row["源对象"], row["路径点坐标"]) for row in shuffled_rows
    ]

    def dxf_projection(path: Path) -> list[tuple[str, str]]:
        blocks = path.read_text(encoding="ascii").split("0\nPOLYLINE\n")[1:]
        return [
            (
                re.search(r"\n1\n(F1-[^\n]+)\n", block).group(1),
                re.search(r"\n10\n([^\n]+)\n20\n([^\n]+)\n30\n", block).group(1)
                + ","
                + re.search(r"\n10\n([^\n]+)\n20\n([^\n]+)\n30\n", block).group(2),
            )
            for block in blocks[:2]
        ]

    assert dxf_projection(dxf) == [("F1-PB1-S1", "0.000,0.000"), ("F1-PB2-S1", "100.000,0.000")]
    assert dxf.read_text(encoding="ascii") == shuffled_dxf.read_text(encoding="ascii")


def test_text_readability_flip_does_not_reorder_west_down_or_folded_path(tmp_path: Path) -> None:
    west = ((10.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    down = ((0.0, 10.0, 0.0), (0.0, 0.0, 0.0))
    folded = ((10.0, 0.0, 0.0), (5.0, 5.0, 0.0), (5.0, 6.0, 0.0))

    cases = ((west, 0.0), (down, 90.0), (folded, -45.0))

    def exported_label_angle_and_vertices(path: Path) -> tuple[float, list[tuple[float, float, float]]]:
        lines = path.read_text(encoding="ascii").splitlines()
        entities: list[tuple[str, dict[str, str]]] = []
        starts = [index for index, value in enumerate(lines[:-1]) if value == "0"]
        for start, next_start in zip(starts, starts[1:] + [len(lines)]):
            kind = lines[start + 1]
            values = lines[start + 2 : next_start]
            fields = {values[index]: values[index + 1] for index in range(0, len(values) - 1, 2)}
            entities.append((kind, fields))
        label = next(fields for kind, fields in entities if kind == "TEXT" and fields.get("1", "").startswith("F1-"))
        vertices = [
            (float(fields["10"]), float(fields["20"]), float(fields["30"]))
            for kind, fields in entities
            if kind == "VERTEX"
        ]
        return float(label["50"]), vertices

    for index, (points, expected_angle) in enumerate(cases, start=1):
        span = BeamSpan("F1", "PB", index, 1, (f"B{index}",), points, False, False)
        output = export_structural_spans_dxf([span], tmp_path / f"direction-{index}.dxf")
        angle, vertices = exported_label_angle_and_vertices(output)
        assert angle == expected_angle
        assert vertices == list(points)
        assert points == span.path_points


def test_structural_span_default_delivery_id_omits_internal_s_and_resolver_wins(tmp_path: Path) -> None:
    span = BeamSpan("F1", "PB", 3, 2, ("B3",), ((0, 0, 0), (10, 0, 0)), False, False)
    default_dxf = export_structural_spans_dxf([span], tmp_path / "default.dxf")
    default_csv = export_structural_spans_csv([span], tmp_path / "default.csv")
    custom_dxf = export_structural_spans_dxf(
        [span], tmp_path / "custom.dxf", span_id_resolver=lambda value: f"DELIVERY-{value.span_index}"
    )
    custom_csv = export_structural_spans_csv(
        [span], tmp_path / "custom.csv", span_id_resolver=lambda value: f"DELIVERY-{value.span_index}"
    )

    assert "F1-PB3-2" in default_dxf.read_text(encoding="ascii")
    assert "F1-PB3-S2" not in default_dxf.read_text(encoding="ascii")
    assert "F1-PB3-2" in default_csv.read_text(encoding="utf-8-sig")
    assert "F1-PB3-S2" not in default_csv.read_text(encoding="utf-8-sig")
    assert "DELIVERY-2" in custom_dxf.read_text(encoding="ascii")
    assert "DELIVERY-2" in custom_csv.read_text(encoding="utf-8-sig")
