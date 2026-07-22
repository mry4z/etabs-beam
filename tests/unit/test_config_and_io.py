from pathlib import Path

import pytest

from src.config import load_config
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
    assert config.geometry.source_span_overlap_tolerance_mm == 2.0


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


def test_available_output_path_increments_suffix(tmp_path: Path) -> None:
    (tmp_path / "report.xlsx").touch()
    (tmp_path / "report_01.xlsx").touch()
    assert available_output_path(tmp_path, "report.xlsx").name == "report_02.xlsx"
