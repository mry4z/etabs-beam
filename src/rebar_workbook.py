"""Build the versioned detailing payload and its static result-table views.

The Python side owns ETABS extraction, span aggregation and detailing.  It
writes a versioned JSON payload that retains raw demands, review evidence and
final rows.  Excel is only a static PB or SB result projection; it is not the
calculation or audit store.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, replace
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Iterable, Mapping

from src.core.beam_line_span_analyzer import BeamLineSpanResult
from src.core.etabs_connector import EtabsApiError, api_values
from src.core.geometry_extractor import BeamGeometry
from src.design.detailing import (
    BeamDetailingProfile,
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamRegionDemand,
    BeamSection,
    BeamDetailingResult,
    DetailingError,
    coordinate_adjacent_spans,
    resolve_detailing_profile,
)
from src.design.constructability import SeismicDetailingContext, SupportFaceContext
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.excel_span_adapter import SpanScheduleMapping, build_span_schedule

PAYLOAD_SCHEMA_VERSION = "2.1"
SUPPORTED_PAYLOAD_SCHEMA_VERSIONS = frozenset({"2.0", PAYLOAD_SCHEMA_VERSION})
RESULT_WORKBOOK_LAYOUT_VERSION = "single-sheet-static-v1"
BOTTOM_REINFORCEMENT_STRATEGY = "FULL_SPAN_UNIFORM_PER_SPAN"
ADJACENT_SPAN_BOTTOM_STRATEGY = "INDEPENDENT_PER_SPAN"
REGIONS = ("left", "middle", "right")
BUILDER_PATH = Path(__file__).resolve().parent / "spreadsheet" / "build_rebar_workbook.mjs"
PREVIEW_BUILDER_PATH = Path(__file__).resolve().parent / "spreadsheet" / "render_rebar_workbook_preview.mjs"
SECONDARY_BUILDER_PATH = Path(__file__).resolve().parent / "spreadsheet" / "build_secondary_beam_workbook.mjs"


def _apply_current_workbook_metadata(metadata: dict[str, Any]) -> None:
    """Stamp the current result projection and detailing semantics.

    These keys are additive to payload schema 2.1.  They make it impossible
    for a downstream renderer to mistake a legacy three-station payload for a
    newly recomputed full-span bottom result.
    """

    metadata["rebar_workbook_layout_version"] = RESULT_WORKBOOK_LAYOUT_VERSION
    metadata["rebar_workbook_layout"] = "one-sheet-static-values"
    metadata["bottom_reinforcement_strategy"] = BOTTOM_REINFORCEMENT_STRATEGY
    metadata["adjacent_span_bottom_strategy"] = ADJACENT_SPAN_BOTTOM_STRATEGY
    metadata["top_reinforcement_coordination"] = "EXISTING_BEAM_LINE_RULES"
    metadata["audit_data_boundary"] = (
        "raw_rows/review_rows/final_rows retained in JSON; Excel is a filtered static projection"
    )


def build_rebar_workbook_payload(
    sap_model: Any,
    structural: BeamLineSpanResult,
    beams: Iterable[BeamGeometry],
    designs: Mapping[str, BeamDesignResult],
    *,
    model_path: str | Path,
    tolerance_mm: float = 1.0,
    clear_cover_to_stirrup_outer_mm: float | None,
    clear_cover_source: str,
    max_aggregate_size_mm: float | None,
    max_aggregate_size_source: str,
    engine: BeamDetailingEngine | None = None,
    support_face_contexts: Mapping[str, tuple[SupportFaceContext | None, SupportFaceContext | None]] | None = None,
    seismic_contexts: Mapping[str, SeismicDetailingContext] | None = None,
    support_points: Mapping[str, tuple[float, float, float]] | None = None,
    support_dimensions_mm: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build the complete JSON-ready workbook payload from one ETABS run."""

    return build_rebar_workbook_payload_from_domain(
        structural,
        beams,
        designs,
        section_loader=lambda name: _section_info(sap_model, name),
        frame_types=_frame_types(sap_model),
        resolved_frame_types=_resolved_frame_types(sap_model),
        capacity_shear_envelope=_capacity_shear_envelope(sap_model),
        model_path=model_path,
        design_code=str(
            api_values(
                sap_model.DesignConcrete.GetCode(None),
                "DesignConcrete.GetCode",
            )[0]
        ),
        tolerance_mm=tolerance_mm,
        clear_cover_to_stirrup_outer_mm=clear_cover_to_stirrup_outer_mm,
        clear_cover_source=clear_cover_source,
        max_aggregate_size_mm=max_aggregate_size_mm,
        max_aggregate_size_source=max_aggregate_size_source,
        engine=engine,
        support_face_contexts=support_face_contexts,
        seismic_contexts=seismic_contexts,
        support_points=support_points,
        support_dimensions_mm=support_dimensions_mm,
    )


def build_rebar_workbook_payload_from_domain(
    structural: BeamLineSpanResult,
    beams: Iterable[BeamGeometry],
    designs: Mapping[str, BeamDesignResult],
    *,
    section_loader: Any,
    frame_types: Mapping[str, str],
    resolved_frame_types: Mapping[str, str] | None = None,
    capacity_shear_envelope: Mapping[str, tuple[Any, ...]] | None = None,
    model_path: str | Path,
    design_code: str,
    tolerance_mm: float = 1.0,
    clear_cover_to_stirrup_outer_mm: float | None,
    clear_cover_source: str,
    max_aggregate_size_mm: float | None,
    max_aggregate_size_source: str,
    engine: BeamDetailingEngine | None = None,
    support_face_contexts: Mapping[str, tuple[SupportFaceContext | None, SupportFaceContext | None]] | None = None,
    seismic_contexts: Mapping[str, SeismicDetailingContext] | None = None,
    support_points: Mapping[str, tuple[float, float, float]] | None = None,
    support_dimensions_mm: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build the same workbook payload from any normalized read-model source."""

    engine = engine or BeamDetailingEngine()
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
        section_loader=section_loader,
        frame_types=frame_types,
        resolved_frame_types=resolved_frame_types,
        capacity_shear_envelope=capacity_shear_envelope,
        metadata={
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "model": str(Path(model_path)),
            "units": "N_mm_C",
            "design_code": str(design_code),
            "frame_objects": len(beam_list),
            "beam_lines": len(structural.lines),
            "spans": len(structural.spans),
            "cover_calculation_basis": (
                "CLEAR_TO_STIRRUP_OUTER"
                if clear_cover_to_stirrup_outer_mm is not None
                else "ETABS_LONGITUDINAL_CENTROID"
            ),
            "clear_cover_to_stirrup_outer_mm": clear_cover_to_stirrup_outer_mm,
            "clear_cover_source": clear_cover_source,
            "max_aggregate_size_mm": max_aggregate_size_mm,
            "max_aggregate_size_source": max_aggregate_size_source,
            "etabs_longitudinal_cover_source": (
                "ETABS PropFrame.GetRebarBeam CoverTop/CoverBot; "
                "concrete face to longitudinal-reinforcement centroid"
            ),
            "detailing_profile_source": (
                "Explicit ETABS FrameType is preserved; Program Determined defaults to SMF"
            ),
            "program_determined_default": "SMF",
            "minimum_bars_per_layer": 2,
            "minimum_main_diameter_mm": engine.project.minimum_main_diameter_mm,
            "hanger_diameter_mm": engine.project.sb_hanger_diameter_mm,
            "smf_end_max_spacing_mm": engine.project.smf_end_max_spacing_mm,
            "smf_max_longitudinal_ratio": engine.project.smf_max_longitudinal_ratio,
        },
        clear_cover_to_stirrup_outer_mm=clear_cover_to_stirrup_outer_mm,
        clear_cover_source=clear_cover_source,
        max_aggregate_size_mm=max_aggregate_size_mm,
        max_aggregate_size_source=max_aggregate_size_source,
        engine=engine,
        support_face_contexts=support_face_contexts,
        seismic_contexts=seismic_contexts,
        support_points=support_points,
        support_dimensions_mm=support_dimensions_mm,
    )


def build_payload_from_schedule(
    schedule: SpanScheduleMapping,
    beams: Iterable[BeamGeometry],
    *,
    section_loader: Any,
    frame_types: Mapping[str, str],
    resolved_frame_types: Mapping[str, str] | None = None,
    capacity_shear_envelope: Mapping[str, tuple[Any, ...]] | None = None,
    metadata: Mapping[str, Any],
    clear_cover_to_stirrup_outer_mm: float | None,
    clear_cover_source: str,
    max_aggregate_size_mm: float | None,
    max_aggregate_size_source: str,
    engine: BeamDetailingEngine | None = None,
    support_face_contexts: Mapping[str, tuple[SupportFaceContext | None, SupportFaceContext | None]] | None = None,
    seismic_contexts: Mapping[str, SeismicDetailingContext] | None = None,
    support_points: Mapping[str, tuple[float, float, float]] | None = None,
    support_dimensions_mm: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Pure-ish adapter used by both the ETABS workflow and unit tests."""

    engine = engine or BeamDetailingEngine()
    resolved_frame_types = resolved_frame_types or {}
    capacity_shear_envelope = capacity_shear_envelope or {}
    support_face_contexts = support_face_contexts or _derive_support_face_contexts(
        schedule.records,
        support_points,
        support_dimensions_mm,
        tolerance_mm=1.0,
    )
    by_name = {beam.name: beam for beam in beams}
    section_cache: dict[str, dict[str, Any]] = {}
    raw_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    contexts: list[tuple[dict[str, Any], Mapping[str, Any] | None]] = []
    detailing_results: list[BeamDetailingResult | None] = []

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
            blocked_reason = "MIXED_SECTION_REVIEW_REQUIRED"
        if len(source_frame_types) > 1:
            warnings.append("MIXED_FRAME_TYPE")
            blocked_reason = blocked_reason or "MIXED_FRAME_TYPE_REVIEW_REQUIRED"
        if "Program Determined" in source_frame_types:
            warnings.append("FRAME_TYPE_PROGRAM_DETERMINED")
        resolved_source_types = tuple(
            dict.fromkeys(
                str(resolved_frame_types.get(name, "")).strip()
                for name in span.source_objects
                if str(resolved_frame_types.get(name, "")).strip()
            )
        )
        if resolved_source_types:
            if "Program Determined" in source_frame_types:
                if any(_normalize_frame_type_label(value) not in {"sway special", "program determined"}
                       for value in resolved_source_types):
                    warnings.append("PROGRAM_DEFAULT_MODEL_CONFLICT")
            elif any(
                _normalize_frame_type_label(value) != _normalize_frame_type_label(source_frame_types[index])
                for index, value in enumerate(resolved_source_types)
                if index < len(source_frame_types)
            ):
                warnings.append("FRAME_TYPE_API_CONFLICT")
        if detailing_profile is BeamDetailingProfile.UNRESOLVED:
            warnings.append("DETAILING_PROFILE_REVIEW_REQUIRED")
            blocked_reason = blocked_reason or "FRAME_TYPE_UNRESOLVED"

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
                "etabs_longitudinal_cover_mm": None,
                "etabs_longitudinal_cover_source": "",
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
            "etabs_longitudinal_cover_mm": section.get("etabs_longitudinal_cover_mm"),
            "cover_calculation_basis": (
                "CLEAR_TO_STIRRUP_OUTER"
                if clear_cover_to_stirrup_outer_mm is not None
                else "ETABS_LONGITUDINAL_CENTROID"
            ),
            "etabs_longitudinal_cover_source": section.get(
                "etabs_longitudinal_cover_source",
                "ETABS PropFrame.GetRebarBeam",
            ),
            "clear_cover_to_stirrup_outer_mm": clear_cover_to_stirrup_outer_mm,
            "clear_cover_source": clear_cover_source,
            "fc_mpa": section.get("fc_mpa"),
            "fy_mpa": section.get("fy_mpa"),
            "fyt_mpa": section.get("fyt_mpa"),
            "max_aggregate_size_mm": max_aggregate_size_mm,
            "max_aggregate_size_source": max_aggregate_size_source,
            "frame_type": ", ".join(source_frame_types),
            "resolved_frame_type": ", ".join(
                dict.fromkeys(
                    resolved_frame_types.get(name, "")
                    for name in span.source_objects
                    if resolved_frame_types.get(name, "")
                )
            ),
            "detailing_profile": detailing_profile.value,
            "effective_frame_type": detailing_profile.value,
            "profile_source": profile_source,
            "frame_type_source": (
                "project default: Program Determined → SMF"
                if "Program Determined" in source_frame_types
                else "model-defined overwrite"
            ),
            "beam_role": span.beam_type,
            "is_smf": detailing_profile.is_smf,
            "source_objects": ", ".join(span.source_objects),
            "design_status": record.design.status.value,
            "design_status_message": record.design.status_message,
            "design_result_source": record.design.result_source,
            "design_units": record.design.units,
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
        if record.design.status is DesignResultStatus.API_ERROR:
            status = status or "DESIGN_RESULT_API_ERROR"
        elif record.design.status is DesignResultStatus.PARTIAL:
            status = status or "DESIGN_RESULTS_PARTIAL"
        elif record.design.status is DesignResultStatus.SKIPPED:
            status = status or "DESIGN_RESULT_SKIPPED"
        elif record.design.status is DesignResultStatus.NO_RESULTS:
            status = status or "NO_RESULTS"
        semantics_status = _detailing_input_status(common)
        status = status or semantics_status
        if controls is None:
            status = status or "NO_RESULTS"
        if status:
            raw["engine_status"] = status
            raw_rows.append(raw)
            review_rows.append(_empty_review(common, status, controls))
            final_rows.append(_empty_final(common, status, controls))
            contexts.append((common, controls))
            detailing_results.append(None)
            continue

        try:
            demands = tuple(_engine_demand(name, controls[name]) for name in REGIONS)
            support_pair = _support_faces_for_span(
                support_face_contexts,
                f"{span.beam_line_id}-{span.span_index}",
                span.span_id,
            )
            seismic_context = _seismic_context_for_span(
                seismic_contexts,
                f"{span.beam_line_id}-{span.span_index}",
                span.span_id,
            )
            api_points = tuple(
                point
                for name in span.source_objects
                for point in capacity_shear_envelope.get(name, ())
            )
            api_demand = max(
                (abs(float(point.shear_kN)) for point in api_points),
                default=None,
            )
            api_source = (
                "ETABS API: Concrete Beam Shear Envelope - ACI 318-14"
                if api_demand is not None
                else ""
            )
            if seismic_context is None and detailing_profile.is_smf:
                # Keep the ETABS result provenance explicit even when callers
                # do not provide a custom seismic context.  The capacity
                # source remains unverified until the design settings prove
                # that ETABS included 18.6.5 capacity shear.
                seismic_context = SeismicDetailingContext(
                    is_smf=True,
                    frame_type=(source_frame_types[0] if source_frame_types else ""),
                    design_code=str(metadata.get("design_code", "ACI 318-14")),
                    capacity_shear_source=(
                        api_source
                        or f"{record.design.result_source or 'GetSummaryResultsBeam_2'}.VMajorArea"
                    ),
                    capacity_shear_verified=api_demand is not None,
                    capacity_shear_demand_kN=api_demand,
                    mpr_strength_factor=1.25,
                    mpr_strength_source="ACI 318-14 SMF probable-strength screening factor 1.25; automatic selected reinforcement",
                    source=(
                        f"ETABS design status={record.design.status.value}; "
                        f"units={record.design.units or 'N_mm_C'}"
                    ),
                )
            if seismic_context is not None and detailing_profile.is_smf and api_demand is not None:
                seismic_context = replace(
                    seismic_context,
                    capacity_shear_demand_kN=api_demand,
                    capacity_shear_source=api_source,
                    capacity_shear_verified=True,
                )
            result = engine.design(
                BeamDetailingInput(
                    BeamSection(
                        common["beam_no"],
                        float(section["width_mm"]),
                        float(section["depth_mm"]),
                        (
                            float(clear_cover_to_stirrup_outer_mm)
                            if clear_cover_to_stirrup_outer_mm is not None
                            else None
                        ),
                        float(section["fc_mpa"]),
                        float(section["fy_mpa"]),
                        float(section["fyt_mpa"]),
                        float(max_aggregate_size_mm),
                        geometry.length,
                        detailing_profile,
                        float(section["etabs_longitudinal_cover_mm"]),
                        str(section.get("etabs_longitudinal_cover_source", "")),
                        clear_cover_source,
                        max_aggregate_size_source,
                        profile_source,
                        span.beam_type,
                    ),
                    demands,
                    left_support=support_pair[0],
                    right_support=support_pair[1],
                    seismic_context=seismic_context,
                    yield_locations_mm=(
                        seismic_context.likely_yield_locations_mm if seismic_context is not None else ()
                    ),
                )
            )
            raw["engine_status"] = "OK"
            raw["engine_mode"] = result.mode.value
            _record_initial_layout(raw, result)
            review = _review_row(common, controls, result)
            final = _final_row(common, result)
        except (DetailingError, ValueError) as exc:
            status = str(exc)
            raw["engine_status"] = status
            review = _empty_review(common, status, controls)
            final = _empty_final(common, status, controls)
            result = None
        raw_rows.append(raw)
        review_rows.append(review)
        final_rows.append(final)
        contexts.append((common, controls))
        detailing_results.append(result)

    detailing_results, coordination_errors = _coordinate_results_by_beam_line(
        detailing_results,
        contexts,
        engine,
    )
    for index, result in enumerate(detailing_results):
        common, controls = contexts[index]
        if index in coordination_errors:
            status = coordination_errors[index]
            raw_rows[index]["engine_status"] = status
            raw_rows[index].pop("engine_mode", None)
            review_rows[index] = _empty_review(common, status, controls)
            final_rows[index] = _empty_final(common, status, controls)
        elif result is not None and controls is not None:
            raw_rows[index]["engine_status"] = "OK"
            raw_rows[index]["engine_mode"] = result.mode.value
            raw_rows[index].update(_constructability_row_fields(result))
            raw_rows[index].update(_uniform_bottom_fields(result))
            raw_rows[index]["coordination_status"] = result.coordination_status
            raw_rows[index]["continuity_option"] = (
                result.continuity_option.value if result.continuity_option is not None else ""
            )
            raw_rows[index]["coordination_final_signature"] = "|".join(_result_layout_signature(result))
            review_rows[index] = _review_row(common, controls, result)
            final_rows[index] = _final_row(common, result)

    payload = {
        "metadata": dict(metadata),
        "raw_rows": raw_rows,
        "review_rows": review_rows,
        "final_rows": final_rows,
    }
    payload["metadata"].setdefault("schema_version", PAYLOAD_SCHEMA_VERSION)
    payload["metadata"].setdefault(
        "clear_cover_to_stirrup_outer_mm",
        clear_cover_to_stirrup_outer_mm,
    )
    payload["metadata"].setdefault("clear_cover_source", clear_cover_source)
    payload["metadata"].setdefault(
        "max_aggregate_size_mm",
        max_aggregate_size_mm,
    )
    payload["metadata"].setdefault(
        "max_aggregate_size_source",
        max_aggregate_size_source,
    )
    payload["metadata"].setdefault("minimum_bars_per_layer", 2)
    payload["metadata"].setdefault("minimum_main_diameter_mm", engine.project.minimum_main_diameter_mm)
    payload["metadata"].setdefault("smf_end_max_spacing_mm", engine.project.smf_end_max_spacing_mm)
    payload["metadata"].setdefault("smf_max_longitudinal_ratio", engine.project.smf_max_longitudinal_ratio)
    payload["metadata"].setdefault("hanger_diameter_mm", engine.project.sb_hanger_diameter_mm)
    _apply_current_workbook_metadata(payload["metadata"])
    return payload


def redetail_payload(
    payload: Mapping[str, Any],
    *,
    engine: BeamDetailingEngine | None = None,
) -> dict[str, Any]:
    """Re-run only the detailing engine from a previously extracted payload."""

    engine = engine or BeamDetailingEngine()
    schema_version = str(payload.get("metadata", {}).get("schema_version", ""))
    if schema_version not in SUPPORTED_PAYLOAD_SCHEMA_VERSIONS:
        raise DetailingError(
            f"UNSUPPORTED_PAYLOAD_SCHEMA: expected one of {sorted(SUPPORTED_PAYLOAD_SCHEMA_VERSIONS)}, "
            f"got {schema_version or 'missing'}"
        )
    output = json.loads(json.dumps(payload, ensure_ascii=False))
    raw_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    contexts: list[tuple[dict[str, Any], Mapping[str, Any] | None]] = []
    detailing_results: list[BeamDetailingResult | None] = []
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
            try:
                detailing_profile = BeamDetailingProfile(str(profile_value))
            except ValueError:
                detailing_profile = {
                    "PB_OMF": BeamDetailingProfile.OMF,
                    "PB_SMF": BeamDetailingProfile.SMF,
                    "PB_REVIEW_REQUIRED": BeamDetailingProfile.UNRESOLVED,
                    "SB_ORDINARY_NO_CH18": BeamDetailingProfile.SB_ORDINARY_NO_CH18,
                }.get(str(profile_value), BeamDetailingProfile.UNRESOLVED)
        elif str(common.get("beam_type", "")).upper() == "SB":
            detailing_profile = BeamDetailingProfile.SB_ORDINARY_NO_CH18
        elif bool(common.get("is_smf")):
            detailing_profile = BeamDetailingProfile.PB_SMF
        else:
            detailing_profile = BeamDetailingProfile.PB_OMF
        common["detailing_profile"] = detailing_profile.value
        common["is_smf"] = detailing_profile.is_smf
        raw["detailing_profile"] = detailing_profile.value
        raw["is_smf"] = detailing_profile.is_smf
        controls = {
            region: {key[len(region) + 1 :]: value for key, value in raw.items() if key.startswith(f"{region}_")}
            for region in REGIONS
        }
        prior_status = str(source.get("engine_status", ""))
        if prior_status and prior_status != "OK":
            status = prior_status
        elif any("top_total_mm2" not in controls[region] for region in REGIONS):
            status = "NO_RESULTS"
        else:
            status = _detailing_input_status(common)
        if status:
            raw["engine_status"] = status
            raw.pop("engine_mode", None)
            raw_rows.append(raw)
            review_rows.append(_empty_review(common, status, controls))
            final_rows.append(_empty_final(common, status, controls))
            contexts.append((common, controls))
            detailing_results.append(None)
            continue
        try:
            result = engine.design(
                BeamDetailingInput(
                    BeamSection(
                        str(common["beam_no"]),
                        float(common["width_mm"]),
                        float(common["depth_mm"]),
                        (
                            float(common["clear_cover_to_stirrup_outer_mm"])
                            if common.get("clear_cover_to_stirrup_outer_mm") is not None
                            else None
                        ),
                        float(common["fc_mpa"]),
                        float(common["fy_mpa"]),
                        float(common["fyt_mpa"]),
                        float(common["max_aggregate_size_mm"]),
                        float(common["length_mm"]),
                        detailing_profile,
                        float(common["etabs_longitudinal_cover_mm"]),
                        str(common["etabs_longitudinal_cover_source"]),
                        str(common["clear_cover_source"]),
                        str(common["max_aggregate_size_source"]),
                        str(common.get("profile_source", "")),
                        str(common.get("beam_role", common.get("beam_type", "PB"))),
                    ),
                    tuple(_engine_demand(region, controls[region]) for region in REGIONS),
                )
            )
            raw["engine_status"] = "OK"
            raw["engine_mode"] = result.mode.value
            _record_initial_layout(raw, result)
            review = _review_row(common, controls, result)
            final = _final_row(common, result)
        except (DetailingError, ValueError) as exc:
            status = str(exc)
            raw["engine_status"] = status
            raw.pop("engine_mode", None)
            review = _empty_review(common, status, controls)
            final = _empty_final(common, status, controls)
            result = None
        raw_rows.append(raw)
        review_rows.append(review)
        final_rows.append(final)
        contexts.append((common, controls))
        detailing_results.append(result)
    detailing_results, coordination_errors = _coordinate_results_by_beam_line(
        detailing_results,
        contexts,
        engine,
    )
    for index, result in enumerate(detailing_results):
        common, controls = contexts[index]
        if index in coordination_errors:
            status = coordination_errors[index]
            raw_rows[index]["engine_status"] = status
            raw_rows[index].pop("engine_mode", None)
            review_rows[index] = _empty_review(common, status, controls)
            final_rows[index] = _empty_final(common, status, controls)
        elif result is not None and controls is not None:
            raw_rows[index]["engine_status"] = "OK"
            raw_rows[index]["engine_mode"] = result.mode.value
            raw_rows[index].update(_uniform_bottom_fields(result))
            raw_rows[index]["coordination_status"] = result.coordination_status
            raw_rows[index]["continuity_option"] = (
                result.continuity_option.value if result.continuity_option is not None else ""
            )
            raw_rows[index]["coordination_final_signature"] = "|".join(_result_layout_signature(result))
            review_rows[index] = _review_row(common, controls, result)
            final_rows[index] = _final_row(common, result)
    output["raw_rows"] = raw_rows
    output["review_rows"] = review_rows
    output["final_rows"] = final_rows
    output.setdefault("metadata", {})["minimum_bars_per_layer"] = 2
    output["metadata"]["schema_version"] = PAYLOAD_SCHEMA_VERSION
    output["metadata"]["minimum_main_diameter_mm"] = engine.project.minimum_main_diameter_mm
    output["metadata"]["smf_end_max_spacing_mm"] = engine.project.smf_end_max_spacing_mm
    output["metadata"]["smf_max_longitudinal_ratio"] = engine.project.smf_max_longitudinal_ratio
    output["metadata"]["hanger_diameter_mm"] = engine.project.sb_hanger_diameter_mm
    _apply_current_workbook_metadata(output["metadata"])
    return output


def build_secondary_schedule_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the SB payload while retaining the complete backend audit rows.

    ``schedule_rows`` and ``exception_rows`` remain available for diagnostics and
    backward-compatible consumers. The renderer receives aligned filtered
    ``raw_rows`` / ``review_rows`` / ``final_rows`` and projects them to one
    static ``整理梁表`` sheet.
    """

    schema_version = str(payload.get("metadata", {}).get("schema_version", ""))
    if schema_version not in SUPPORTED_PAYLOAD_SCHEMA_VERSIONS:
        raise DetailingError(
            f"UNSUPPORTED_PAYLOAD_SCHEMA: expected one of {sorted(SUPPORTED_PAYLOAD_SCHEMA_VERSIONS)}, "
            f"got {schema_version or 'missing'}"
        )
    has_source_tables = all(key in payload for key in ("raw_rows", "review_rows", "final_rows"))
    raw_rows = list(payload.get("raw_rows", []))
    review_rows = list(payload.get("review_rows", []))
    final_rows = list(payload.get("final_rows", []))
    if has_source_tables and not (len(raw_rows) == len(review_rows) == len(final_rows)):
        raise DetailingError("SECONDARY_PAYLOAD_ROW_ALIGNMENT_ERROR")

    schedule_rows: list[dict[str, Any]] = []
    exception_rows: list[dict[str, Any]] = []
    secondary_indices: list[int] = []
    for index, source in enumerate(final_rows):
        if str(source.get("beam_type", "")).upper() != "SB":
            continue
        secondary_indices.append(index)
        row = dict(source)
        status = str(row.get("engine_status", ""))
        if status == "OK":
            if str(row.get("effective_frame_type", row.get("detailing_profile", ""))) == "UNRESOLVED":
                row["engine_status"] = "SB_FRAME_TYPE_UNRESOLVED"
                exception_rows.append(row)
                continue
            support = str(row.get("s2", "")).strip()
            middle = str(row.get("s1", "")).strip()
            if not support or support != middle:
                row["engine_status"] = "SB_SPAN_STIRRUP_NOT_UNIFIED"
                exception_rows.append(row)
                continue
            if (
                row.get("bottom_reinforcement_strategy") != BOTTOM_REINFORCEMENT_STRATEGY
                or row.get("bottom_uniform_regions_match") is not True
            ):
                row["engine_status"] = "LEGACY_REDETAIL_REQUIRED"
                exception_rows.append(row)
                continue
            row["stirrups"] = support
            schedule_rows.append(row)
        else:
            exception_rows.append(row)
    metadata = dict(payload.get("metadata", {}))
    metadata.update(
        {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "schedule_kind": "SECONDARY_BEAM_REINFORCEMENT_SCHEDULE",
            "stirrup_rule": "ONE_SPECIFICATION_PER_STRUCTURAL_SPAN",
            "secondary_schedule_rows": len(schedule_rows),
            "secondary_exception_rows": len(exception_rows),
            "secondary_workbook_layout": RESULT_WORKBOOK_LAYOUT_VERSION,
        }
    )
    _apply_current_workbook_metadata(metadata)

    # Exceptions can be discovered after the detailing engine has produced a
    # nominal row (for example, a non-unified SB stirrup). Propagate the final
    # exception status into every aligned JSON row so each static projection
    # fails closed and the整理梁表 does not present that row as deliverable.
    exception_status = {
        str(row.get("beam_no", "")): str(row.get("engine_status", ""))
        for row in exception_rows
        if row.get("beam_no")
    }
    filtered_raw: list[dict[str, Any]] = []
    filtered_review: list[dict[str, Any]] = []
    filtered_final: list[dict[str, Any]] = []
    for index in secondary_indices:
        if not has_source_tables:
            break
        final = dict(final_rows[index])
        beam_no = str(final.get("beam_no", ""))
        override = exception_status.get(beam_no)
        if override:
            final["engine_status"] = override
        raw = dict(raw_rows[index])
        review = dict(review_rows[index])
        if override:
            raw["engine_status"] = override
            review["engine_status"] = override
        filtered_raw.append(raw)
        filtered_review.append(review)
        filtered_final.append(final)

    return {
        "metadata": metadata,
        "raw_rows": filtered_raw,
        "review_rows": filtered_review,
        "final_rows": filtered_final,
        "schedule_rows": schedule_rows,
        "exception_rows": exception_rows,
    }


def project_result_table_payload(
    payload: Mapping[str, Any],
    beam_type: str,
) -> dict[str, Any]:
    """Return an aligned PB/SB view without mutating the full audit payload."""

    schema_version = str(payload.get("metadata", {}).get("schema_version", ""))
    if schema_version not in SUPPORTED_PAYLOAD_SCHEMA_VERSIONS:
        raise DetailingError(
            f"UNSUPPORTED_PAYLOAD_SCHEMA: expected one of {sorted(SUPPORTED_PAYLOAD_SCHEMA_VERSIONS)}, "
            f"got {schema_version or 'missing'}"
        )
    raw_rows = list(payload.get("raw_rows", []))
    review_rows = list(payload.get("review_rows", []))
    final_rows = list(payload.get("final_rows", []))
    if not (len(raw_rows) == len(review_rows) == len(final_rows)):
        raise DetailingError("RESULT_PROJECTION_ROW_ALIGNMENT_ERROR")
    role = str(beam_type).strip().upper()
    if role not in {"PB", "SB"}:
        raise DetailingError(f"RESULT_PROJECTION_UNKNOWN_BEAM_TYPE: {beam_type}")
    indices = [
        index
        for index, row in enumerate(final_rows)
        if str(row.get("beam_type", "")).strip().upper() == role
    ]
    metadata = dict(payload.get("metadata", {}))
    metadata["beam_type_filter"] = role
    metadata["result_projection"] = "STATIC_SINGLE_SHEET"
    return {
        "metadata": metadata,
        "raw_rows": [dict(raw_rows[index]) for index in indices],
        "review_rows": [dict(review_rows[index]) for index in indices],
        "final_rows": [dict(final_rows[index]) for index in indices],
        "schedule_rows": [
            dict(row)
            for row in payload.get("schedule_rows", [])
            if str(row.get("beam_type", "")).strip().upper() == role
        ],
        "exception_rows": [
            dict(row)
            for row in payload.get("exception_rows", [])
            if str(row.get("beam_type", "")).strip().upper() == role
        ],
    }


def _coordinate_results_by_beam_line(
    results: list[BeamDetailingResult | None],
    contexts: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    engine: BeamDetailingEngine,
) -> tuple[list[BeamDetailingResult | None], dict[int, str]]:
    output = list(results)
    errors: dict[int, str] = {}
    groups: dict[str, list[int]] = {}
    for index, (common, _) in enumerate(contexts):
        if output[index] is None:
            continue
        groups.setdefault(str(common.get("beam_line", "")), []).append(index)

    for indexes in groups.values():
        indexes.sort(key=lambda index: int(contexts[index][0].get("span_no", 0)))
        if len(indexes) < 2:
            continue
        try:
            max_rounds = max(2, len(indexes) * 3)
            for _ in range(max_rounds):
                before = tuple(_result_layout_signature(output[index]) for index in indexes)
                for first_index, second_index in zip(indexes, indexes[1:]):
                    first_span = int(contexts[first_index][0].get("span_no", 0))
                    second_span = int(contexts[second_index][0].get("span_no", 0))
                    if second_span != first_span + 1:
                        continue
                    first = output[first_index]
                    second = output[second_index]
                    if first is None or second is None:
                        continue
                    output[first_index], output[second_index] = coordinate_adjacent_spans(
                        first,
                        second,
                        engine.project.default_continuity_option,
                        engine,
                    )
                after = tuple(_result_layout_signature(output[index]) for index in indexes)
                if after == before:
                    break
            else:
                raise DetailingError("ADJACENT_SPAN_COORDINATION_NOT_CONVERGED")
        except (DetailingError, ValueError) as exc:
            status = f"BEAM_LINE_COORDINATION_BLOCKED: {exc}"
            for index in indexes:
                output[index] = None
                errors[index] = status
    return output, errors


def _result_layout_signature(result: BeamDetailingResult | None) -> tuple[str, ...]:
    if result is None:
        return ()
    return tuple(value for region in result.regions for value in (region.top.notation, region.bottom.notation))


def _uniform_bottom_fields(result: BeamDetailingResult) -> dict[str, Any]:
    """Flatten the actual full-span bottom layout for audit and projection."""

    bottoms = tuple(region.bottom for region in result.regions)
    notations = tuple(design.notation for design in bottoms)
    first = bottoms[0]
    diameters = tuple(
        dict.fromkeys(
            bar.diameter_mm
            for design in bottoms
            for bar in design.bars
        )
    )
    return {
        "bottom_reinforcement_strategy": BOTTOM_REINFORCEMENT_STRATEGY,
        "bottom_uniform_regions_match": len(set(notations)) == 1,
        "bottom_uniform_text": first.notation,
        "bottom_uniform_required_mm2": max(design.required_area_mm2 for design in bottoms),
        "bottom_uniform_provided_mm2": first.provided_area_mm2,
        "bottom_uniform_diameters_mm": ",".join(str(value) for value in diameters),
        "bottom_uniform_layers": first.layers,
    }


def _record_initial_layout(
    raw: dict[str, Any],
    result: BeamDetailingResult,
) -> None:
    raw["iteration_status"] = result.iteration_status
    raw["iteration_count"] = result.iteration_count
    raw["coordination_initial_signature"] = "|".join(_result_layout_signature(result))
    raw.update(_uniform_bottom_fields(result))
    raw.update(_constructability_row_fields(result))


def _constructability_row_fields(result: BeamDetailingResult) -> dict[str, Any]:
    """Flatten the spatial detailing contract for JSON/Excel consumers."""

    constructability = result.constructability
    capacity_review = result.capacity_shear_review
    capacity_fields = {
        "capacity_shear_status": capacity_review.status if capacity_review else "",
        "capacity_shear_left_mpr_positive_kNm": capacity_review.left_mpr_positive_kNm if capacity_review else None,
        "capacity_shear_left_mpr_negative_kNm": capacity_review.left_mpr_negative_kNm if capacity_review else None,
        "capacity_shear_right_mpr_positive_kNm": capacity_review.right_mpr_positive_kNm if capacity_review else None,
        "capacity_shear_right_mpr_negative_kNm": capacity_review.right_mpr_negative_kNm if capacity_review else None,
        "capacity_shear_actual_kN": capacity_review.actual_capacity_shear_kN if capacity_review else None,
        "capacity_shear_vu_actual_rebar_kN": capacity_review.vu_from_actual_rebar_kN if capacity_review else None,
        "capacity_shear_etabs_kN": capacity_review.etabs_capacity_shear_kN if capacity_review else None,
        "capacity_shear_final_kN": capacity_review.final_capacity_shear_kN if capacity_review else None,
        "capacity_shear_gravity_kN": capacity_review.gravity_shear_kN if capacity_review else None,
        "capacity_shear_required_av_over_s": capacity_review.required_av_over_s_mm2_per_mm if capacity_review else None,
        "capacity_shear_control_source": capacity_review.control_source if capacity_review else "",
        "capacity_shear_source": capacity_review.source if capacity_review else "",
        "capacity_shear_vc_zero": capacity_review.vc_zero if capacity_review else None,
        "capacity_shear_message": capacity_review.message if capacity_review else "",
        "capacity_shear_review_json": json.dumps(
            capacity_review.as_mapping() if capacity_review else {},
            ensure_ascii=False,
        ),
    }
    if constructability is None:
        return {
            "constructability_status": "",
            "constructability_blocking_statuses": "",
            "constructability_zones_json": "[]",
            "constructability_hoops_json": "[]",
            "constructability_checks_json": "[]",
            "rebar_layout_geometry_json": "{}",
            "smf_check_statuses": "",
            **capacity_fields,
        }
    zones = {zone.name: zone for zone in constructability.zones}
    left = zones.get("smf_left_2h")
    right = zones.get("smf_right_2h")
    overlap = zones.get("smf_end_hoop_overlap")
    left_first = next(
        (hoop.location_mm for hoop in constructability.hoop_positions if hoop.is_first_from_support and hoop.location_mm <= (left.end_mm if left else float("inf"))),
        None,
    )
    right_first = next(
        (hoop.location_mm for hoop in reversed(constructability.hoop_positions) if hoop.is_first_from_support),
        None,
    )
    return {
        "constructability_status": constructability.status,
        "constructability_blocking_statuses": "; ".join(constructability.blocking_statuses),
        "constructability_zones_json": json.dumps(constructability.as_mapping()["zones"], ensure_ascii=False),
        "constructability_hoops_json": json.dumps(constructability.as_mapping()["hoop_positions"], ensure_ascii=False),
        "constructability_checks_json": json.dumps(constructability.as_mapping()["checks"], ensure_ascii=False),
        "rebar_layout_geometry_json": json.dumps(constructability.as_mapping().get("layout_geometry"), ensure_ascii=False),
        "smf_check_statuses": "; ".join(
            f"{check.rule_id}={check.status}" for check in result.smf_checks
        ),
        "left_support_face_mm": (
            left.start_mm if left else overlap.start_mm if overlap else None
        ),
        "left_first_hoop_mm": left_first or (overlap.first_hoop_location_mm if overlap else None),
        "left_2h_end_mm": left.end_mm if left else overlap.end_mm if overlap else None,
        "right_2h_start_mm": right.start_mm if right else overlap.start_mm if overlap else None,
        "right_first_hoop_mm": right_first or (overlap.end_mm - 50.0 if overlap else None),
        "right_support_face_mm": right.end_mm if right else overlap.end_mm if overlap else None,
        "left_2h_spacing_mm": left.selected_spacing_mm if left else overlap.selected_spacing_mm if overlap else None,
        "middle_spacing_mm": zones.get("smf_middle").selected_spacing_mm if zones.get("smf_middle") else None,
        "right_2h_spacing_mm": right.selected_spacing_mm if right else overlap.selected_spacing_mm if overlap else None,
        "constructability_zone_count": len(constructability.zones),
        "constructability_hoop_count": len(constructability.hoop_positions),
        **capacity_fields,
    }


def write_rebar_payload(payload: Mapping[str, Any], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination


def write_capacity_shear_audit(
    payload: Mapping[str, Any],
    envelope: Mapping[str, tuple[Any, ...]],
    path: str | Path,
    *,
    api_error: str = "",
) -> Path:
    """Write process-only SMF capacity-shear evidence as JSONL.

    The payload retains the numeric review internally so the detailing engine
    can select hoops, but the workbook renderers have an explicit allowlist
    that excludes these fields.  This file is therefore the durable audit
    boundary for API values, actual-rebar recalculation and fallback reasons.
    """

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows_by_beam = {
        str(row.get("beam_no", "")): row
        for row in payload.get("raw_rows", ())
        if row.get("beam_no")
    }
    final_by_beam = {
        str(row.get("beam_no", "")): row
        for row in payload.get("final_rows", ())
        if row.get("beam_no")
    }
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for beam_no, row in rows_by_beam.items():
            if str(row.get("effective_frame_type", row.get("detailing_profile", ""))) != "SMF":
                continue
            object_names = tuple(
                name.strip()
                for name in str(row.get("source_objects", "")).split(",")
                if name.strip()
            )
            points = [
                asdict(point)
                for name in object_names
                for point in envelope.get(name, ())
            ]
            final = final_by_beam.get(beam_no, {})
            record = {
                "schema": "etabs-beam-generator/capacity-shear@1",
                "beam_no": beam_no,
                "beam_line": row.get("beam_line", ""),
                "span_no": row.get("span_no"),
                "beam_role": row.get("beam_role", row.get("beam_type", "")),
                "etabs_frame_type": row.get("frame_type", ""),
                "resolved_frame_type": row.get("resolved_frame_type", ""),
                "effective_frame_type": row.get("effective_frame_type", "SMF"),
                "api_points": points,
                "api_error": api_error,
                "review": {
                    key: final.get(key)
                    for key in (
                        "capacity_shear_status",
                        "capacity_shear_actual_kN",
                        "capacity_shear_vu_actual_rebar_kN",
                        "capacity_shear_etabs_kN",
                        "capacity_shear_final_kN",
                        "capacity_shear_gravity_kN",
                        "capacity_shear_required_av_over_s",
                        "capacity_shear_control_source",
                        "capacity_shear_source",
                        "capacity_shear_vc_zero",
                    )
                },
            }
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return destination


def run_workbook_builder(
    payload_path: str | Path,
    output_path: str | Path,
    *,
    preview_dir: str | Path,
    node_executable: str | Path = "node",
    node_modules: str | Path | None = None,
    node_max_old_space_mb: int = 8192,
    builder: str = "python",
) -> Path:
    """Run the PB static single-sheet result workbook builder.

    ``builder="python"`` uses the portable openpyxl renderer (no node) and
    skips PNG previews.  ``builder="node"`` runs the tracked Node artifact-tool
    builder plus its preview renderer.
    """
    if builder == "python":
        from src.spreadsheet.render_rebar_workbook_xlsx import render_rebar_workbook_xlsx

        Path(preview_dir).mkdir(parents=True, exist_ok=True)
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
        return render_rebar_workbook_xlsx(payload, Path(output_path))
    if builder != "node":
        raise ValueError(f"unknown workbook builder: {builder}")

    output = _run_artifact_builder(
        BUILDER_PATH,
        payload_path,
        output_path,
        preview_dir=preview_dir,
        node_executable=node_executable,
        node_modules=node_modules,
        node_max_old_space_mb=node_max_old_space_mb,
    )
    return output


def run_secondary_workbook_builder(
    payload_path: str | Path,
    output_path: str | Path,
    *,
    preview_dir: str | Path,
    node_executable: str | Path = "node",
    node_modules: str | Path | None = None,
    node_max_old_space_mb: int = 8192,
    builder: str = "python",
) -> Path:
    """Generate the SB static single-sheet reinforcement workbook.

    ``builder="python"`` uses the portable renderer; ``builder="node"`` keeps
    the Node artifact-tool reference path. Both consume the same filtered SB
    raw/review/final payload and therefore produce the same workbook contract.
    """
    if builder == "python":
        from src.spreadsheet.render_secondary_beam_xlsx import render_secondary_beam_xlsx

        Path(preview_dir).mkdir(parents=True, exist_ok=True)
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
        return render_secondary_beam_xlsx(payload, Path(output_path))
    if builder != "node":
        raise ValueError(f"unknown workbook builder: {builder}")
    # The Node reference path intentionally uses the canonical main renderer.
    # The secondary payload is already filtered to SB rows, so both builders
    # produce the same static single-sheet contract.
    output = _run_artifact_builder(
        BUILDER_PATH,
        payload_path,
        output_path,
        preview_dir=preview_dir,
        node_executable=node_executable,
        node_modules=node_modules,
        node_max_old_space_mb=node_max_old_space_mb,
    )
    return output


def _run_artifact_builder(
    builder_path: Path,
    payload_path: str | Path,
    output_path: str | Path,
    *,
    preview_dir: str | Path,
    node_executable: str | Path,
    node_modules: str | Path | None,
    node_max_old_space_mb: int,
) -> Path:
    payload = Path(payload_path).resolve()
    output = Path(output_path).resolve()
    inspect_spill = Path(f"{output}.inspect.ndjson")
    previews = Path(preview_dir).resolve()
    if not builder_path.is_file():
        raise FileNotFoundError(f"找不到正式工作簿生成器：{builder_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    previews.mkdir(parents=True, exist_ok=True)
    if node_max_old_space_mb < 1024:
        raise ValueError("大型配筋工作簿的 Node 堆内存不得小于 1024 MB。")
    node_prefix = [
        str(node_executable),
        f"--max-old-space-size={int(node_max_old_space_mb)}",
    ]

    try:
        if node_modules is None:
            command = [
                *node_prefix,
                str(builder_path),
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
            builder = work_dir / builder_path.name
            shutil.copy2(builder_path, builder)
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
    finally:
        # Artifact-tool may create this large scratch file before a native
        # failure. Keep it out of successful and failed run directories.
        inspect_spill.unlink(missing_ok=True)


def _run_rebar_preview_builder(
    workbook_path: str | Path,
    preview_dir: str | Path,
    *,
    node_executable: str | Path,
    node_modules: str | Path | None,
    node_max_old_space_mb: int,
) -> None:
    """Render each large-workbook preview in an isolated Node process."""

    workbook = Path(workbook_path).resolve()
    previews = Path(preview_dir).resolve()
    if not workbook.is_file():
        raise FileNotFoundError(f"找不到待预览配筋工作簿：{workbook}")
    if not PREVIEW_BUILDER_PATH.is_file():
        raise FileNotFoundError(f"找不到工作簿预览生成器：{PREVIEW_BUILDER_PATH}")
    previews.mkdir(parents=True, exist_ok=True)
    preview_names = ("schedule",)
    node_prefix = [
        str(node_executable),
        f"--max-old-space-size={int(node_max_old_space_mb)}",
    ]

    def render_all(builder: Path, *, cwd: Path | None = None) -> None:
        for preview_name in preview_names:
            command = [
                *node_prefix,
                str(builder),
                str(workbook),
                preview_name,
                str(previews / f"{preview_name}.png"),
            ]
            for attempt in range(1, 4):
                try:
                    subprocess.run(command, cwd=cwd, check=True)
                    break
                except subprocess.CalledProcessError:
                    if attempt == 3:
                        raise
                    print(
                        f"PREVIEW_RETRY={preview_name};" f"attempt={attempt + 1};max_attempts=3",
                        flush=True,
                    )
                    time.sleep(1.0)

    if node_modules is None:
        render_all(PREVIEW_BUILDER_PATH)
        return

    modules = Path(node_modules).resolve()
    if not modules.is_dir():
        raise FileNotFoundError(f"找不到 artifact-tool node_modules：{modules}")
    with tempfile.TemporaryDirectory(prefix="etabs_beam_preview_") as temp_name:
        work_dir = Path(temp_name)
        builder = work_dir / PREVIEW_BUILDER_PATH.name
        shutil.copy2(PREVIEW_BUILDER_PATH, builder)
        link = work_dir / "node_modules"
        _link_node_modules(link, modules)
        try:
            render_all(builder, cwd=work_dir)
        finally:
            if link.exists():
                os.rmdir(link)


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
    values = api_values(
        sap_model.DatabaseTables.GetTableForDisplayArray(
            "Concrete Beam Overwrites - ACI 318-14",
            None,
            "",
            0,
            None,
            0,
            None,
        ),
        "DatabaseTables.GetTableForDisplayArray(Concrete Beam Overwrites - ACI 318-14)",
    )
    if len(values) != 5:
        raise EtabsApiError("Concrete Beam Overwrites: 返回参数数量异常。")
    _requested_fields, _table_version, included_fields, record_count, flat_data = values
    fields = [str(value) for value in list(included_fields or ())]
    data = [str(value) for value in list(flat_data or ())]
    if not fields or "UniqueName" not in fields or "FrameType" not in fields:
        raise EtabsApiError("Concrete Beam Overwrites: 缺少 UniqueName/FrameType 字段。")
    width = len(fields)
    if int(record_count) < 0 or len(data) != int(record_count) * width:
        raise EtabsApiError("Concrete Beam Overwrites: 记录数或数据宽度不一致。")
    unique_index = fields.index("UniqueName")
    type_index = fields.index("FrameType")
    return {data[offset + unique_index]: data[offset + type_index] for offset in range(0, len(data), width)}


def _resolved_frame_types(sap_model: Any) -> dict[str, str]:
    """Return ETABS' resolved seismic type when the host exposes it."""

    from src.design.etabs_result import EtabsResultProvider

    try:
        return EtabsResultProvider(sap_model).get_seismic_framing_types()
    except Exception:
        # The raw overwrite table remains the authoritative explicit model
        # definition.  A missing resolved API is an audit limitation, not a
        # reason to fail all offline-compatible exports.
        return {}


def _capacity_shear_envelope(sap_model: Any) -> dict[str, tuple[Any, ...]]:
    """Read the process-only ETABS shear envelope through the adapter."""

    from src.design.etabs_result import EtabsResultProvider

    try:
        return EtabsResultProvider(sap_model).get_beam_shear_envelope()
    except Exception:
        return {}


def _normalize_frame_type_label(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("-", " ").split())


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
        "etabs_top_longitudinal_cover_mm": float(rebar[2]),
        "etabs_bottom_longitudinal_cover_mm": float(rebar[3]),
        "etabs_longitudinal_cover_mm": max(float(rebar[2]), float(rebar[3])),
        "etabs_longitudinal_cover_source": (
            f"ETABS PropFrame.GetRebarBeam({section_name}) CoverTop/CoverBot; "
            "concrete face to longitudinal-reinforcement centroid; governing maximum used"
        ),
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


def _support_faces_for_span(
    contexts: Mapping[str, tuple[SupportFaceContext | None, SupportFaceContext | None]] | None,
    *keys: str,
) -> tuple[SupportFaceContext | None, SupportFaceContext | None]:
    if not contexts:
        return None, None
    for key in keys:
        value = contexts.get(key)
        if value is not None:
            if len(value) != 2:
                raise DetailingError(f"支承面上下文必须包含左右两个支承面：{key}")
            return value
    return None, None


def _seismic_context_for_span(
    contexts: Mapping[str, SeismicDetailingContext] | None,
    *keys: str,
) -> SeismicDetailingContext | None:
    if not contexts:
        return None
    for key in keys:
        value = contexts.get(key)
        if value is not None:
            return value
    return None


def _derive_support_face_contexts(
    records: Iterable[Any],
    support_points: Mapping[str, tuple[float, float, float]] | None,
    support_dimensions_mm: Mapping[str, float] | None,
    tolerance_mm: float,
) -> dict[str, tuple[SupportFaceContext | None, SupportFaceContext | None]]:
    """Convert ETABS support centres and column dimensions to span-face data."""

    if not support_points or not support_dimensions_mm:
        return {}
    points = {
        str(name): tuple(float(value) for value in point)
        for name, point in support_points.items()
    }
    dimensions = {
        str(name): float(value)
        for name, value in support_dimensions_mm.items()
        if float(value) > 0
    }

    def nearest(point: tuple[float, float, float]) -> tuple[str, float] | None:
        candidates = []
        for name, support in points.items():
            gap = math.sqrt(sum((point[index] - support[index]) ** 2 for index in range(3)))
            if gap <= max(tolerance_mm, 5.0):
                candidates.append((gap, name))
        if not candidates:
            return None
        gap, name = min(candidates)
        return name, gap

    output: dict[str, tuple[SupportFaceContext | None, SupportFaceContext | None]] = {}
    for record in records:
        span = record.span
        geometry = record.geometry
        if not span.path_points or len(span.path_points) < 2:
            continue
        left = nearest(span.path_points[0])
        right = nearest(span.path_points[-1])
        if left is None or right is None:
            continue
        left_dim = dimensions.get(left[0])
        right_dim = dimensions.get(right[0])
        if left_dim is None or right_dim is None:
            continue
        left_face = left_dim / 2.0
        right_face = geometry.length - right_dim / 2.0
        if not (0.0 <= left_face < right_face <= geometry.length):
            continue
        context = (
            SupportFaceContext(
                f"{left[0]}:face",
                left_face,
                0.0,
                left_dim,
                "ETABS column support point + conservative max T2/T3 dimension",
            ),
            SupportFaceContext(
                f"{right[0]}:face",
                right_face,
                geometry.length,
                right_dim,
                "ETABS column support point + conservative max T2/T3 dimension",
            ),
        )
        output[span.span_id] = context
        output[f"{span.beam_line_id}-{span.span_index}"] = context
    return output


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
        "as_provided_top_mm2": _maximum_available(station.top_provided_area for station in items),
        "as_provided_bottom_mm2": _maximum_available(station.bottom_provided_area for station in items),
        "raw_as_provided_top": _maximum_available(station.raw_top_provided_area for station in items),
        "raw_as_provided_bottom": _maximum_available(station.raw_bottom_provided_area for station in items),
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
        "source_objects": tuple(dict.fromkeys(station.source_object for station in items if station.source_object)),
        "units": ",".join(dict.fromkeys(station.units for station in items if station.units)),
        "result_source": ",".join(dict.fromkeys(station.result_source for station in items if station.result_source)),
        "design_errors": "; ".join(dict.fromkeys(station.error for station in items if station.error.strip())),
        "design_warnings": "; ".join(dict.fromkeys(station.warning for station in items if station.warning.strip())),
    }


def _maximum_available(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return max(available, default=None)


def _engine_demand(name: str, control: Mapping[str, Any]) -> BeamRegionDemand:
    return BeamRegionDemand(
        name=name,
        location_mm=float(control["top_total_station_mm"]),
        as_top_total_mm2=float(control["top_total_mm2"]),
        as_bottom_total_mm2=float(control["bottom_total_mm2"]),
        av_over_s_mm2_per_mm=float(control["av_over_s"]),
        at_over_s_mm2_per_mm=float(control["at_over_s"]),
        al_torsion_mm2=float(control["al_mm2"]),
        as_top_etabs_mm2=(
            float(control["as_provided_top_mm2"])
            if control.get("as_provided_top_mm2") is not None
            else None
        ),
        as_bottom_etabs_mm2=(
            float(control["as_provided_bottom_mm2"])
            if control.get("as_provided_bottom_mm2") is not None
            else None
        ),
        top_combo=str(control["top_total_combo"]),
        bottom_combo=str(control["bottom_total_combo"]),
        shear_combo=str(control["av_combo"]),
        torsion_combo=str(control["al_combo"]),
        source_objects=tuple(control.get("source_objects", ())),
        units=str(control.get("units", "N_mm_C")),
        shear_source=(
            f"{control.get('result_source') or 'GetSummaryResultsBeam_2'}.VMajorArea (Av/s)"
        ),
        as_flex_top_override_mm2=float(control["top_flex_mm2"]),
        as_flex_bottom_override_mm2=float(control["bottom_flex_mm2"]),
    )


def _detailing_input_status(common: Mapping[str, Any]) -> str:
    if common.get("clear_cover_to_stirrup_outer_mm") is not None:
        required = (
            common.get("clear_cover_to_stirrup_outer_mm"),
            common.get("max_aggregate_size_mm"),
        )
        sources = (
            common.get("clear_cover_source"),
            common.get("max_aggregate_size_source"),
        )
    else:
        required = (
            common.get("etabs_longitudinal_cover_mm"),
            common.get("max_aggregate_size_mm"),
        )
        sources = (
            common.get("etabs_longitudinal_cover_source"),
            common.get("max_aggregate_size_source"),
        )
    if any(value is None for value in required) or any(not str(source or "").strip() for source in sources):
        return "COVER_SEMANTICS_UNRESOLVED"
    try:
        if any(float(value) <= 0 for value in required):
            return "COVER_SEMANTICS_UNRESOLVED"
    except (TypeError, ValueError):
        return "COVER_SEMANTICS_UNRESOLVED"
    return ""


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
            "iteration_status": result.iteration_status,
            "iteration_count": result.iteration_count,
            "coordination_status": result.coordination_status,
            "continuity_option": (result.continuity_option.value if result.continuity_option is not None else ""),
        }
    )
    row.update(_constructability_row_fields(result))
    row.update(_uniform_bottom_fields(result))
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
        stirrup = by_name[region_name].stirrup
        row[f"{field}_av_required"] = stirrup.required_av_over_s_mm2_per_mm
        row[f"{field}_av_min"] = stirrup.minimum_av_over_s_mm2_per_mm
        row[f"{field}_combo_required"] = stirrup.required_combo_over_s_mm2_per_mm
        row[f"{field}_combo_provided"] = stirrup.provided_av_over_s_mm2_per_mm
        row[f"{field}_at_provided"] = stirrup.provided_at_over_s_mm2_per_mm
        row[f"{field}_maximum_spacing_mm"] = stirrup.maximum_spacing_mm
        row[f"{field}_governing_reason"] = stirrup.governing_reason
        row[f"{field}_final_main_db_mm"] = stirrup.final_main_bar_diameter_mm
        row[f"{field}_high_shear"] = stirrup.high_shear
        row[f"{field}_shear_source"] = by_name[region_name].demand.transverse_shear_source
        row[f"{field}_spacing_limits"] = "; ".join(f"{name}={value:.3f}" for name, value in stirrup.spacing_limits)
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
        **_constructability_row_fields(result),
        **_uniform_bottom_fields(result),
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
        "engine_warnings": "; ".join(result.warnings),
        "iteration_status": result.iteration_status,
        "iteration_count": result.iteration_count,
        "coordination_status": result.coordination_status,
        "continuity_option": (result.continuity_option.value if result.continuity_option is not None else ""),
    }


def _fallback_rebar_notation(area: Any, *, effective_frame_type: str = "") -> str:
    try:
        value = float(area)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        value = 0.0
    diameters = (20, 22, 25, 28, 32) if str(effective_frame_type).upper() == "SMF" else (16, 18, 20, 22, 25, 28, 32)
    for diameter in diameters:
        bar_area = math.pi * diameter * diameter / 4.0
        count = max(2, math.ceil(value / bar_area))
        if count <= 8:
            return f"{count}T{diameter}"
    diameter = diameters[-1]
    count = max(2, math.ceil(value / (math.pi * diameter * diameter / 4.0)))
    return f"{count}T{diameter}"


def _fallback_stirrup_text(value: Any, *, source: str) -> str:
    try:
        av = float(value)
    except (TypeError, ValueError):
        return "2T8@100"
    if not math.isfinite(av) or av < 0:
        return "2T8@100"
    for legs in (2, 4):
        for diameter in (8, 10, 12):
            provided = legs * math.pi * diameter * diameter / 4.0 / 100.0
            if provided + 1e-9 >= av:
                return f"{legs}T{diameter}@100"
    return "4T12@100"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _fallback_region_texts(
    controls: Mapping[str, Any] | None,
    common: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if not controls:
        effective_frame_type = str((common or {}).get("effective_frame_type", ""))
        minimum_bar = _fallback_rebar_notation(0, effective_frame_type=effective_frame_type)
        return {
            "et1": minimum_bar,
            "t1": minimum_bar,
            "et2": minimum_bar,
            "eb1": minimum_bar,
            "b1": minimum_bar,
            "eb2": minimum_bar,
            "s2": "2T8@100",
            "s1": "2T8@100",
            "face": f"{minimum_bar}（面筋候选）",
        }
    mapping = {
        "et1": ("left", "top_total_mm2"), "t1": ("middle", "top_total_mm2"), "et2": ("right", "top_total_mm2"),
        "eb1": ("left", "bottom_total_mm2"), "b1": ("middle", "bottom_total_mm2"), "eb2": ("right", "bottom_total_mm2"),
    }
    effective_frame_type = str((common or {}).get("effective_frame_type", ""))
    result = {
        field: _fallback_rebar_notation(
            controls.get(region, {}).get(key),
            effective_frame_type=effective_frame_type,
        )
        for field, (region, key) in mapping.items()
        if field not in {"eb1", "b1", "eb2"}
    }
    bottom_required = max(
        (_safe_float(controls.get(region, {}).get("bottom_total_mm2"), 0.0) for region in REGIONS),
        default=0.0,
    )
    bottom_text = _fallback_rebar_notation(
        bottom_required,
        effective_frame_type=effective_frame_type,
    )
    result.update({"eb1": bottom_text, "b1": bottom_text, "eb2": bottom_text})
    result["s2"] = _fallback_stirrup_text(
        max((controls.get(name, {}).get("av_over_s", 0.0) for name in ("left", "right")), default=None),
        source="ETABS横筋需求",
    )
    result["s1"] = _fallback_stirrup_text(controls.get("middle", {}).get("av_over_s"), source="ETABS横筋需求")
    result["face"] = "面筋：按项目规则需人工复核"
    return result


def _empty_review(
    common: Mapping[str, Any],
    status: str,
    controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = _fallback_region_texts(controls, common)
    required = {}
    if controls:
        required = {
            "et1_required_mm2": controls["left"].get("top_total_mm2"),
            "t1_required_mm2": controls["middle"].get("top_total_mm2"),
            "et2_required_mm2": controls["right"].get("top_total_mm2"),
            "eb1_required_mm2": controls["left"].get("bottom_total_mm2"),
            "b1_required_mm2": controls["middle"].get("bottom_total_mm2"),
            "eb2_required_mm2": controls["right"].get("bottom_total_mm2"),
        }
    return {
        **common,
        "engine_status": status,
        "mode": "ETABS_READBACK_CANDIDATE",
        "bottom_reinforcement_strategy": f"{BOTTOM_REINFORCEMENT_STRATEGY}_CANDIDATE_REQUIRES_REVIEW",
        "bottom_uniform_regions_match": None,
        "bottom_uniform_text": candidates["b1"],
        "legs": "需复核",
        "face_required_mm2": None,
        "face_text": candidates["face"],
        **{f"{field}_required_mm2": required.get(f"{field}_required_mm2") for field in ("et1", "t1", "et2", "eb1", "b1", "eb2")},
        **{f"{field}_text": candidates[field] for field in ("et1", "t1", "et2", "eb1", "b1", "eb2")},
        "s2_left_text": candidates["s2"],
        "s1_text": candidates["s1"],
        "s2_right_text": candidates["s2"],
        "s2_text": candidates["s2"],
        "engine_warnings": f"配筋候选已保留，但未通过构造校核；原因为：{status}",
        "review_note": "高亮复核：本行钢筋规格仅按面积要求形成候选，不代表满足净距、锚固、箍筋或抗震构造，不得直接用于施工图。",
        "constructability_status": status,
        "constructability_blocking_statuses": status,
        "constructability_zones_json": "[]",
        "constructability_hoops_json": "[]",
        "constructability_checks_json": "[]",
        "rebar_layout_geometry_json": "{}",
        "smf_check_statuses": "",
        "capacity_shear_status": status if status else "",
        "capacity_shear_review_json": "{}",
    }


def _empty_final(
    common: Mapping[str, Any],
    status: str,
    controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = _fallback_region_texts(controls, common)
    return {
        **common,
        "engine_status": status,
        "mode": "ETABS_READBACK_CANDIDATE",
        "bottom_reinforcement_strategy": f"{BOTTOM_REINFORCEMENT_STRATEGY}_CANDIDATE_REQUIRES_REVIEW",
        "bottom_uniform_regions_match": None,
        "bottom_uniform_text": candidates["b1"],
        **{
            field: candidates[field]
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
        "engine_warnings": f"配筋候选已保留，但未通过构造校核；原因为：{status}",
        "review_note": "高亮复核：本行钢筋规格仅按面积要求形成候选，不代表满足净距、锚固、箍筋或抗震构造，不得直接用于施工图。",
        "legs": "需复核",
        "constructability_status": status,
        "constructability_blocking_statuses": status,
        "constructability_zones_json": "[]",
        "constructability_hoops_json": "[]",
        "constructability_checks_json": "[]",
        "rebar_layout_geometry_json": "{}",
        "smf_check_statuses": "",
        "capacity_shear_status": status if status else "",
        "capacity_shear_review_json": "{}",
    }
