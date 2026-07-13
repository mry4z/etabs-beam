"""Project configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class EtabsSettings:
    dll_path: Path
    mode: str = "attach"


@dataclass(frozen=True)
class GeometrySettings:
    coordinate_tolerance_mm: float = 1.0
    slope_limit_deg: float = 1.0
    merge_angle_limit_deg: float = 15.0
    export_unit: str = "N_mm_C"


@dataclass(frozen=True)
class OutputSettings:
    reports_dir: Path = PROJECT_ROOT / "build/output/reports"
    logs_dir: Path = PROJECT_ROOT / "build/output/logs"
    report_filename: str = "beam_schedule_{timestamp}.xlsx"
    log_filename: str = "etabs_beam_{timestamp}.log"


@dataclass(frozen=True)
class AppConfig:
    etabs: EtabsSettings
    geometry: GeometrySettings
    output: OutputSettings
    beam_selection_mode: str = "all"
    log_level: str = "INFO"


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"配置项 {name} 必须是对象。")
    return value


def _normalize_unit(value: object) -> str:
    normalized = str(value).strip().replace("-", "_")
    if normalized != "N_mm_C":
        raise ValueError("当前 MVP 仅支持固定导出单位 N_mm_C。")
    return normalized


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load ``config.yaml``; fall back to the checked-in example configuration."""
    config_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        config_path = PROJECT_ROOT / "config.example.yaml"
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    etabs = _section(raw, "etabs")
    geometry = _section(raw, "geometry")
    output = _section(raw, "output")
    logging = _section(raw, "logging")
    advanced = _section(raw, "advanced")

    dll_path = _resolve_path(etabs.get("dll_path", ""))
    if not dll_path.name:
        raise ValueError("缺少 etabs.dll_path 配置。")

    return AppConfig(
        etabs=EtabsSettings(
            dll_path=dll_path,
            mode=str(etabs.get("mode", "attach")),
        ),
        geometry=GeometrySettings(
            coordinate_tolerance_mm=float(geometry.get("coordinate_tolerance_mm", 1.0)),
            slope_limit_deg=float(geometry.get("slope_limit_deg", 1.0)),
            merge_angle_limit_deg=float(geometry.get("merge_angle_limit_deg", 15.0)),
            export_unit=_normalize_unit(geometry.get("unit_system", "N_mm_C")),
        ),
        output=OutputSettings(
            reports_dir=_resolve_path(output.get("reports_dir", "build/output/reports")),
            logs_dir=_resolve_path(output.get("logs_dir", "build/output/logs")),
            report_filename=str(output.get("report_filename", "beam_schedule_{timestamp}.xlsx")),
            log_filename=str(output.get("log_filename", "etabs_beam_{timestamp}.log")),
        ),
        beam_selection_mode=str(advanced.get("beam_selection_mode", "all")),
        log_level=str(logging.get("level", "INFO")).upper(),
    )
