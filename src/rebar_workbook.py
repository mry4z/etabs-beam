"""Formal three-sheet reinforcement-workbook data and build workflow.

The Python side owns ETABS extraction, span aggregation and detailing.  It
writes a versioned JSON payload; the tracked artifact-tool builder only owns
Excel formulas, formatting and rendering.  This keeps numbering/topology and
the workbook layout independently replaceable.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

from src.core.beam_line_span_analyzer import BeamLineSpanResult
from src.core.etabs_connector import api_values
from src.core.geometry_extractor import BeamGeometry
from src.design.detailing import (
    BeamDetailingProfile,
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamRegionDemand,
    BeamSection,
    DetailingError,
    resolve_detailing_profile,
)
from src.design.provider import BeamDesignResult, DesignStation
from src.excel_span_adapter import SpanScheduleMapping, build_span_schedule

PAYLOAD_SCHEMA_VERSION = "1.1"
DEFAULT_AGGREGATE_MM = 20.0
REGIONS = ("left", "middle", "right")
BUILDER_PATH = Path(__file__).resolve().parent / "spreadsheet" / "build_rebar_workbook.mjs"


def build_rebar_workbook_payload(
    sap_model: Any,
    structural: BeamLineSpanResult,
    beams: Iterable[BeamGeometry],
    designs: Mapping[str, BeamDesignResult],
    *,
    model_path: str | Path,
    tolerance_mm: float = 1.0,
    aggregate_mm: float = DEFAULT_AGGREGATE_MM,
    engine: BeamDetailingEngine | None = None,
) -> dict[str, Any]:
    """Build the complete JSON-ready workbook payload from one ETABS run."""

    beam_list = tuple(beams)
    schedule = build_span_schedule(
        structural,
        list(beam_list),
        designs,
        tolerance_mm,
    )
    return build_payload_from_schedule(
        schedule,
        beam_list,
        section_loader=lambda name: _section_info(sap_model, name),
        frame_types=_frame_types(sap_model),
        metadata={
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "model": str(Path(model_path)),
            "units": "N_mm_C",
            "design_code": str(
                api_values(
                    sap_model.DesignConcrete.GetCode(None),
                    "DesignConcrete.GetCode",
                )[0]
            ),
            "frame_objects": len(beam_list),
            "beam_lines": len(structural.lines),
            "spans": len(structural.spans),
            "aggregate_mm": float(aggregate_mm),
            "aggregate_source": "项目默认值；原始分跨数据表显式列出",
            "cover_source": (
                "ETABS PropFrame.GetRebarBeam 顶/底纵筋设计 cover 的较大值；" "不等同于施工箍筋外缘净保护层"
            ),
            "detailing_profile_source": (
                "SB is always ordinary without Chapter 18; PB Sway Special is SMF; "
                "all other resolved PB types use the project OMF profile"
            ),
            "minimum_bars_per_layer": 2,
        },
        aggregate_mm=aggregate_mm,
        engine=engine,
    )


def build_payload_from_schedule(
    schedule: SpanScheduleMapping,
    beams: Iterable[BeamGeometry],
    *,
    section_loader: Any,
    frame_types: Mapping[str, str],
    metadata: Mapping[str, Any],
    aggregate_mm: float = DEFAULT_AGGREGATE_MM,
    engine: BeamDetailingEngine | None = None,
) -> dict[str, Any]:
    """Pure-ish adapter used by both the ETABS workflow and unit tests."""

    engine = engine or BeamDetailingEngine()
    by_name = {beam.name: beam for beam in beams}
    section_cache: dict[str, dict[str, Any]] = {}
    raw_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []

    for record in schedule.records:
        span = record.span
        geometry = record.geometry
        source_sections = tuple(dict.fromkeys(by_name[name].section for name in span.source_objects if name in by_name))
        primary_section = source_sections[0] if source_sections else geometry.section
        source_frame_types = tuple(dict.fromkeys(frame_types.get(name, "Unknown") for name in span.source_objects))
        detailing_profile, profile_source = resolve_detailing_profile(span.beam_type, source_frame_types)
        warnings: list[str] = []
        blocked_reason = ""
        if len(source_sections) > 1:
            warnings.append("MIXED_SECTION")
            blocked_reason = "MIXED_SECTION_DETAILING_BLOCKED"
        if len(source_frame_types) > 1 and span.beam_type == "PB":
            warnings.append("MIXED_FRAME_TYPE")
            blocked_reason = blocked_reason or "MIXED_FRAME_TYPE_DETAILING_BLOCKED"
        elif len(source_frame_types) > 1:
            warnings.append("SB_FRAME_TYPE_IGNORED_FOR_PROFILE")
        if "Program Determined" in source_frame_types and span.beam_type == "PB":
            warnings.append("FRAME_TYPE_PROGRAM_DETERMINED")
            blocked_reason = blocked_reason or "FRAME_TYPE_REVIEW_REQUIRED"
        elif "Program Determined" in source_frame_types:
            warnings.append("SB_FRAME_TYPE_IGNORED_FOR_PROFILE")
        if span.beam_type == "SB" and any(
            frame_type in {"Sway Ordinary", "Sway Intermediate", "Sway Special"} for frame_type in source_frame_types
        ):
            warnings.append("SB_ETABS_SWAY_DESIGN_RESULT_REVIEW")
        if detailing_profile is BeamDetailingProfile.PB_REVIEW_REQUIRED:
            warnings.append("DETAILING_PROFILE_REVIEW_REQUIRED")
            blocked_reason = blocked_reason or "DETAILING_PROFILE_REVIEW_REQUIRED"

        section_error = ""
        try:
            section = section_cache.setdefault(
                primary_section,
                section_loader(primary_section),
            )
        except Exception as exc:
            section_error = f"SECTION_INFO_ERROR: {exc}"
            section = {
                "width_mm": None,
                "depth_mm": geometry.depth,
                "cover_mm": None,
                "fc_mpa": None,
                "fy_mpa": None,
                "fyt_mpa": None,
            }
            warnings.append("SECTION_INFO_ERROR")
            blocked_reason = blocked_reason or "SECTION_INFO_ERROR"

        common = {
            "beam_no": f"{span.beam_line_id}-{span.span_index}",
            "beam_line": span.beam_line_id,
            "span_no": span.span_index,
            "story": span.story,
            "beam_type": span.beam_type,
            "section": primary_section,
            "all_sections": ", ".join(source_sections),
            "width_mm": section.get("width_mm"),
            "depth_mm": section.get("depth_mm"),
            "length_mm": geometry.length,
            "cover_mm": section.get("cover_mm"),
            "fc_mpa": section.get("fc_mpa"),
            "fy_mpa": section.get("fy_mpa"),
            "fyt_mpa": section.get("fyt_mpa"),
            "aggregate_mm": float(aggregate_mm),
            "frame_type": ", ".join(source_frame_types),
            "detailing_profile": detailing_profile.value,
            "profile_source": profile_source,
            "is_smf": detailing_profile.is_smf,
            "source_objects": ", ".join(span.source_objects),
            "design_status": record.design.status.value,
            "warnings": "; ".join(warnings),
        }
        controls = _region_controls(
            record.design,
            geometry.length,
            float(section.get("depth_mm") or 0.0),
        )
        raw = dict(common)
        if controls is not None:
            for region_name, control in controls.items():
                for key, value in control.items():
                    raw[f"{region_name}_{key}"] = value

        status = blocked_reason or section_error
        if controls is None:
            status = "NO_RESULTS"
        if status:
            raw["engine_status"] = status
            raw_rows.append(raw)
            review_rows.append(_empty_review(common, status))
            final_rows.append(_empty_final(common, status))
            continue

        try:
            demands = tuple(_engine_demand(name, controls[name]) for name in REGIONS)
            result = engine.design(
                BeamDetailingInput(
                    BeamSection(
                        common["beam_no"],
                        float(section["width_mm"]),
                        float(section["depth_mm"]),
                        float(section["cover_mm"]),
                        float(section["fc_mpa"]),
                        float(section["fy_mpa"]),
                        float(section["fyt_mpa"]),
                        float(aggregate_mm),
                        geometry.length,
                        detailing_profile,
                    ),
                    demands,
                )
            )
            raw["engine_status"] = "OK"
            raw["engine_mode"] = result.mode.value
            review = _review_row(common, controls, result)
            final = _final_row(common, result)
        except (DetailingError, ValueError) as exc:
            status = str(exc)
            raw["engine_status"] = status
            review = _empty_review(common, status)
            final = _empty_final(common, status)
        raw_rows.append(raw)
        review_rows.append(review)
        final_rows.append(final)

    payload = {
        "metadata": dict(metadata),
        "raw_rows": raw_rows,
        "review_rows": review_rows,
        "final_rows": final_rows,
    }
    payload["metadata"].setdefault("schema_version", PAYLOAD_SCHEMA_VERSION)
    payload["metadata"].setdefault("aggregate_mm", float(aggregate_mm))
    payload["metadata"].setdefault("minimum_bars_per_layer", 2)
    return payload


def redetail_payload(
    payload: Mapping[str, Any],
    *,
    engine: BeamDetailingEngine | None = None,
) -> dict[str, Any]:
    """Re-run only the detailing engine from a previously extracted payload."""

    engine = engine or BeamDetailingEngine()
    output = json.loads(json.dumps(payload, ensure_ascii=False))
    raw_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    for source in output.get("raw_rows", []):
        raw = dict(source)
        common = {
            key: value
            for key, value in raw.items()
            if not key.startswith(tuple(f"{region}_" for region in REGIONS))
            and key not in {"engine_status", "engine_mode"}
        }
        profile_value = common.get("detailing_profile")
        if profile_value:
            detailing_profile = BeamDetailingProfile(str(profile_value))
        elif str(common.get("beam_type", "")).upper() == "SB":
            detailing_profile = BeamDetailingProfile.SB_ORDINARY_NO_CH18
        elif bool(common.get("is_smf")):
            detailing_profile = BeamDetailingProfile.PB_SMF
        else:
            detailing_profile = BeamDetailingProfile.PB_OMF
        controls = {
            region: {key[len(region) + 1 :]: value for key, value in raw.items() if key.startswith(f"{region}_")}
            for region in REGIONS
        }
        prior_status = str(source.get("engine_status", ""))
        if prior_status.startswith(("MIXED_", "FRAME_TYPE_", "DETAILING_PROFILE_", "SECTION_INFO_")):
            status = prior_status
        elif any("top_total_mm2" not in controls[region] for region in REGIONS):
            status = "NO_RESULTS"
        else:
            status = ""
        if status:
            raw["engine_status"] = status
            raw.pop("engine_mode", None)
            raw_rows.append(raw)
            review_rows.append(_empty_review(common, status))
            final_rows.append(_empty_final(common, status))
            continue
        try:
            result = engine.design(
                BeamDetailingInput(
                    BeamSection(
                        str(common["beam_no"]),
                        float(common["width_mm"]),
                        float(common["depth_mm"]),
                        float(common["cover_mm"]),
                        float(common["fc_mpa"]),
                        float(common["fy_mpa"]),
                        float(common["fyt_mpa"]),
                        float(common["aggregate_mm"]),
                        float(common["length_mm"]),
                        detailing_profile,
                    ),
                    tuple(_engine_demand(region, controls[region]) for region in REGIONS),
                )
            )
            raw["engine_status"] = "OK"
            raw["engine_mode"] = result.mode.value
            review = _review_row(common, controls, result)
            final = _final_row(common, result)
        except (DetailingError, ValueError) as exc:
            status = str(exc)
            raw["engine_status"] = status
            raw.pop("engine_mode", None)
            review = _empty_review(common, status)
            final = _empty_final(common, status)
        raw_rows.append(raw)
        review_rows.append(review)
        final_rows.append(final)
    output["raw_rows"] = raw_rows
    output["review_rows"] = review_rows
    output["final_rows"] = final_rows
    output.setdefault("metadata", {})["minimum_bars_per_layer"] = 2
    return output


def write_rebar_payload(payload: Mapping[str, Any], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination


def run_workbook_builder(
    payload_path: str | Path,
    output_path: str | Path,
    *,
    preview_dir: str | Path,
    node_executable: str | Path = "node",
    node_modules: str | Path | None = None,
    node_max_old_space_mb: int = 8192,
) -> Path:
    """Run the tracked artifact-tool builder without importing ignored files."""

    payload = Path(payload_path).resolve()
    output = Path(output_path).resolve()
    previews = Path(preview_dir).resolve()
    if not BUILDER_PATH.is_file():
        raise FileNotFoundError(f"找不到正式工作簿生成器：{BUILDER_PATH}")
    output.parent.mkdir(parents=True, exist_ok=True)
    previews.mkdir(parents=True, exist_ok=True)
    if node_max_old_space_mb < 1024:
        raise ValueError("大型配筋工作簿的 Node 堆内存不得小于 1024 MB。")
    node_prefix = [
        str(node_executable),
        f"--max-old-space-size={int(node_max_old_space_mb)}",
    ]

    if node_modules is None:
        command = [
            *node_prefix,
            str(BUILDER_PATH),
            str(payload),
            str(output),
            str(previews),
        ]
        subprocess.run(command, check=True)
        return output

    modules = Path(node_modules).resolve()
    if not modules.is_dir():
        raise FileNotFoundError(f"找不到 artifact-tool node_modules：{modules}")
    with tempfile.TemporaryDirectory(prefix="etabs_beam_workbook_") as temp_name:
        work_dir = Path(temp_name)
        builder = work_dir / BUILDER_PATH.name
        shutil.copy2(BUILDER_PATH, builder)
        link = work_dir / "node_modules"
        _link_node_modules(link, modules)
        try:
            subprocess.run(
                [
                    *node_prefix,
                    str(builder),
                    str(payload),
                    str(output),
                    str(previews),
                ],
                cwd=work_dir,
                check=True,
            )
        finally:
            if link.exists():
                os.rmdir(link)
    return output


def _link_node_modules(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )


def _frame_types(sap_model: Any) -> dict[str, str]:
    result = sap_model.DatabaseTables.GetTableForDisplayArray(
        "Concrete Beam Overwrites - ACI 318-14",
        None,
        "",
        0,
        None,
        0,
        None,
    )
    if not isinstance(result, tuple) or not result or int(result[0]) != 0:
        return {}
    fields = [str(value) for value in list(result[3])]
    data = [str(value) for value in list(result[5])]
    if not fields or "UniqueName" not in fields or "FrameType" not in fields:
        return {}
    width = len(fields)
    unique_index = fields.index("UniqueName")
    type_index = fields.index("FrameType")
    return {
        data[offset + unique_index]: data[offset + type_index]
        for offset in range(0, len(data), width)
        if offset + max(unique_index, type_index) < len(data)
    }


def _section_info(sap_model: Any, section_name: str) -> dict[str, Any]:
    try:
        values = api_values(
            sap_model.PropFrame.GetRectangle(
                section_name,
                None,
                None,
                0.0,
                0.0,
                0,
                None,
                None,
            ),
            f"PropFrame.GetRectangle({section_name})",
        )
        material, depth, width = str(values[1]), float(values[2]), float(values[3])
    except Exception:
        values = api_values(
            sap_model.PropFrame.GetGeneral(
                section_name,
                None,
                None,
                0.0,
                0.0,
                *([0.0] * 12),
                0,
                None,
                None,
            ),
            f"PropFrame.GetGeneral({section_name})",
        )
        material, depth, width = str(values[1]), float(values[2]), float(values[3])
    rebar = api_values(
        sap_model.PropFrame.GetRebarBeam(
            section_name,
            None,
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ),
        f"PropFrame.GetRebarBeam({section_name})",
    )
    longitudinal = _rebar_material(sap_model, str(rebar[0]))
    transverse = _rebar_material(sap_model, str(rebar[1]))
    concrete = api_values(
        sap_model.PropMaterial.GetOConcrete(
            material,
            0.0,
            False,
            0.0,
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ),
        f"PropMaterial.GetOConcrete({material})",
    )
    return {
        "section": section_name,
        "width_mm": width,
        "depth_mm": depth,
        "cover_mm": max(float(rebar[2]), float(rebar[3])),
        "fc_mpa": float(concrete[0]),
        "fy_mpa": longitudinal,
        "fyt_mpa": transverse,
    }


def _rebar_material(sap_model: Any, name: str) -> float:
    return float(
        api_values(
            sap_model.PropMaterial.GetORebar(
                name,
                0.0,
                0.0,
                0.0,
                0.0,
                0,
                0,
                0.0,
                0.0,
                False,
                0.0,
            ),
            f"PropMaterial.GetORebar({name})",
        )[0]
    )


def _region_controls(
    design: BeamDesignResult,
    length_mm: float,
    depth_mm: float,
) -> dict[str, dict[str, Any]] | None:
    if not design.stations or length_mm <= 0 or depth_mm <= 0:
        return None
    end_zone = min(2.0 * depth_mm, length_mm / 2.0)
    groups = {
        "left": [station for station in design.stations if station.location <= end_zone],
        "middle": [station for station in design.stations if end_zone < station.location < length_mm - end_zone],
        "right": [station for station in design.stations if station.location >= length_mm - end_zone],
    }
    targets = {
        "left": end_zone,
        "middle": length_mm / 2.0,
        "right": length_mm - end_zone,
    }
    for name, stations in groups.items():
        if not stations:
            groups[name] = [
                min(
                    design.stations,
                    key=lambda station: abs(station.location - targets[name]),
                )
            ]
    return {name: _control_values(stations) for name, stations in groups.items()}


def _control_values(stations: Iterable[DesignStation]) -> dict[str, Any]:
    items = list(stations)
    top_total = max(items, key=lambda station: station.as_total_top_mm2)
    top_flex = max(items, key=lambda station: station.as_flex_top_mm2)
    bottom_total = max(items, key=lambda station: station.as_total_bottom_mm2)
    bottom_flex = max(items, key=lambda station: station.as_flex_bottom_mm2)
    shear = max(items, key=lambda station: station.shear_area)
    tl = max(items, key=lambda station: station.torsion_longitudinal_area)
    tt = max(items, key=lambda station: station.torsion_trans_area)
    return {
        "top_total_mm2": top_total.as_total_top_mm2,
        "top_total_station_mm": top_total.location,
        "top_total_combo": top_total.top_flexural_combo,
        "top_flex_mm2": top_flex.as_flex_top_mm2,
        "top_flex_station_mm": top_flex.location,
        "top_flex_combo": top_flex.top_flexural_combo,
        "bottom_total_mm2": bottom_total.as_total_bottom_mm2,
        "bottom_total_station_mm": bottom_total.location,
        "bottom_total_combo": bottom_total.bottom_flexural_combo,
        "bottom_flex_mm2": bottom_flex.as_flex_bottom_mm2,
        "bottom_flex_station_mm": bottom_flex.location,
        "bottom_flex_combo": bottom_flex.bottom_flexural_combo,
        "as_min_top_mm2": max(
            (station.top_min_area or 0.0 for station in items),
            default=0.0,
        ),
        "as_min_bottom_mm2": max(
            (station.bottom_min_area or 0.0 for station in items),
            default=0.0,
        ),
        "as_provided_top_mm2": max(
            (station.top_provided_area or -1.0 for station in items),
            default=-1.0,
        ),
        "as_provided_bottom_mm2": max(
            (station.bottom_provided_area or -1.0 for station in items),
            default=-1.0,
        ),
        "av_over_s": shear.shear_area,
        "av_station_mm": shear.location,
        "av_combo": shear.shear_combo,
        "al_mm2": tl.torsion_longitudinal_area,
        "al_station_mm": tl.location,
        "al_combo": tl.torsion_longitudinal_combo,
        "at_over_s": tt.torsion_trans_area,
        "at_station_mm": tt.location,
        "at_combo": tt.torsion_trans_combo,
        "source_station_count": len(items),
    }


def _engine_demand(name: str, control: Mapping[str, Any]) -> BeamRegionDemand:
    return BeamRegionDemand(
        name=name,
        location_mm=float(control["top_total_station_mm"]),
        as_top_total_mm2=float(control["top_total_mm2"]),
        as_bottom_total_mm2=float(control["bottom_total_mm2"]),
        av_over_s_mm2_per_mm=float(control["av_over_s"]),
        at_over_s_mm2_per_mm=float(control["at_over_s"]),
        al_torsion_mm2=float(control["al_mm2"]),
        top_combo=str(control["top_total_combo"]),
        bottom_combo=str(control["bottom_total_combo"]),
        shear_combo=str(control["av_combo"]),
        torsion_combo=str(control["al_combo"]),
        as_flex_top_override_mm2=float(control["top_flex_mm2"]),
        as_flex_bottom_override_mm2=float(control["bottom_flex_mm2"]),
    )


def _review_row(common: Mapping[str, Any], controls: Mapping[str, Any], result: Any) -> dict[str, Any]:
    by_name = {region.name: region for region in result.regions}
    row = dict(common)
    row.update(
        {
            "engine_status": "OK",
            "mode": result.mode.value,
            "legs": max(region.stirrup.legs for region in result.regions),
            "face_required_mm2": result.face_rebar.required_each_side_mm2,
            "face_text": result.face_rebar.notation,
            "engine_warnings": "; ".join(result.warnings),
        }
    )
    for field, region_name, face in (
        ("et1", "left", "top"),
        ("t1", "middle", "top"),
        ("et2", "right", "top"),
        ("eb1", "left", "bottom"),
        ("b1", "middle", "bottom"),
        ("eb2", "right", "bottom"),
    ):
        region = by_name[region_name]
        design = getattr(region, face)
        demand_key = f"{face}_total_mm2" if result.mode.value == "NORMAL" else f"{face}_flex_mm2"
        row[f"{field}_required_mm2"] = controls[region_name][demand_key]
        row[f"{field}_text"] = design.notation
    for field, region_name in (
        ("s2_left", "left"),
        ("s1", "middle"),
        ("s2_right", "right"),
    ):
        row[f"{field}_text"] = by_name[region_name].stirrup.notation
        row[f"{field}_at_required"] = controls[region_name]["at_over_s"]
        row[f"{field}_combo_required"] = controls[region_name]["av_over_s"] + 2.0 * controls[region_name]["at_over_s"]
    support = max(
        (by_name["left"].stirrup, by_name["right"].stirrup),
        key=lambda stirrup: (math.pi * stirrup.diameter_mm**2 / 4.0 / stirrup.spacing_mm),
    )
    row["s2_text"] = support.notation
    return row


def _final_row(common: Mapping[str, Any], result: Any) -> dict[str, Any]:
    by_name = {region.name: region for region in result.regions}
    support = max(
        (by_name["left"].stirrup, by_name["right"].stirrup),
        key=lambda stirrup: (math.pi * stirrup.diameter_mm**2 / 4.0 / stirrup.spacing_mm),
    )
    return {
        **common,
        "engine_status": "OK",
        "mode": result.mode.value,
        "et1": by_name["left"].top.notation,
        "t1": by_name["middle"].top.notation,
        "et2": by_name["right"].top.notation,
        "eb1": by_name["left"].bottom.notation,
        "b1": by_name["middle"].bottom.notation,
        "eb2": by_name["right"].bottom.notation,
        "s2": support.notation,
        "s1": by_name["middle"].stirrup.notation,
        "legs": max(region.stirrup.legs for region in result.regions),
        "face": result.face_rebar.notation,
    }


def _empty_review(common: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {
        **common,
        "engine_status": status,
        "mode": "",
        "legs": None,
        "face_required_mm2": None,
        "face_text": "",
        **{f"{field}_required_mm2": None for field in ("et1", "t1", "et2", "eb1", "b1", "eb2")},
        **{f"{field}_text": "" for field in ("et1", "t1", "et2", "eb1", "b1", "eb2")},
        **{f"{field}_text": "" for field in ("s2_left", "s1", "s2_right")},
        "s2_text": "",
    }


def _empty_final(common: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {
        **common,
        "engine_status": status,
        "mode": "",
        **{
            field: ""
            for field in (
                "et1",
                "t1",
                "et2",
                "eb1",
                "b1",
                "eb2",
                "s2",
                "s1",
                "face",
            )
        },
        "legs": None,
    }
