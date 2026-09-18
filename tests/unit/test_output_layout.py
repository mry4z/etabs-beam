from pathlib import Path

from src.output_layout import resolve_output_layout, sanitize_model_name


def test_model_name_is_windows_safe_and_layout_is_idempotent(tmp_path: Path) -> None:
    model = tmp_path / "Tower:North?.EDB"
    layout = resolve_output_layout(tmp_path / "outputs", model)
    assert layout.model_name == "Tower_North_"
    assert layout.root.name == "Tower_North__beam_output"
    assert layout.data == layout.root / "_data"
    assert layout.primary_schedule.name == "Tower_North__primarybeam_schedule.xlsx"
    assert layout.secondary_schedule.name == "Tower_North__secondarybeam_schedule.xlsx"
    assert layout.span_note.name == "Tower_North__span_note.dxf"
    same = resolve_output_layout(layout.root, model)
    assert same == layout


def test_snapshot_equivalent_source_uses_stem_without_extra_output_directory(tmp_path: Path) -> None:
    model = tmp_path / "working.EDB"
    layout = resolve_output_layout(tmp_path / "parent", model)
    assert layout.root == tmp_path / "parent" / "working_beam_output"
    assert resolve_output_layout(layout.root, model).root == layout.root
    assert sanitize_model_name("CON.EDB") == "_CON"
