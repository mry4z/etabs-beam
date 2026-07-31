from pathlib import Path

import pytest

from src.config import (
    DEFAULT_MAX_AGGREGATE_SIZE_MM,
    DEFAULT_MAX_AGGREGATE_SIZE_SOURCE,
    DesignSettings,
    load_config,
)
from src.utils.io import available_output_path


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


def test_design_settings_default_to_project_25_mm_aggregate() -> None:
    settings = DesignSettings()

    assert settings.max_aggregate_size_mm == DEFAULT_MAX_AGGREGATE_SIZE_MM
    assert settings.max_aggregate_size_source == DEFAULT_MAX_AGGREGATE_SIZE_SOURCE


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
