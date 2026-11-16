from pathlib import Path

import pytest

from src.config import (
    DEFAULT_MAX_AGGREGATE_SIZE_MM,
    DEFAULT_MAX_AGGREGATE_SIZE_SOURCE,
    DesignSettings,
    load_config,
)
from src.utils.io import available_output_path


@pytest.mark.parametrize("cpu_count, expected", [(None, 1), (1, 1), (2, 1), (8, 7), (128, 127)])
@pytest.mark.parametrize("configured", [None, "auto"])
def test_rebar_workers_auto_resolves_logical_cpu_minus_one(tmp_path, monkeypatch, cpu_count, expected, configured):
    monkeypatch.setattr("src.config.os.cpu_count", lambda: cpu_count)
    path = tmp_path / "auto.yaml"
    text = "etabs:\n  dll_path: test.dll\n"
    if configured is not None:
        text += "design:\n  rebar_workers: auto\n"
    path.write_text(text, encoding="utf-8")
    workers = load_config(path).design.rebar_workers
    assert workers == expected
    assert isinstance(workers, int)


@pytest.mark.parametrize("configured, override, expected", [(1, None, 1), (4, None, 4), (64, None, 64), ("auto", 4, 4), (4, "auto", 7)])
def test_rebar_workers_integer_and_cli_override_preserved(tmp_path, monkeypatch, configured, override, expected):
    monkeypatch.setattr("src.config.os.cpu_count", lambda: 8)
    path = tmp_path / "workers.yaml"
    path.write_text(f"etabs:\n  dll_path: test.dll\ndesign:\n  rebar_workers: {configured}\n", encoding="utf-8")
    assert load_config(path, rebar_workers=override).design.rebar_workers == expected


@pytest.mark.parametrize("invalid", [0, -1, 65, "bad", "4.5"])
def test_rebar_workers_rejects_invalid_explicit_settings(tmp_path, invalid):
    path = tmp_path / "workers.yaml"
    path.write_text("etabs:\n  dll_path: test.dll\n", encoding="utf-8")
    with pytest.raises(ValueError, match="auto or an integer from 1 to 64"):
        load_config(path, rebar_workers=invalid)


def test_design_settings_direct_default_remains_serial():
    assert DesignSettings().rebar_workers == 1


def test_config_normalizes_n_mm_c_export_unit(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: test.dll\ngeometry:\n  unit_system: N-mm-C\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.geometry.export_unit == "N_mm_C"
    assert config.design.standard == "ACI 318-14"
    assert config.design.clear_cover_to_stirrup_outer_mm is None
    assert config.design.max_aggregate_size_mm == 25
    assert config.design.max_aggregate_size_source == DEFAULT_MAX_AGGREGATE_SIZE_SOURCE
    assert config.geometry.source_span_overlap_tolerance_mm == 2.0


def test_config_accepts_host_api_path_override(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: machine-specific.dll\ngeometry:\n  unit_system: N_mm_C\n",
        encoding="utf-8",
    )
    host_api = tmp_path / "ETABSv1.dll"

    config = load_config(config_path, etabs_dll_path=host_api)

    assert config.etabs.dll_path == host_api


def test_config_resolves_relative_paths_from_config_directory(tmp_path: Path) -> None:
    config_directory = tmp_path / "portable"
    config_directory.mkdir()
    config_path = config_directory / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: ETABSv1.dll\n" "output:\n  reports_dir: outputs/reports\n  logs_dir: outputs/logs\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.etabs.dll_path == config_directory / "ETABSv1.dll"
    assert config.output.reports_dir == config_directory / "outputs" / "reports"
    assert config.output.logs_dir == config_directory / "outputs" / "logs"


def test_candidate_lookup_defaults_to_auto_for_drawing_and_preserves_explicit_off(tmp_path: Path) -> None:
    config_path = tmp_path / "drawing.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: ETABSv1.dll\n"
        "design:\n  detailing_policy: drawing_20260924_v1\n",
        encoding="utf-8",
    )

    assert load_config(config_path).design.candidate_lookup == "auto"
    assert load_config(config_path, candidate_lookup="off").design.candidate_lookup == "off"
    explicit_off = tmp_path / "drawing-off.yaml"
    explicit_off.write_text(
        'etabs:\n  dll_path: ETABSv1.dll\n'
        'design:\n  detailing_policy: drawing_20260924_v1\n  candidate_lookup: "off"\n',
        encoding="utf-8",
    )
    assert load_config(explicit_off).design.candidate_lookup == "off"


def test_layout_dictionary_path_is_optional_and_resolves_from_config_or_cli(tmp_path: Path) -> None:
    config_directory = tmp_path / "portable"
    config_directory.mkdir()
    config_path = config_directory / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: ETABSv1.dll\n"
        "design:\n  detailing_policy: drawing_20260924_v1\n"
        "  layout_dictionary_path: dictionaries/bank.json\n"
        "  rebar_workers: 3\n",
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.design.layout_dictionary_path == config_directory / "dictionaries" / "bank.json"
    assert config.design.candidate_lookup == "off"
    assert config.design.rebar_workers == 3
    cli_path = tmp_path / "cli-bank.json"
    overridden = load_config(config_path, layout_dictionary_path=cli_path, rebar_workers=4)
    assert overridden.design.layout_dictionary_path == cli_path.resolve()
    assert overridden.design.candidate_lookup == "off"
    assert overridden.design.rebar_workers == 4


def test_design_settings_default_to_project_25_mm_aggregate() -> None:
    settings = DesignSettings()

    assert settings.max_aggregate_size_mm == DEFAULT_MAX_AGGREGATE_SIZE_MM
    assert settings.max_aggregate_size_source == DEFAULT_MAX_AGGREGATE_SIZE_SOURCE
    assert settings.candidate_lookup == "auto"


def test_checked_in_project_config_uses_nasiriyah_measure_geometry_inputs() -> None:
    project_root = Path(__file__).resolve().parents[2]
    config = load_config(project_root / "config.example.yaml")

    assert config.design.clear_cover_to_stirrup_outer_mm == pytest.approx(40.0)
    assert "Nasiriyah SMF/SB beam measure" in config.design.clear_cover_source
    assert config.design.max_aggregate_size_mm == pytest.approx(25.0)
    assert "Nasiriyah SMF/SB beam measure" in config.design.max_aggregate_size_source
    assert config.design.candidate_lookup == "auto"
    portable = load_config(project_root / "plugins" / "etabs" / "portable" / "config.yaml")
    assert portable.design.candidate_lookup == "auto"


def test_config_rejects_non_n_mm_c_export_unit(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: test.dll\ngeometry:\n  unit_system: kN_m_C\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="N_mm_C"):
        load_config(config_path)


def test_config_rejects_unsupported_design_standard(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: test.dll\ndesign:\n  standard: ACI 318-19\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ACI 318-14"):
        load_config(config_path)


def test_config_requires_sources_for_confirmed_detailing_inputs(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: test.dll\n"
        "design:\n"
        "  clear_cover_to_stirrup_outer_mm: 40\n"
        "  max_aggregate_size_mm: 20\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="clear_cover_source"):
        load_config(config_path)


def test_config_preserves_separate_confirmed_detailing_inputs(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: test.dll\n"
        "design:\n"
        "  clear_cover_to_stirrup_outer_mm: 40\n"
        "  clear_cover_source: structural-general-notes\n"
        "  max_aggregate_size_mm: 20\n"
        "  max_aggregate_size_source: concrete-mix-submittal\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.design.clear_cover_to_stirrup_outer_mm == 40
    assert config.design.clear_cover_source == "structural-general-notes"
    assert config.design.max_aggregate_size_mm == 20
    assert config.design.max_aggregate_size_source == "concrete-mix-submittal"


def test_config_requires_source_for_explicit_aggregate_override(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: test.dll\n" "design:\n" "  max_aggregate_size_mm: 20\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_aggregate_size_source"):
        load_config(config_path)


def test_config_rejects_aggregate_source_without_selected_size(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "etabs:\n  dll_path: test.dll\n" "design:\n" "  max_aggregate_size_source: plugin-selection\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="缺少对应"):
        load_config(config_path)


def test_available_output_path_increments_suffix(tmp_path: Path) -> None:
    (tmp_path / "report.xlsx").touch()
    (tmp_path / "report_01.xlsx").touch()
    assert available_output_path(tmp_path, "report.xlsx").name == "report_02.xlsx"
