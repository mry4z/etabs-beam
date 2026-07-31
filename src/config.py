"""Project configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAX_AGGREGATE_SIZE_MM = 25.0
DEFAULT_MAX_AGGREGATE_SIZE_SOURCE = "Project default: 25 mm maximum aggregate size"


@dataclass(frozen=True)
class EtabsSettings:
    dll_path: Path
    mode: str = "attach"


@dataclass(frozen=True)
class GeometrySettings:
    coordinate_tolerance_mm: float = 1.0
    source_span_overlap_tolerance_mm: float = 2.0
    slope_limit_deg: float = 1.0
    merge_angle_limit_deg: float = 15.0
    export_unit: str = "N_mm_C"


@dataclass(frozen=True)
class DesignSettings:
    """Design contract for the current production path."""

    standard: str = "ACI 318-14"
    clear_cover_to_stirrup_outer_mm: float | None = None
    clear_cover_source: str = ""
    max_aggregate_size_mm: float = DEFAULT_MAX_AGGREGATE_SIZE_MM
    max_aggregate_size_source: str = DEFAULT_MAX_AGGREGATE_SIZE_SOURCE


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
    design: DesignSettings
    output: OutputSettings
    beam_selection_mode: str = "all"
    log_level: str = "INFO"


def _resolve_path(
    value: str | Path,
    base_directory: str | Path = PROJECT_ROOT,
) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(base_directory) / path


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


def _normalize_design_standard(value: object) -> str:
    normalized = str(value).strip().upper().replace("-", " ")
    normalized = " ".join(normalized.split())
    if normalized in {"ACI 318 14", "ACI318 14", "ACI31814"}:
        return "ACI 318-14"
    raise ValueError("当前配筋引擎仅支持 ACI 318-14。")


def _optional_positive(value: object, name: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    normalized = float(value)
    if normalized <= 0:
        raise ValueError(f"配置项 {name} 必须为正数。")
    return normalized


def _require_source(value: float | None, source: object, name: str) -> str:
    normalized = str(source or "").strip()
    if value is not None and not normalized:
        raise ValueError(f"配置项 {name} 已提供数值，但缺少可追溯来源。")
    return normalized


def _resolve_max_aggregate_size(design: dict[str, Any]) -> tuple[float, str]:
    """Resolve the project default while keeping every explicit override traceable."""

    raw_size = design.get("max_aggregate_size_mm")
    raw_source = design.get("max_aggregate_size_source")
    if raw_size is None or str(raw_size).strip() == "":
        if str(raw_source or "").strip():
            raise ValueError(
                "配置项 design.max_aggregate_size_source 已提供来源，" "但缺少对应的 design.max_aggregate_size_mm。"
            )
        return (
            DEFAULT_MAX_AGGREGATE_SIZE_MM,
            DEFAULT_MAX_AGGREGATE_SIZE_SOURCE,
        )
    size = _optional_positive(raw_size, "design.max_aggregate_size_mm")
    assert size is not None
    source = _require_source(
        size,
        raw_source,
        "design.max_aggregate_size_source",
    )
    return size, source


def load_config(
    path: str | Path | None = None,
    *,
    etabs_dll_path: str | Path | None = None,
) -> AppConfig:
    """Load ``config.yaml``; fall back to the checked-in example configuration."""
    config_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        config_path = PROJECT_ROOT / "config.example.yaml"
    config_directory = config_path.resolve().parent
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    etabs = _section(raw, "etabs")
    geometry = _section(raw, "geometry")
    design = _section(raw, "design")
    output = _section(raw, "output")
    logging = _section(raw, "logging")
    advanced = _section(raw, "advanced")

    dll_path = _resolve_path(
        etabs_dll_path if etabs_dll_path is not None else etabs.get("dll_path", ""),
        config_directory,
    )
    if not dll_path.name:
        raise ValueError("缺少 etabs.dll_path 配置。")
    clear_cover = _optional_positive(
        design.get("clear_cover_to_stirrup_outer_mm"),
        "design.clear_cover_to_stirrup_outer_mm",
    )
    clear_cover_source = _require_source(
        clear_cover,
        design.get("clear_cover_source"),
        "design.clear_cover_source",
    )
    aggregate_size, aggregate_source = _resolve_max_aggregate_size(design)

    return AppConfig(
        etabs=EtabsSettings(
            dll_path=dll_path,
            mode=str(etabs.get("mode", "attach")),
        ),
        geometry=GeometrySettings(
            coordinate_tolerance_mm=float(geometry.get("coordinate_tolerance_mm", 1.0)),
            source_span_overlap_tolerance_mm=float(geometry.get("source_span_overlap_tolerance_mm", 2.0)),
            slope_limit_deg=float(geometry.get("slope_limit_deg", 1.0)),
            merge_angle_limit_deg=float(geometry.get("merge_angle_limit_deg", 15.0)),
            export_unit=_normalize_unit(geometry.get("unit_system", "N_mm_C")),
        ),
        design=DesignSettings(
            standard=_normalize_design_standard(design.get("standard", "ACI 318-14")),
            clear_cover_to_stirrup_outer_mm=clear_cover,
            clear_cover_source=clear_cover_source,
            max_aggregate_size_mm=aggregate_size,
            max_aggregate_size_source=aggregate_source,
        ),
        output=OutputSettings(
            reports_dir=_resolve_path(output.get("reports_dir", "build/output/reports"), config_directory),
            logs_dir=_resolve_path(output.get("logs_dir", "build/output/logs"), config_directory),
            report_filename=str(output.get("report_filename", "beam_schedule_{timestamp}.xlsx")),
            log_filename=str(output.get("log_filename", "etabs_beam_{timestamp}.log")),
        ),
        beam_selection_mode=str(advanced.get("beam_selection_mode", "all")),
        log_level=str(logging.get("level", "INFO")).upper(),
    )
