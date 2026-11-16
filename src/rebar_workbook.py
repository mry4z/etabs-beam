"""Build the versioned detailing payload and its static result-table views.

The Python side owns ETABS extraction, span aggregation and detailing.  It
writes a versioned JSON payload that retains raw demands, review evidence and
final rows.  Excel is only a static PB or SB result projection; it is not the
calculation or audit store.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import json
import logging
import math
import multiprocessing
import os
from dataclasses import asdict, replace
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping

from src.core.beam_line_span_analyzer import BeamLineSpanResult
from src.core.etabs_connector import EtabsApiError, api_values
from src.core.geometry_extractor import BeamGeometry
from src.core.numbering import canonical_span_id
from src.design.detailing import (
    ACI318_14_DetailingPolicy,
    BeamDetailingProfile,
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamRegionDemand,
    BeamSection,
    ProjectDetailingPolicy,
    BeamDetailingResult,
    DetailingError,
    coordinate_span_segment,
    resolve_detailing_profile,
)
from src.design.constructability import DesignResultProvenance, SeismicDetailingContext, SupportFaceContext
from src.design.parent_context import ParentSpanContext
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.design.region_results import (
    DRAWING_DETAILING_POLICY_ID,
    DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE,
    LEGACY_DETAILING_POLICY_ID,
    SUPPORTED_DETAILING_POLICY_IDS,
    build_region_controls as _region_controls,
)
from src.design.offline_style import (
    DRAWING_V3_POLICY_ID,
    DRAWING_V3_REVISION_ID,
    DRAWING_V3_SOLVER_REVISION_ID,
)
from src.excel_span_adapter import (
    SpanObjectPart,
    SpanScheduleMapping,
    SpanScheduleRecord,
    apply_valid_station_domains,
    build_span_schedule,
    section_runs,
)

PAYLOAD_SCHEMA_VERSION = "2.3"
SUPPORTED_PAYLOAD_SCHEMA_VERSIONS = frozenset({"2.0", "2.1", "2.2", PAYLOAD_SCHEMA_VERSION})
RESULT_WORKBOOK_LAYOUT_VERSION = "single-sheet-static-v1"
BOTTOM_REINFORCEMENT_STRATEGY = "FULL_SPAN_UNIFORM_PER_SPAN"
ADJACENT_SPAN_BOTTOM_STRATEGY = "INDEPENDENT_PER_SPAN"
REGIONS = ("left", "middle", "right")
_REGION_CONTROL_FIELDS = frozenset(
    {
        "top_total_mm2", "top_total_station_mm", "top_total_combo",
        "top_flex_mm2", "top_flex_station_mm", "top_flex_combo",
        "bottom_total_mm2", "bottom_total_station_mm", "bottom_total_combo",
        "bottom_flex_mm2", "bottom_flex_station_mm", "bottom_flex_combo",
        "as_min_top_mm2", "as_min_top_station_mm", "as_min_bottom_mm2",
        "top_demand_mm2", "top_demand_station_mm", "top_demand_source", "top_demand_combo",
        "as_provided_top_mm2", "as_provided_bottom_mm2", "raw_as_provided_top", "raw_as_provided_bottom",
        "av_over_s", "av_station_mm", "av_combo", "al_mm2", "al_station_mm", "al_combo",
        "at_over_s", "at_station_mm", "at_combo", "source_station_count", "source_objects",
        "units", "result_source", "design_errors", "design_warnings",
        "middle_top_window_start_parent_mm", "middle_top_window_end_parent_mm",
        "middle_top_window_station_count", "middle_top_window_invalid_reason",
        "middle_top_window_invalid_values",
        "middle_top_demand_basis",
    }
)
ACCEPTED_WARNING_CODES = ("MIXED_SECTION_REVIEW_REQUIRED",)
CONFIRMED_SB_NONSWAY_OVERRIDE_ID = "user_confirmed_all_sb_nonsway_20261025"
CONFIRMED_SB_NONSWAY_OVERRIDE_REASON = (
    "User confirmed all SB beams are nonsway ordinary beams for this plugin release."
)
BUILDER_PATH = Path(__file__).resolve().parent / "spreadsheet" / "build_rebar_workbook.mjs"
PREVIEW_BUILDER_PATH = Path(__file__).resolve().parent / "spreadsheet" / "render_rebar_workbook_preview.mjs"
SECONDARY_BUILDER_PATH = Path(__file__).resolve().parent / "spreadsheet" / "build_secondary_beam_workbook.mjs"
_LINE_WORKER_ENGINE: BeamDetailingEngine | None = None
_LOGGER = logging.getLogger(__name__)


def _engine_warning_text(common: Mapping[str, Any], warnings: Iterable[str]) -> str:
    accepted = str(common.get("accepted_warning_codes", "")).strip()
    current = "; ".join(str(value) for value in warnings if str(value).strip())
    return "; ".join(value for value in (accepted, current) if value)


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


def _engine_detailing_policy(engine: BeamDetailingEngine) -> str:
    policy_id = str(getattr(engine, "detailing_policy_id", LEGACY_DETAILING_POLICY_ID))
    if policy_id not in SUPPORTED_DETAILING_POLICY_IDS:
        raise DetailingError(f"UNSUPPORTED_DETAILING_POLICY: {policy_id}")
    return policy_id


def _resolve_detailing_engine(
    engine: BeamDetailingEngine | None,
    detailing_policy_id: str | None,
    *,
    ranking_mode: str | None = None,
    rules_path: str | None = None,
    detailing_policy_revision_id: str | None = None,
) -> BeamDetailingEngine:
    requested = detailing_policy_id
    if requested is not None and requested not in SUPPORTED_DETAILING_POLICY_IDS:
        raise DetailingError(f"UNSUPPORTED_DETAILING_POLICY: {requested}")
    if engine is None:
        if requested in (None, LEGACY_DETAILING_POLICY_ID):
            engine = BeamDetailingEngine()
        else:
            # The policy factory is supplied by the offline strategy engine.
            from src.design.detailing import make_detailing_engine
            from src.design.offline_style import DRAWING_POLICY_REVISION_R9_ID
            if requested == DRAWING_DETAILING_POLICY_ID and detailing_policy_revision_id == DRAWING_POLICY_REVISION_R9_ID:
                from src.design.lookup_catalog import LookupCatalog
                provider = LookupCatalog()
            else:
                provider = None

            engine = make_detailing_engine(
                detailing_policy_id=requested,
                ranking_mode=ranking_mode,
                rules_path=rules_path,
                detailing_policy_revision_id=detailing_policy_revision_id,
                candidate_lookup_provider=provider,
            )
    actual = _engine_detailing_policy(engine)
    if requested is not None and actual != requested:
        raise DetailingError(f"DETAILING_ENGINE_POLICY_MISMATCH: requested={requested}, engine={actual}")
    return engine


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
    detailing_policy_id: str | None = None,
) -> dict[str, Any]:
    """Build the complete JSON-ready workbook payload from one ETABS run."""

    return build_rebar_workbook_payload_from_domain(
        structural,
        beams,
        designs,
        section_loader=lambda name: _section_info(sap_model, name),
        frame_types=_frame_types(sap_model),
        resolved_frame_types=_resolved_frame_types(sap_model),
        capacity_shear_envelope=(
            {} if getattr(engine, "etabs_design_results_only", False)
            else _capacity_shear_envelope(sap_model)
        ),
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
        detailing_policy_id=detailing_policy_id,
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
    detailing_policy_id: str | None = None,
    sb_frame_type_override: str | None = None,
    rebar_workers: int = 1,
    line_result_callback: Any | None = None,
    v3_coverage_schedule: bool = False,
) -> dict[str, Any]:
    """Build the same workbook payload from any normalized read-model source."""

    engine = _resolve_detailing_engine(engine, detailing_policy_id)
    policy_id = _engine_detailing_policy(engine)
    if v3_coverage_schedule and policy_id != DRAWING_V3_POLICY_ID:
        raise DetailingError("V3_COVERAGE_SCHEDULE_REQUIRES_DRAWING_V3")
    engine.v3_coverage_schedule_enabled = bool(v3_coverage_schedule)
    beam_list = tuple(beams)
    schedule = build_span_schedule(
        structural,
        list(beam_list),
        designs,
        tolerance_mm,
        detailing_policy_id=policy_id,
        valid_station_domain=(
            policy_id == DRAWING_V3_POLICY_ID
            and getattr(engine, "detailing_policy_revision_id", None) == DRAWING_V3_REVISION_ID
        ),
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
            "sb_frame_type_override": sb_frame_type_override,
            "sb_frame_type_override_scope": "SB span rows only" if sb_frame_type_override else None,
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
        detailing_policy_id=policy_id,
        sb_frame_type_override=sb_frame_type_override,
        rebar_workers=rebar_workers,
        line_result_callback=line_result_callback,
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
    detailing_policy_id: str | None = None,
    sb_frame_type_override: str | None = None,
    rebar_workers: int = 1,
    line_result_callback: Any | None = None,
) -> dict[str, Any]:
    """Pure-ish adapter used by both the ETABS workflow and unit tests."""

    engine = _resolve_detailing_engine(engine, detailing_policy_id or schedule.detailing_policy_id)
    if isinstance(rebar_workers, bool) or int(rebar_workers) < 1:
        raise ValueError("rebar_workers must be a positive integer")
    policy_id = _engine_detailing_policy(engine)
    if schedule.detailing_policy_id != policy_id:
        raise DetailingError(
            f"SPAN_SCHEDULE_POLICY_MISMATCH: schedule={schedule.detailing_policy_id}, detailing={policy_id}"
        )
    use_valid_station_domain = (
        policy_id == DRAWING_V3_POLICY_ID
        and getattr(engine, "detailing_policy_revision_id", None) == DRAWING_V3_REVISION_ID
    )
    if use_valid_station_domain and schedule.records and any(
        record.station_domain is None for record in schedule.records
    ):
        schedule = apply_valid_station_domains(schedule)
    region_control_projection_mode = (
        DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE
        if policy_id == DRAWING_V3_POLICY_ID
        else "APPROVED_REGION_CONTROLS"
    )
    resolved_frame_types = resolved_frame_types or {}
    capacity_shear_envelope = capacity_shear_envelope or {}
    support_face_records = tuple(
        replace(
            record,
            span=record.original_span or record.span,
            geometry=record.original_geometry or record.geometry,
        )
        for record in schedule.records
    )
    support_face_contexts = support_face_contexts or _derive_support_face_contexts(
        support_face_records,
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
    parallel_work_inputs: list[BeamDetailingInput | None] = []

    parent_lengths = {
        canonical_span_id(
            record.span.story,
            record.span.beam_type,
            record.span.line_number,
            record.span.span_index,
        ): (
            record.station_domain.effective_length_mm
            if record.station_domain is not None
            else record.geometry.length
        )
        for record in schedule.records
    }
    parent_info = {}
    for parent in schedule.records:
        parts = section_runs(parent.object_parts)
        sections = {part.section.strip() for part in parts if part.section.strip()}
        if len(sections) <= 1:
            continue
        parent_id = canonical_span_id(parent.span.story, parent.span.beam_type,
                                      parent.span.line_number, parent.span.span_index)
        active_parts = [part for part in parts if part.valid_domain_status != "OUTSIDE_VALID_STATION_DOMAIN"]
        valid = bool(active_parts) and all(
            part.geometry is not None and not part.geometry_error for part in active_parts
        )
        if valid:
            valid = (abs(active_parts[0].parent_offset_mm) <= 1.0 and
                     abs(active_parts[-1].parent_offset_mm + active_parts[-1].geometry.length - parent.geometry.length) <= 1.0 and
                     all(abs(a.parent_offset_mm + a.geometry.length - b.parent_offset_mm) <= 1.0
                         for a, b in zip(active_parts, active_parts[1:])))
        try:
            ends = tuple(float(section_loader(part.section)["depth_mm"])
                         for part in (active_parts[0], active_parts[-1]))
            valid = valid and all(math.isfinite(value) and value > 0 for value in ends)
        except Exception:
            ends, valid = (0.0, 0.0), False
        parent_info[parent_id] = (valid, len(parts), *ends)
    for record in _records_for_payload(schedule.records, by_name):
        span = record.span
        geometry = record.geometry
        object_part = record.object_part
        station_domain = record.station_domain
        outside_valid_domain = bool(
            object_part is not None
            and object_part.valid_domain_status == "OUTSIDE_VALID_STATION_DOMAIN"
        )
        source_names = (object_part.geometry.source_names if object_part and object_part.geometry else
                        (object_part.source_object,) if object_part else span.source_objects)
        parent_source_sections = tuple(
            dict.fromkeys(by_name[name].section for name in span.source_objects if name in by_name)
        )
        source_sections = tuple(dict.fromkeys(by_name[name].section for name in source_names if name in by_name))
        if object_part and object_part.section and not source_sections:
            source_sections = (object_part.section,)
        primary_section = (
            object_part.section or geometry.section
            if object_part
            else (source_sections[0] if source_sections else geometry.section)
        )
        source_frame_types = tuple(dict.fromkeys(frame_types.get(name, "Unknown") for name in source_names))
        parent_frame_types = tuple(dict.fromkeys(frame_types.get(name, "Unknown") for name in span.source_objects))
        has_sb_override = span.beam_type == "SB" and bool(sb_frame_type_override)
        effective_frame_types = (str(sb_frame_type_override),) if has_sb_override else source_frame_types
        detailing_profile, profile_source = resolve_detailing_profile(span.beam_type, effective_frame_types)
        if has_sb_override:
            profile_source = f"explicit SB frame-type override: {sb_frame_type_override}"
        warnings: list[str] = []
        blocked_reason = ""
        multi_section_parent = len(parent_source_sections) > 1
        if multi_section_parent:
            warnings.append("MIXED_SECTION_PARENT")
        if len(source_sections) > 1:
            warnings.append("MIXED_SECTION")
        if len(parent_frame_types) > 1:
            warnings.append("MIXED_FRAME_TYPE")
            if not has_sb_override:
                blocked_reason = blocked_reason or "MIXED_FRAME_TYPE_REVIEW_REQUIRED"
        if "Program Determined" in source_frame_types:
            warnings.append("FRAME_TYPE_PROGRAM_DETERMINED")
        resolved_source_types = tuple(
            dict.fromkeys(
                str(resolved_frame_types.get(name, "")).strip()
                for name in source_names
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
            "beam_no": object_part.object_part_id if object_part else canonical_span_id(
                span.story, span.beam_type, span.line_number, span.span_index
            ),
            "parent_span_id": canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index),
            "parent_length_mm": parent_lengths.get(
                canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index),
                record.geometry.length,
            ),
            "parent_offset_mm": object_part.parent_offset_mm if object_part else 0.0,
            "object_part_id": object_part.object_part_id if object_part else "",
            "object_part_index": object_part.object_part_index if object_part else 0,
            "parent_source_objects": ", ".join(span.source_objects),
            "parent_sections": ", ".join(parent_source_sections),
            "parent_segment_count": parent_info.get(canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index), (True, 0))[1],
            "parent_end_depths_mm": (list(parent_info[canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index)][2:4])
                                     if canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index) in parent_info
                                     else None),
            "beam_line": span.beam_line_id,
            "span_no": span.span_index,
            "is_cantilever": span.is_cantilever,
            "story": span.story,
            "beam_type": span.beam_type,
            "section": primary_section,
            "all_sections": ", ".join(parent_source_sections if object_part else source_sections),
            "width_mm": section.get("width_mm"),
            "depth_mm": section.get("depth_mm"),
            "length_mm": (
                None if outside_valid_domain or (
                    station_domain is not None and station_domain.status != "AVAILABLE"
                ) else geometry.length if not object_part or object_part.geometry is not None else None
            ),
            "geometry_length_mm": (
                object_part.original_geometry_length_mm
                if object_part is not None and object_part.original_geometry_length_mm is not None
                else station_domain.geometry_length_mm if station_domain is not None
                else (record.original_geometry or geometry).length
            ),
            "parent_geometry_length_mm": (
                station_domain.geometry_length_mm if station_domain is not None
                else (record.original_geometry or geometry).length
            ),
            "effective_parent_length_mm": (
                station_domain.effective_length_mm if station_domain is not None else None
            ),
            "effective_length_mm": (
                None if outside_valid_domain or (
                    station_domain is not None and station_domain.status != "AVAILABLE"
                ) else geometry.length
            ),
            "valid_station_domain_status": (
                object_part.valid_domain_status if outside_valid_domain
                else station_domain.status if station_domain is not None else "NOT_APPLIED"
            ),
            "valid_station_domain_start_parent_mm": (
                station_domain.start_parent_mm if station_domain is not None else None
            ),
            "valid_station_domain_end_parent_mm": (
                station_domain.end_parent_mm if station_domain is not None else None
            ),
            "valid_station_domain_reason": station_domain.reason if station_domain is not None else "",
            "valid_station_domain_invalid_in_domain_count": (
                station_domain.invalid_in_domain_count if station_domain is not None else 0
            ),
            "valid_station_domain_unmapped_station_count": (
                station_domain.unmapped_station_count if station_domain is not None else 0
            ),
            "valid_station_domain_station_trace_json": (
                json.dumps(station_domain.station_trace, ensure_ascii=False, allow_nan=False)
                if station_domain is not None else ""
            ),
            "original_parent_offset_mm": (
                object_part.original_parent_offset_mm if object_part is not None
                else 0.0 if station_domain is not None else None
            ),
            "original_object_part_id": (
                object_part.original_object_part_id if object_part is not None else ""
            ),
            "original_object_part_index": (
                object_part.original_object_part_index if object_part is not None else None
            ),
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
            "originalFrameType": ", ".join(source_frame_types),
            "actual_frame_type": ", ".join(effective_frame_types),
            "frame_type_input_effective": ", ".join(effective_frame_types),
            "frame_type_override_applied": has_sb_override,
            "frame_type_override_id": (
                CONFIRMED_SB_NONSWAY_OVERRIDE_ID
                if has_sb_override and policy_id in {DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID}
                else ""
            ),
            "frame_type_override_source": (
                CONFIRMED_SB_NONSWAY_OVERRIDE_REASON
                if has_sb_override and policy_id in {DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID}
                else ""
            ),
            "resolved_frame_type": ", ".join(
                dict.fromkeys(
                    resolved_frame_types.get(name, "")
                    for name in source_names
                    if resolved_frame_types.get(name, "")
                )
            ),
            "detailing_profile": detailing_profile.value,
            "effective_frame_type": detailing_profile.value,
            "profile_source": profile_source,
            "frame_type_source": (
                f"explicit SB frame-type override: {sb_frame_type_override}"
                if has_sb_override
                else
                "project default: Program Determined → SMF"
                if "Program Determined" in source_frame_types
                else "model-defined overwrite"
            ),
            "beam_role": span.beam_type,
            "is_smf": detailing_profile.is_smf,
            "source_objects": ", ".join(source_names),
            "design_status": record.design.status.value,
            "design_status_message": record.design.status_message,
            "design_result_source": record.design.result_source,
            "design_units": record.design.units,
            "warnings": "; ".join(warnings),
            "accepted_warning_codes": "; ".join(
                ACCEPTED_WARNING_CODES if multi_section_parent else ()
            ),
        }
        domain_input_blocker = _station_domain_blocker(station_domain, object_part)
        controls = None if domain_input_blocker else _region_controls(
            record.design,
            geometry.length,
            float(section.get("depth_mm") or 0.0),
            detailing_policy_id=policy_id,
            parent_length_mm=float(common["parent_length_mm"]),
            segment_start_mm=float(common["parent_offset_mm"]),
            parent_end_depths_mm=(parent_info[common["parent_span_id"]][2:4]
                                  if common["parent_span_id"] in parent_info and parent_info[common["parent_span_id"]][0]
                                  else None),
            projection_mode=region_control_projection_mode,
        )
        raw = dict(common)
        parent_support_pair = _support_faces_for_span(
            support_face_contexts,
            f"{span.beam_line_id}-{span.span_index}",
            span.span_id,
        )
        if station_domain is not None and station_domain.status == "AVAILABLE":
            parent_support_pair = tuple(
                _translate_support_face_to_domain(
                    face,
                    float(station_domain.start_parent_mm),
                    float(station_domain.effective_length_mm),
                )
                for face in parent_support_pair
            )
        support_pair = (
            (
                _rebase_support_face(parent_support_pair[0], object_part.parent_offset_mm, geometry.length)
                if object_part.is_span_start
                else None,
                _rebase_support_face(parent_support_pair[1], object_part.parent_offset_mm, geometry.length)
                if object_part.is_span_end
                else None,
            )
            if object_part is not None
            else parent_support_pair
        )
        parent_context = None
        if object_part is not None and multi_section_parent and not domain_input_blocker:
            info = parent_info.get(common["parent_span_id"], (False, 0, 0.0, 0.0))
            if not info[0]:
                blocked_reason = blocked_reason or "PARENT_SECTION_COVERAGE_UNRESOLVED"
            else:
                parent_context = ParentSpanContext(common["parent_span_id"], float(common["parent_length_mm"]),
                                                   object_part.parent_offset_mm, info[2], info[3])
                support_pair = parent_support_pair
        seismic_context = _seismic_context_for_span(
            seismic_contexts,
            f"{span.beam_line_id}-{span.span_index}",
            span.span_id,
        )
        raw["detailing_context"] = {
            "left_support": asdict(support_pair[0]) if support_pair[0] else None,
            "right_support": asdict(support_pair[1]) if support_pair[1] else None,
            "seismic_context": asdict(seismic_context) if seismic_context else None,
            "parent_context": asdict(parent_context) if parent_context else None,
        }
        if controls is not None:
            for region_name, control in controls.items():
                for key, value in control.items():
                    raw[f"{region_name}_{key}"] = value

        skipped_reason = str(record.design.status_message or "").casefold()
        excluded_no_design = (
            record.design.status is DesignResultStatus.SKIPPED
            and ("no design" in skipped_reason or "excluded_no_design" in skipped_reason)
        )
        if excluded_no_design:
            excluded_diagnostics = [value for value in (blocked_reason, section_error) if value]
            if object_part is not None and object_part.geometry_error:
                excluded_diagnostics.append("OBJECT_PART_GEOMETRY_UNRESOLVED: " + object_part.geometry_error)
            if excluded_diagnostics:
                raw["excluded_no_design_diagnostics"] = "; ".join(dict.fromkeys(excluded_diagnostics))
            # A deliberate ETABS No Design result is outside the concrete-design
            # denominator even when retained source geometry has independent review notes.
            status = "EXCLUDED_NO_DESIGN"
        else:
            status = (
                "OUTSIDE_VALID_STATION_DOMAIN"
                if outside_valid_domain
                else blocked_reason or section_error
            )
            if object_part is not None and object_part.geometry_error:
                status = status or "OBJECT_PART_GEOMETRY_UNRESOLVED"
            if record.design.status is DesignResultStatus.API_ERROR:
                status = status or "DESIGN_RESULT_API_ERROR"
            elif record.design.status is DesignResultStatus.PARTIAL:
                status = status or "DESIGN_RESULTS_PARTIAL"
            elif record.design.status is DesignResultStatus.SKIPPED:
                status = status or "DESIGN_RESULT_SKIPPED"
            elif record.design.status is DesignResultStatus.NO_RESULTS:
                status = status or "NO_RESULTS"
            status = status or domain_input_blocker
            semantics_status = _detailing_input_status(common)
            status = status or semantics_status
            status = status or _demand_view_status(
                controls, policy_id, etabs_design_results_only=getattr(engine, "etabs_design_results_only", False),
            )
        if status:
            raw["engine_status"] = status
            raw_rows.append(raw)
            review_rows.append(_empty_review_for_policy(engine, common, status, controls, engine.project))
            final_rows.append(_empty_final_for_policy(engine, common, status, controls, engine.project))
            contexts.append((common, controls))
            detailing_results.append(None)
            parallel_work_inputs.append(None)
            continue

        work_input = None
        try:
            demands = tuple(_engine_demand(name, controls[name]) for name in REGIONS)
            api_points = tuple(
                point
                for name in source_names
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
            raw["detailing_context"] = {
                "left_support": asdict(support_pair[0]) if support_pair[0] else None,
                "right_support": asdict(support_pair[1]) if support_pair[1] else None,
                "seismic_context": asdict(seismic_context) if seismic_context else None,
                "parent_context": asdict(parent_context) if parent_context else None,
            }
            input_data = BeamDetailingInput(
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
                        actual_frame_type=str(common.get("frame_type_input_effective", common.get("frame_type", ""))),
                    ),
                    demands,
                    left_support=support_pair[0],
                    right_support=support_pair[1],
                    seismic_context=seismic_context,
                    parent_context=parent_context,
                    yield_locations_mm=(
                        seismic_context.likely_yield_locations_mm if seismic_context is not None else ()
                    ),
                )
            if getattr(engine, "etabs_design_results_only", False):
                input_data = engine._etabs_only_input(input_data)
                _clear_derived_capacity_fields(raw)
            work_input = input_data
            result = None
            pending_status = "DETAILING_PENDING"
            raw["engine_status"] = pending_status
            review = _empty_review_for_policy(engine, common, pending_status, controls, engine.project)
            final = _empty_final_for_policy(engine, common, pending_status, controls, engine.project)
        except (DetailingError, ValueError) as exc:
            status = str(exc)
            raw["engine_status"] = status
            _record_candidate_search_audit(raw, exc)
            review = _empty_review_for_policy(engine, common, status, controls, engine.project)
            final = _empty_final_for_policy(engine, common, status, controls, engine.project)
            result = None
        raw_rows.append(raw)
        review_rows.append(review)
        final_rows.append(final)
        contexts.append((common, controls))
        detailing_results.append(result)
        parallel_work_inputs.append(work_input)

    initial_results, detailing_results, coordination_errors, design_errors, effective_workers = (
        _dispatch_beam_line_work(
            parallel_work_inputs,
            contexts,
            engine,
            workers=int(rebar_workers),
            on_line_complete=line_result_callback,
        )
    )
    for index, error in design_errors.items():
        status = error["status"]
        raw_rows[index]["engine_status"] = status
        if error.get("candidate_search_audit") is not None:
            raw_rows[index]["candidate_search_audit_json"] = json.dumps(
                error["candidate_search_audit"], ensure_ascii=False,
            )
        review_rows[index] = _empty_review_for_policy(engine, contexts[index][0], status, contexts[index][1], engine.project)
        final_rows[index] = _empty_final_for_policy(engine, contexts[index][0], status, contexts[index][1], engine.project)
    for index, result in enumerate(initial_results):
        if result is None:
            continue
        common, controls = contexts[index]
        raw_rows[index]["engine_status"] = "OK"
        raw_rows[index]["engine_mode"] = result.mode.value
        raw_rows[index]["engine_warnings"] = _engine_warning_text(common, result.warnings)
        _record_initial_layout(raw_rows[index], result)
        review_rows[index] = _review_row(common, controls, result)
        final_rows[index] = _final_row(common, result)
    metadata = dict(metadata)
    metadata["rebar_parallelism"] = {
        "strategy": "PROCESS_PER_BEAM_LINE" if effective_workers > 1 else "SERIAL_PER_BEAM_LINE",
        "workers_requested": int(rebar_workers),
        "workers_effective": effective_workers,
        "beam_line_jobs": sum(
            1 for indices in _group_context_indices(contexts).values()
            if any(parallel_work_inputs[index] is not None for index in indices)
        ),
    }
    if hasattr(engine, "_last_rebar_performance"):
        metadata["rebar_performance"] = dict(engine._last_rebar_performance)
    for index, result in enumerate(detailing_results):
        common, controls = contexts[index]
        if index in coordination_errors:
            status = coordination_errors[index]
            raw_rows[index]["engine_status"] = status
            raw_rows[index].pop("engine_mode", None)
            review_rows[index] = _empty_review_for_policy(engine, common, status, controls, engine.project)
            final_rows[index] = _empty_final_for_policy(engine, common, status, controls, engine.project)
        elif result is not None and controls is not None:
            raw_rows[index]["engine_status"] = ("FEASIBLE_OPTIMIZATION_INCOMPLETE" if result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE" else "OK")
            raw_rows[index]["engine_mode"] = result.mode.value
            raw_rows[index]["engine_warnings"] = _engine_warning_text(common, result.warnings)
            raw_rows[index].update(_row_layout_fields(result))
            raw_rows[index].update(_constructability_row_fields(result))
            raw_rows[index].update(_uniform_bottom_fields(result))
            raw_rows[index]["coordination_status"] = result.coordination_status
            raw_rows[index]["continuity_option"] = (
                result.continuity_option.value if result.continuity_option is not None else ""
            )
            raw_rows[index]["coordination_final_signature"] = "|".join(_result_layout_signature(result))
            review_rows[index] = _review_row(common, controls, result)
            final_rows[index] = _final_row(common, result)
            if common.get("object_part_index") and len(
                tuple(filter(None, str(common.get("parent_sections", "")).split(", ")))
            ) > 1:
                if (result.coordination_status == "PARENT_COORDINATION_VERIFIED" or
                    (result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE" and any(
                        isinstance(item, dict) and item.get("strategy") == "PARENT_R7_STRUCTURED_PROPOSALS"
                        for item in result.coordination_audit))):
                    for row in (raw_rows[index], review_rows[index], final_rows[index]):
                        row["parent_continuity_status"] = "PARENT_COORDINATION_VERIFIED"
                        row["parent_continuity_review_required"] = False
                elif _v3_selected_row_has_only_no_design_parent_sibling(
                    index, raw_rows, parallel_work_inputs, contexts, engine,
                ):
                    for row in (raw_rows[index], review_rows[index], final_rows[index]):
                        _clear_external_scope_mixed_section_review(row)
                else:
                    _mark_multi_section_continuity_review(
                        raw_rows[index], review_rows[index], final_rows[index])

    _apply_v3_recommendation_row_audits(raw_rows, engine, review_rows, final_rows)
    payload = {
        "metadata": dict(metadata),
        "raw_rows": raw_rows,
        "review_rows": review_rows,
        "final_rows": final_rows,
    }
    payload["metadata"]["schema_version"] = PAYLOAD_SCHEMA_VERSION
    if policy_id == DRAWING_V3_POLICY_ID:
        payload["metadata"]["region_control_projection_mode"] = region_control_projection_mode
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
    if policy_id in {DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID} and sb_frame_type_override == "Non Sway":
        sb_source_objects = sorted(
            {
                name
                for record in schedule.records
                if record.span.beam_type == "SB"
                for name in record.span.source_objects
            }
        )
        payload["metadata"]["frame_type_override_manifest"] = {
            "schema": "frame-type-override-manifest@1",
            "override_id": CONFIRMED_SB_NONSWAY_OVERRIDE_ID,
            "user_confirmed": True,
            "user_confirmation_date": "2026-10-25",
            "reason": CONFIRMED_SB_NONSWAY_OVERRIDE_REASON,
            "scope": "SB span rows only; PB rows retain ETABS source frame type",
            "overridden_actual_frame_type": "Non Sway",
            "original_frame_types": {
                name: str(frame_types.get(name, "Unknown")) for name in sb_source_objects
            },
        }
    _record_detailing_policy_metadata(payload["metadata"], engine)
    if policy_id == DRAWING_V3_POLICY_ID:
        from src.design.v3 import V3_COVERAGE_SOLVER_REVISION_ID

        coverage_solver_revision = (
            V3_COVERAGE_SOLVER_REVISION_ID
            if getattr(engine, "v3_coverage_schedule_enabled", False)
            else engine.coordination_solver_revision_id
        )
        payload["metadata"]["v3_coverage_schedule_enabled"] = bool(
            getattr(engine, "v3_coverage_schedule_enabled", False)
        )
        payload["metadata"]["coordination_solver_revision_id"] = coverage_solver_revision
        policy_parameters = payload["metadata"].get("detailing_policy_parameters")
        if isinstance(policy_parameters, dict):
            policy_parameters["coordination_solver_revision_id"] = coverage_solver_revision
            policy_parameters["v3_coverage_schedule_enabled"] = payload["metadata"][
                "v3_coverage_schedule_enabled"
            ]
        for recommendation_set in payload["metadata"].get("v3_recommendation_sets", ()):
            if isinstance(recommendation_set, dict):
                recommendation_set["solver_revision_id"] = coverage_solver_revision
    _apply_current_workbook_metadata(payload["metadata"])
    return payload


def _records_for_payload(
    records: tuple[SpanScheduleRecord, ...],
    by_name: Mapping[str, BeamGeometry],
) -> tuple[SpanScheduleRecord, ...]:
    """Expand mixed-section parents into continuous section runs."""

    expanded: list[SpanScheduleRecord] = []
    for record in records:
        sections = {
            str(by_name[name].section).strip()
            for name in record.span.source_objects
            if name in by_name and str(by_name[name].section).strip()
        }
        if len(sections) <= 1 or not record.object_parts:
            expanded.append(record)
            continue
        for part in section_runs(record.object_parts):
            geometry = part.geometry or replace(
                record.geometry,
                name=part.object_part_id,
                label=part.object_part_id,
                section=part.section,
                depth=None,
                source_names=(part.source_object,),
            )
            expanded.append(
                replace(record, geometry=geometry, design=part.design, object_part=part)
            )
    return tuple(expanded)


def _record_detailing_policy_metadata(metadata: dict[str, Any], engine: BeamDetailingEngine) -> None:
    """Record selection policy only after calculation, never just projection."""

    detailing_policy_id = _engine_detailing_policy(engine)
    new_demand_view = detailing_policy_id in {DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID}
    ranking_mode = str(getattr(engine, "ranking_mode", "legacy"))
    revision_id = getattr(engine, "detailing_policy_revision_id", "legacy")
    revision_parameters = getattr(engine, "detailing_policy_parameters", {})
    rules_path = getattr(engine, "rules_path", None)
    if new_demand_view:
        style_library = getattr(engine, "offline_style_library", None)
        if style_library is not None:
            rules_path = style_library.path
            rules_payload = style_library.payload
        else:
            rules_path = Path(rules_path) if rules_path else (
                Path(__file__).resolve().parent
                / "design"
                / "policies"
                / f"{DRAWING_DETAILING_POLICY_ID}.json"
            )
            try:
                rules_payload = json.loads(Path(rules_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rules_payload = {}
    else:
        rules_payload = {}
    metadata.update(
        {
            "detailing_policy_id": detailing_policy_id,
            "detailing_policy_revision_id": revision_id,
            **({"coordination_solver_revision_id": engine.coordination_solver_revision_id} if getattr(engine, "coordination_solver_revision_id", None) else {}),
            "detailing_policy_parameters": dict(revision_parameters or {}),
            "ranking_mode": ranking_mode,
            "detailing_policy_rules_file": str(rules_path) if rules_path else None,
            "detailing_policy_rules_version": rules_payload.get("schema"),
            "detailing_policy_frozen_revision": (
                rules_payload.get("policy_id")
                if new_demand_view and rules_payload.get("policy_id") == detailing_policy_id else None
            ),
            "detailing_policy_frozen_on": (
                rules_payload.get("provenance", {}).get("user_approval")
                if new_demand_view and rules_payload.get("policy_id") == detailing_policy_id else None
            ),
            "detailing_policy_style_reference_policy_id": (
                rules_payload.get("policy_id") if detailing_policy_id == DRAWING_V3_POLICY_ID else None
            ),
            "detailing_policy_rules_used_for_ranking": (
                bool(new_demand_view and ranking_mode == "style" and rules_payload)
            ),
            "demand_projection_version": (
                "drawing_v3_valid_native_station_domain_parent_fraction"
                if detailing_policy_id == DRAWING_V3_POLICY_ID and revision_id == DRAWING_V3_REVISION_ID
                else "drawing_v3_parent_fraction" if detailing_policy_id == DRAWING_V3_POLICY_ID
                else "drawing_20260924_v1" if new_demand_view else "legacy_2h_regions"
            ),
            "valid_station_domain_policy_id": (
                "VALID_NATIVE_STATION_DOMAIN@1"
                if detailing_policy_id == DRAWING_V3_POLICY_ID and revision_id == DRAWING_V3_REVISION_ID
                else None
            ),
            "valid_station_domain_required_values": (
                ["TopFlex", "TopTotal", "BottomFlex", "BottomTotal", "Shear",
                 "TorsionLongitudinal", "TorsionTransverse"]
                if detailing_policy_id == DRAWING_V3_POLICY_ID and revision_id == DRAWING_V3_REVISION_ID
                else None
            ),
            "valid_station_domain_duplicate_policy": (
                "PRESERVE_ALL" if detailing_policy_id == DRAWING_V3_POLICY_ID
                and revision_id == DRAWING_V3_REVISION_ID else None
            ),
            "middle_top_parent_fraction": [3.0 / 8.0, 5.0 / 8.0] if new_demand_view else None,
            "middle_top_window_inclusive": True if new_demand_view else None,
            "demand_station_duplicate_policy": (
                "PRESERVE_ALL" if new_demand_view else "DEDUPLICATE_WITHIN_TOLERANCE"
            ),
            "detailing_policy_exceptions": list(getattr(engine, "detailing_policy_exceptions", ())),
            "project_policy_version": engine.project.version,
            "enabled_diameters_mm": list(engine.project.enabled_diameters_mm),
            "allowed_spacings_mm": list(engine.project.allowed_spacings_mm),
            "smf_allowed_spacings_mm": list(engine.project.smf_allowed_spacings_mm),
            "smf_shallow_min_stirrup_diameter_mm": engine.project.smf_shallow_min_stirrup_diameter_mm,
            "smf_spacing_depth_policy": "NOMINAL_BEAM_DEPTH_PROJECT_OVERRIDE",
            "preferred_stirrup_diameters_mm": list(engine.project.preferred_stirrup_diameters_mm),
            "sb_top_preferred_max_diameter_mm": engine.project.sb_top_preferred_max_diameter_mm,
            "sb_midspan_top_pair_strategy": "MATCHING_DIAMETER_PAIR",
            "top_first_row_strategy": "COMMON_DIAMETER_INDEPENDENT_COUNT_LOCAL_HOOP_SUPPORT",
            "pb_preferred_max_stirrup_legs": engine.project.pb_preferred_max_stirrup_legs,
            "pb_stirrup_selection_strategy": "SPACING_THEN_DIAMETER_THEN_LEGS",
        }
    )
    if detailing_policy_id == DRAWING_V3_POLICY_ID:
        metadata.update({
            "detailing_recommendation_scope": "RECOMMENDATION_SET_ONLY",
            "detailing_global_optimum_proven": False,
            "detailing_full_specification_compliance_claimed": False,
            "detailing_recommendation_limit": 10,
            "candidate_lookup_mode": getattr(engine, "candidate_lookup_mode", "auto"),
        })
        recommendation_sets = getattr(engine, "_last_v3_recommendation_sets", None)
        if recommendation_sets is not None:
            metadata["v3_recommendation_sets"] = recommendation_sets
    if str(metadata.get("schema_version", "")) == PAYLOAD_SCHEMA_VERSION:
        metadata["section_part_semantics"] = "CONTIGUOUS_SECTION_RUN_PARENT_I_TO_J"
    if getattr(engine, "etabs_design_results_only", False):
        metadata["transverse_reinforcement_source"] = "ETABS_DESIGN_RESULTS_ONLY"
        metadata["side_reinforcement_policy"] = "INDEPENDENT_CONSTRUCTION_ONLY"
        metadata["side_reinforcement_strength_credit"] = False
    else:
        for field in ("transverse_reinforcement_source", "side_reinforcement_policy", "side_reinforcement_strength_credit"):
            metadata.pop(field, None)
    if new_demand_view:
        metadata["raw_snapshot_scope"] = (
            "source DesignStations projected onto the fresh target-policy span schedule; "
            "regional raw station counts and maxima retained"
        )
    layout_dictionary = getattr(engine, "layout_dictionary_metadata", None)
    if layout_dictionary is not None:
        metadata["layout_dictionary"] = layout_dictionary
    else:
        # Replay starts from copied source metadata. Remove stale provenance so
        # an enumeration-based redetail is never reported as a dictionary lookup.
        metadata.pop("layout_dictionary", None)


def _apply_v3_recommendation_row_audits(raw_rows, engine, review_rows=None, final_rows=None) -> None:
    for index, fields in getattr(engine, "_last_v3_recommendation_row_audits", {}).items():
        if 0 <= int(index) < len(raw_rows):
            row_index = int(index)
            rows = [raw_rows[row_index]]
            if review_rows is not None and row_index < len(review_rows):
                rows.append(review_rows[row_index])
            if final_rows is not None and row_index < len(final_rows):
                rows.append(final_rows[row_index])
            for row in rows:
                row.update(fields)
                if fields.get("v3_parent_continuity_external_scope"):
                    row["parent_continuity_status"] = "EXTERNAL_SCOPE_NO_DESIGN"
                    row["parent_continuity_review_required"] = False
                    if row.get("coordination_status") == "V3_PARENT_COORDINATION_UNKNOWN_INPUT_BLOCKED":
                        row["coordination_status"] = "V3_PARENT_COORDINATION_EXTERNAL_SCOPE_NO_DESIGN"
                elif fields.get("v3_parent_continuity_unknown"):
                    status = "PARENT_COORDINATION_UNKNOWN_MISSING_SECTION"
                    row["parent_continuity_status"] = status
                    row["parent_continuity_review_required"] = True
                    if row.get("engine_status") in {"OK", "FEASIBLE_OPTIMIZATION_INCOMPLETE"}:
                        row["engine_status"] = status
            recommendation_status = str(fields.get("v3_recommendation_status", ""))
            if (recommendation_status == "PARTIAL_RECOMMENDATION_OUT_OF_DESIGN_SCOPE"
                    and _has_selected_v3_layout(raw_rows[row_index])):
                for row in rows:
                    note = (
                        "局部已设计分项保留；同父跨含ETABS明确标记No Design的对象。"
                        "该对象为设计范围外，父跨未声明为完整配筋设计。"
                    )
                    warning = "V3_PARENT_CONTINUITY_EXTERNAL_SCOPE_NO_DESIGN"
                    for field, value in (("review_note", note), ("engine_warnings", warning)):
                        previous = str(row.get(field, "")).strip()
                        if value not in previous:
                            row[field] = "; ".join(item for item in (previous, value) if item)
            elif (recommendation_status.startswith("PARTIAL_")
                    and _has_selected_v3_layout(raw_rows[row_index])):
                for row in rows:
                    note = (
                        f"需复核：本跨局部配筋已通过当前方案校核；整条梁线状态为 {recommendation_status}。"
                        "存在未求解跨或输入缺口，跨缺口连续性未知，不能视为整串协调完成。"
                    )
                    warning = f"V3_LINE_CONTINUITY_UNKNOWN: {recommendation_status}"
                    for field, value in (("review_note", note), ("engine_warnings", warning)):
                        previous = str(row.get(field, "")).strip()
                        if value not in previous:
                            row[field] = "; ".join(item for item in (previous, value) if item)


def _has_selected_v3_layout(row: Mapping[str, Any]) -> bool:
    if row.get("v3_result_selected") is not None:
        return row.get("v3_result_selected") is True
    if row.get("mode") == "NO_V3_RECOMMENDATION":
        return False
    longitudinal = row.get("longitudinal_rows")
    stirrups = row.get("region_stirrups")
    if not isinstance(longitudinal, Mapping) or not isinstance(stirrups, Mapping) or not stirrups:
        return False
    return any(
        isinstance(face_rows, (list, tuple)) and bool(face_rows)
        for region in longitudinal.values()
        if isinstance(region, Mapping)
        for face_rows in region.values()
    )


def _clear_external_scope_mixed_section_review(row: dict[str, Any]) -> None:
    """Drop only the resolved review marker; keep section provenance in warnings/audit."""

    for field in ("accepted_warning_codes", "engine_warnings"):
        values = [
            value.strip()
            for value in str(row.get(field, "")).split(";")
            if value.strip() and value.strip() != "MIXED_SECTION_REVIEW_REQUIRED"
        ]
        row[field] = "; ".join(values)


def _v3_selected_row_has_only_no_design_parent_sibling(
    index: int,
    raw_rows: list[dict[str, Any]],
    work_inputs: list[BeamDetailingInput | None],
    contexts: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    engine: BeamDetailingEngine,
) -> bool:
    """Allow a selected local result past the generic block only at an explicit NoDesign boundary."""

    if _engine_detailing_policy(engine) != DRAWING_V3_POLICY_ID or index >= len(contexts):
        return False
    fields = getattr(engine, "_last_v3_recommendation_row_audits", {}).get(index, {})
    if (
        fields.get("v3_parent_continuity_external_scope") is not True
        or fields.get("v3_parent_continuity_unknown") is True
        or fields.get("v3_recommendation_status") != "PARTIAL_RECOMMENDATION_OUT_OF_DESIGN_SCOPE"
        or index >= len(raw_rows)
        or index >= len(work_inputs)
        or work_inputs[index] is None
        or not _has_selected_v3_layout(raw_rows[index])
    ):
        return False

    common = contexts[index][0]
    parent_id = str(common.get("parent_span_id", "")).strip()
    if not parent_id:
        parent_id = f"{common.get('beam_line', '')}:{common.get('span_no', '')}"
    active_siblings = []
    for sibling_index, (sibling_common, _controls) in enumerate(contexts):
        sibling_id = str(sibling_common.get("parent_span_id", "")).strip()
        if not sibling_id:
            sibling_id = f"{sibling_common.get('beam_line', '')}:{sibling_common.get('span_no', '')}"
        if sibling_id == parent_id and sibling_index < len(work_inputs) and work_inputs[sibling_index] is not None:
            active_siblings.append(sibling_index)
    return active_siblings == [index]


def _replay_detailing_context(
    raw: Mapping[str, Any], metadata: Mapping[str, Any], profile: BeamDetailingProfile,
) -> dict[str, Any]:
    """Restore recorded input context; legacy payloads keep their missing evidence explicit."""
    saved = raw.get("detailing_context")
    if isinstance(saved, Mapping):
        seismic = saved.get("seismic_context")
        if seismic is not None:
            seismic = dict(seismic)
            if isinstance(seismic.get("provenance"), Mapping):
                seismic["provenance"] = DesignResultProvenance(**seismic["provenance"])
            seismic["likely_yield_locations_mm"] = tuple(seismic.get("likely_yield_locations_mm", ()))
            seismic = SeismicDetailingContext(**seismic)
        return {
            "left_support": SupportFaceContext(**saved["left_support"]) if saved.get("left_support") else None,
            "right_support": SupportFaceContext(**saved["right_support"]) if saved.get("right_support") else None,
            "seismic_context": seismic,
            "yield_locations_mm": tuple(saved.get("yield_locations_mm", ())),
            "parent_context": ParentSpanContext(**saved["parent_context"]) if saved.get("parent_context") else None,
        }
    context: dict[str, Any] = {}
    for side in ("left", "right"):
        face = raw.get(f"{side}_support_face_mm")
        if face is not None:
            context[f"{side}_support"] = SupportFaceContext(
                f"{raw.get('beam_no', '')}/{side}", float(face),
                source="legacy payload recorded support-face location",
            )
    if profile.is_smf:
        etabs_shear = raw.get("capacity_shear_etabs_kN")
        context["seismic_context"] = SeismicDetailingContext(
            is_smf=True,
            frame_type=str(raw.get("frame_type", "")),
            design_code=str(metadata.get("design_code", "ACI 318-14")),
            capacity_shear_demand_kN=float(etabs_shear) if etabs_shear is not None else None,
            capacity_shear_source=str(raw.get("capacity_shear_source", "")),
            capacity_shear_verified=etabs_shear is not None and bool(raw.get("capacity_shear_source")),
            gravity_shear_kN=raw.get("capacity_shear_gravity_kN"),
            mpr_strength_factor=1.25,
            mpr_strength_source="ACI 318-14 SMF probable-strength screening factor 1.25; automatic selected reinforcement",
            source="legacy raw_rows replay; missing context remains unresolved",
        )
    return context


def _raw_snapshot_designs_by_row(
    raw_snapshot: SpanScheduleMapping | Mapping[str, BeamDesignResult],
    target_policy_id: str,
    *, section_run_semantics: bool = False,
) -> dict[str, tuple[BeamDesignResult, float | None, float, float | None]]:
    """Resolve fresh, row-local designs and parent coordinates by payload key."""

    resolved: dict[str, tuple[BeamDesignResult, float | None, float, float | None]] = {}
    if isinstance(raw_snapshot, SpanScheduleMapping):
        if raw_snapshot.detailing_policy_id != target_policy_id:
            raise DetailingError(
                "CROSS_STRATEGY_RAW_SNAPSHOT_POLICY_MISMATCH: "
                f"schedule={raw_snapshot.detailing_policy_id}, target={target_policy_id}"
            )
        for record in raw_snapshot.records:
            parent_id = canonical_span_id(
                record.span.story,
                record.span.beam_type,
                record.span.line_number,
                record.span.span_index,
            )
            parent_length = record.geometry.length
            resolved[parent_id] = (record.design, parent_length, 0.0, parent_length)
            parts = section_runs(record.object_parts) if section_run_semantics else record.object_parts
            for part in parts:
                part_length = part.geometry.length if part.geometry is not None else None
                resolved[part.object_part_id] = (
                    part.design,
                    part_length,
                    float(part.parent_offset_mm),
                    parent_length,
                )
        return resolved

    if not isinstance(raw_snapshot, Mapping):
        raise DetailingError("CROSS_STRATEGY_RAW_SNAPSHOT_INVALID: expected a target-policy SpanScheduleMapping or row map")
    for key, design in raw_snapshot.items():
        if not isinstance(design, BeamDesignResult):
            raise DetailingError(
                "CROSS_STRATEGY_RAW_SNAPSHOT_INVALID: row-map values must be local-coordinate BeamDesignResult values"
            )
        resolved[str(key)] = (design, None, 0.0, None)
    return resolved


def _store_region_controls(raw: dict[str, Any], controls: Mapping[str, Mapping[str, Any]]) -> None:
    for key in tuple(raw):
        if any(key.startswith(f"{region}_") and key[len(region) + 1 :] in _REGION_CONTROL_FIELDS for region in REGIONS):
            raw.pop(key, None)
    for region_name, control in controls.items():
        for key, value in control.items():
            raw[f"{region_name}_{key}"] = value


def _demand_view_status(controls: Mapping[str, Mapping[str, Any]] | None, policy_id: str, *,
                        etabs_design_results_only: bool = False) -> str:
    if controls is None:
        return "NO_RESULTS"
    if policy_id in {DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID}:
        invalid_reason = str(controls["middle"].get("middle_top_window_invalid_reason", "")).strip()
        if invalid_reason:
            return f"DRAWING_MIDDLE_TOP_DEMAND_INVALID: {invalid_reason}"
        if controls["middle"].get("middle_top_window_station_count") == 0:
            return "DRAWING_MIDDLE_QUARTER_NO_STATIONS"
    if any(
        controls.get(region, {}).get("top_total_mm2") is None
        or controls.get(region, {}).get("top_total_station_mm") is None
        for region in REGIONS
    ):
        return "NO_RESULTS"
    if etabs_design_results_only:
        for region in REGIONS:
            control = controls.get(region, {})
            for field in ("top_total_mm2", "bottom_total_mm2", "av_over_s", "at_over_s", "al_mm2"):
                value = control.get(field)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    return "NO_RESULTS"
    return ""


def _station_domain_blocker(
    domain: Any,
    object_part: SpanObjectPart | None,
) -> str:
    if object_part is not None and object_part.valid_domain_status == "OUTSIDE_VALID_STATION_DOMAIN":
        return "OUTSIDE_VALID_STATION_DOMAIN"
    if domain is None:
        return ""
    if domain.status != "AVAILABLE":
        return "NO_VALID_STATION_DOMAIN"
    if domain.invalid_in_domain_count:
        return "VALID_STATION_DOMAIN_SOURCE_STATION_INVALID"
    if domain.unmapped_station_count:
        return "VALID_STATION_DOMAIN_SOURCE_STATION_UNMAPPED"
    return ""


def _clear_derived_capacity_fields(raw: dict[str, Any]) -> None:
    """Remove inherited calculation evidence; preserve source inputs and gates."""

    for key in tuple(raw):
        if key.startswith(("capacity_shear_", "constructability_", "side_reinforcement_")) or any(
            key.startswith(f"{region}_capacity_shear_") for region in REGIONS
        ):
            raw.pop(key, None)
    for key in ("rebar_layout_geometry_json", "smf_check_statuses", "detailing_policy_audit_json",
                "candidate_search_audit_json", "left_first_hoop_mm", "left_2h_end_mm",
                "right_2h_start_mm", "right_first_hoop_mm", "left_2h_spacing_mm",
                "middle_spacing_mm", "right_2h_spacing_mm"):
        raw.pop(key, None)
    # The two support-face coordinates remain replay geometry inputs for
    # historical rows without saved detailing_context. Source warnings and
    # engine/data gate statuses remain untouched.
    if isinstance(raw.get("engine_warnings"), str):
        raw["engine_warnings"] = "; ".join(
            token.strip() for token in raw["engine_warnings"].split(";")
            if token.strip() and token.strip() != "CAPACITY_SHEAR_SOURCE_UNVERIFIED"
        )
    saved = raw.get("detailing_context")
    if isinstance(saved, Mapping):
        saved = dict(saved)
        context = saved.get("seismic_context")
        if isinstance(context, Mapping):
            context = dict(context)
            for key in ("left_mpr_kNm", "right_mpr_kNm", "mpr_strength_factor",
                        "mpr_strength_source", "capacity_shear_final_kN",
                        "capacity_shear_required_av_over_s_mm2_per_mm", "capacity_shear_control_source"):
                context.pop(key, None)
            saved["seismic_context"] = context
        raw["detailing_context"] = saved


def redetail_payload(
    payload: Mapping[str, Any],
    *,
    engine: BeamDetailingEngine | None = None,
    raw_snapshot: SpanScheduleMapping | Mapping[str, BeamDesignResult] | None = None,
    allow_policy_revision_change: bool = False,
    rebar_workers: int = 1,
    line_result_callback: Any | None = None,
    candidate_lookup_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Re-run detailing, requiring explicit opt-in for a same-policy revision change."""

    payload_metadata = payload.get("metadata", {})
    if not isinstance(payload_metadata, Mapping):
        payload_metadata = {}
    schema_version = str(payload_metadata.get("schema_version", ""))
    if schema_version not in SUPPORTED_PAYLOAD_SCHEMA_VERSIONS:
        raise DetailingError(
            f"UNSUPPORTED_PAYLOAD_SCHEMA: expected one of {sorted(SUPPORTED_PAYLOAD_SCHEMA_VERSIONS)}, "
            f"got {schema_version or 'missing'}"
        )
    if isinstance(rebar_workers, bool) or int(rebar_workers) < 1:
        raise ValueError("rebar_workers must be a positive integer")
    source_policy_id = str(payload_metadata.get("detailing_policy_id", LEGACY_DETAILING_POLICY_ID))
    explicit_engine = engine is not None
    if engine is None:
        engine = _resolve_detailing_engine(
            None,
            source_policy_id,
            ranking_mode=(str(payload_metadata["ranking_mode"]) if payload_metadata.get("ranking_mode") else None),
            rules_path=(
                str(payload_metadata["detailing_policy_rules_file"])
                if payload_metadata.get("detailing_policy_rules_file")
                else None
            ),
            detailing_policy_revision_id=(
                str(payload_metadata["detailing_policy_revision_id"])
                if payload_metadata.get("detailing_policy_revision_id")
                else None
            ),
        )
    if candidate_lookup_output_dir is not None:
        engine.candidate_lookup_output_dir = candidate_lookup_output_dir
    if not explicit_engine and source_policy_id != LEGACY_DETAILING_POLICY_ID:
        source_revision_id = payload_metadata.get("detailing_policy_revision_id")
        target_revision_id = getattr(engine, "detailing_policy_revision_id", None)
        source_parameters = payload_metadata.get("detailing_policy_parameters")
        target_parameters = getattr(engine, "detailing_policy_parameters", None)
        if (
            not source_revision_id
            or source_revision_id != target_revision_id
            or not isinstance(source_parameters, Mapping)
            or dict(source_parameters) != dict(target_parameters or {})
        ):
            raise DetailingError(
                "DETAILING_POLICY_REVISION_MISMATCH: implicit replay requires the exact recorded "
                f"revision and effective parameters; source={source_revision_id!r}, target={target_revision_id!r}"
            )
    source_revision_id = payload_metadata.get("detailing_policy_revision_id")
    source_revision_parameters = payload_metadata.get("detailing_policy_parameters")
    target_revision_id = getattr(engine, "detailing_policy_revision_id", None)
    target_revision_parameters = getattr(engine, "detailing_policy_parameters", None)
    revision_changed = source_policy_id == _engine_detailing_policy(engine) and source_policy_id != LEGACY_DETAILING_POLICY_ID and (
        source_revision_id != target_revision_id
        or not isinstance(source_revision_parameters, Mapping)
        or dict(source_revision_parameters) != dict(target_revision_parameters or {})
    )
    if revision_changed and not allow_policy_revision_change:
        raise DetailingError(
            "DETAILING_POLICY_REVISION_MISMATCH: same-policy revision change requires explicit opt-in; "
            f"source={source_revision_id!r}, target={target_revision_id!r}"
        )
    target_policy_id = _engine_detailing_policy(engine)
    cross_strategy = source_policy_id != target_policy_id
    if cross_strategy and raw_snapshot is None:
        raise DetailingError(
            "CROSS_STRATEGY_REQUIRES_RAW_SNAPSHOT: reduced regional controls cannot reconstruct another demand view"
        )
    snapshot_rows = (
        _raw_snapshot_designs_by_row(raw_snapshot, target_policy_id,
                                     section_run_semantics=schema_version == PAYLOAD_SCHEMA_VERSION)
        if cross_strategy and raw_snapshot is not None
        else {}
    )
    output = json.loads(json.dumps(payload, ensure_ascii=False))
    raw_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    contexts: list[tuple[dict[str, Any], Mapping[str, Any] | None]] = []
    detailing_results: list[BeamDetailingResult | None] = []
    parallel_work_inputs: list[BeamDetailingInput | None] = []
    for source in output.get("raw_rows", []):
        raw = dict(source)
        # Derived layouts are regenerated below, never reused as new-policy evidence.
        raw.pop("longitudinal_rows", None)
        raw.pop("row_coordination_audit", None)
        if getattr(engine, "etabs_design_results_only", False):
            _clear_derived_capacity_fields(raw)
        if cross_strategy:
            row_key = str(raw.get("beam_no", ""))
            if row_key not in snapshot_rows:
                raise DetailingError(f"CROSS_STRATEGY_RAW_SNAPSHOT_MISSING_ROW: {row_key}")
            raw_design, snapshot_length, snapshot_offset, snapshot_parent_length = snapshot_rows[row_key]
            length_mm = snapshot_length if snapshot_length is not None else raw.get("length_mm")
            parent_length = snapshot_parent_length if snapshot_parent_length is not None else raw.get("parent_length_mm")
            parent_offset = snapshot_offset if snapshot_parent_length is not None else raw.get("parent_offset_mm", 0.0)
            if length_mm is None or parent_length is None or raw.get("depth_mm") is None:
                raise DetailingError(f"CROSS_STRATEGY_RAW_SNAPSHOT_GEOMETRY_MISSING: {row_key}")
            raw["length_mm"] = float(length_mm)
            raw["parent_length_mm"] = float(parent_length)
            raw["parent_offset_mm"] = float(parent_offset)
            controls = _region_controls(
                raw_design,
                float(length_mm),
                float(raw["depth_mm"]),
                detailing_policy_id=target_policy_id,
                parent_length_mm=float(parent_length),
                segment_start_mm=float(parent_offset),
                parent_end_depths_mm=(tuple(raw["parent_end_depths_mm"])
                                      if raw.get("parent_end_depths_mm") else None),
                projection_mode=(
                    DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE
                    if target_policy_id == DRAWING_V3_POLICY_ID
                    else "APPROVED_REGION_CONTROLS"
                ),
            )
            if controls is not None:
                _store_region_controls(raw, controls)
        else:
            controls = {
                region: {
                    key[len(region) + 1 :]: value
                    for key, value in raw.items()
                    if key.startswith(f"{region}_")
                }
                for region in REGIONS
            }
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
        prior_status = str(source.get("engine_status", ""))
        legacy_face_mode = str(source.get("engine_mode", "")) == "FACE_TORSION_MODE"
        if prior_status == "MIXED_SECTION_REVIEW_REQUIRED":
            common["accepted_warning_codes"] = "; ".join(ACCEPTED_WARNING_CODES)
        retryable = prior_status.startswith((
            "BEAM_LINE_COORDINATION_BLOCKED", "ADJACENT_SPAN_",
        ))
        # These are solver outcomes, not missing/ambiguous source data. A
        # replay must evaluate them against the current engine and catalogue.
        # Keep extraction and mixed-section/profile review gates unchanged.
        solver_status = prior_status.rsplit(":", 1)[-1].strip().split(";", 1)[0]
        retryable = retryable or solver_status in {
            "FEASIBLE_OPTIMIZATION_INCOMPLETE",
            "NO_ALLOWED_SPACING", "ROW_CATALOG_NO_FEASIBLE_LAYOUT",
            "FINAL_ROWS_NO_FEASIBLE_HOOP", "NO_FEASIBLE_TOTAL_LONGITUDINAL_LAYOUT",
        }
        if target_policy_id == DRAWING_V3_POLICY_ID:
            retryable = retryable or solver_status in {
                "ROW_SEGMENT_NO_COMMON_FEASIBLE_LAYOUT",
                "ROW_SEGMENT_NO_EXPLORED_COMPLETE_PATH",
                "FINAL_ROW_NO_EXPLORED_COMPLETE_PATH",
                "FINAL_ROW_ALL_EXPLORED_PATHS_FAILED",
                "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET",
            }
        # Old payloads may have been marked OK while using the retired
        # AsTotal-Al/2 face-torsion path.  Force those rows through the current
        # total-area solver instead of carrying the legacy result forward.
        retryable = retryable or prior_status == "MIXED_SECTION_REVIEW_REQUIRED" or legacy_face_mode
        if prior_status and prior_status != "OK" and not retryable:
            status = prior_status
        else:
            status = _demand_view_status(
                controls, target_policy_id,
                etabs_design_results_only=getattr(engine, "etabs_design_results_only", False),
            ) or _detailing_input_status(common)
        if status:
            raw["engine_status"] = status
            raw.pop("engine_mode", None)
            raw_rows.append(raw)
            review_rows.append(_empty_review_for_policy(engine, common, status, controls, engine.project))
            final_rows.append(_empty_final_for_policy(engine, common, status, controls, engine.project))
            contexts.append((common, controls))
            detailing_results.append(None)
            parallel_work_inputs.append(None)
            continue
        work_input = None
        try:
            input_data = BeamDetailingInput(
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
                        actual_frame_type=str(common.get("frame_type_input_effective", common.get("frame_type", ""))),
                    ),
                    tuple(_engine_demand(region, controls[region]) for region in REGIONS),
                    **_replay_detailing_context(raw, output.get("metadata", {}), detailing_profile),
                )
            if getattr(engine, "etabs_design_results_only", False):
                input_data = engine._etabs_only_input(input_data)
            work_input = input_data
            result = None
            pending_status = "DETAILING_PENDING"
            raw["engine_status"] = pending_status
            review = _empty_review_for_policy(engine, common, pending_status, controls, engine.project)
            final = _empty_final_for_policy(engine, common, pending_status, controls, engine.project)
        except (DetailingError, ValueError) as exc:
            status = str(exc)
            raw["engine_status"] = status
            _record_candidate_search_audit(raw, exc)
            raw.pop("engine_mode", None)
            review = _empty_review_for_policy(engine, common, status, controls, engine.project)
            final = _empty_final_for_policy(engine, common, status, controls, engine.project)
            result = None
        raw_rows.append(raw)
        review_rows.append(review)
        final_rows.append(final)
        contexts.append((common, controls))
        detailing_results.append(result)
        parallel_work_inputs.append(work_input)
    initial_results, detailing_results, coordination_errors, design_errors, effective_workers = (
        _dispatch_beam_line_work(
            parallel_work_inputs,
            contexts,
            engine,
            workers=int(rebar_workers),
            on_line_complete=line_result_callback,
        )
    )
    for index, error in design_errors.items():
        status = error["status"]
        raw_rows[index]["engine_status"] = status
        raw_rows[index].pop("engine_mode", None)
        if error.get("candidate_search_audit") is not None:
            raw_rows[index]["candidate_search_audit_json"] = json.dumps(
                error["candidate_search_audit"], ensure_ascii=False,
            )
        review_rows[index] = _empty_review_for_policy(engine, contexts[index][0], status, contexts[index][1], engine.project)
        final_rows[index] = _empty_final_for_policy(engine, contexts[index][0], status, contexts[index][1], engine.project)
    for index, result in enumerate(initial_results):
        if result is None:
            continue
        raw_rows[index]["engine_status"] = "OK"
        raw_rows[index]["engine_mode"] = result.mode.value
        _record_initial_layout(raw_rows[index], result)
        review_rows[index] = _review_row(contexts[index][0], contexts[index][1], result)
        final_rows[index] = _final_row(contexts[index][0], result)
    output.setdefault("metadata", {})["rebar_parallelism"] = {
        "strategy": "PROCESS_PER_BEAM_LINE" if effective_workers > 1 else "SERIAL_PER_BEAM_LINE",
        "workers_requested": int(rebar_workers),
        "workers_effective": effective_workers,
        "beam_line_jobs": sum(
            1 for indices in _group_context_indices(contexts).values()
            if any(parallel_work_inputs[index] is not None for index in indices)
        ),
    }
    if hasattr(engine, "_last_rebar_performance"):
        output["metadata"]["rebar_performance"] = dict(engine._last_rebar_performance)
    finalize_started = time.perf_counter()
    for index, result in enumerate(detailing_results):
        common, controls = contexts[index]
        if index in coordination_errors:
            status = coordination_errors[index]
            raw_rows[index]["engine_status"] = status
            raw_rows[index].pop("engine_mode", None)
            review_rows[index] = _empty_review_for_policy(engine, common, status, controls, engine.project)
            final_rows[index] = _empty_final_for_policy(engine, common, status, controls, engine.project)
        elif result is not None and controls is not None:
            raw_rows[index]["engine_status"] = ("FEASIBLE_OPTIMIZATION_INCOMPLETE" if result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE" else "OK")
            raw_rows[index]["engine_mode"] = result.mode.value
            raw_rows[index].update(_row_layout_fields(result))
            raw_rows[index].update(_constructability_row_fields(result))
            raw_rows[index].update(_uniform_bottom_fields(result))
            raw_rows[index]["coordination_status"] = result.coordination_status
            raw_rows[index]["continuity_option"] = (
                result.continuity_option.value if result.continuity_option is not None else ""
            )
            raw_rows[index]["coordination_final_signature"] = "|".join(_result_layout_signature(result))
            review_rows[index] = _review_row(common, controls, result)
            final_rows[index] = _final_row(common, result)
    _apply_v3_recommendation_row_audits(raw_rows, engine, review_rows, final_rows)
    output["raw_rows"] = raw_rows
    output["review_rows"] = review_rows
    output["final_rows"] = final_rows
    output.setdefault("metadata", {})["minimum_bars_per_layer"] = 2
    # Replay retains its input numbering contract; old object-part payloads are
    # never relabelled as section runs merely by recalculation.
    output["metadata"]["minimum_main_diameter_mm"] = engine.project.minimum_main_diameter_mm
    output["metadata"]["smf_end_max_spacing_mm"] = engine.project.smf_end_max_spacing_mm
    output["metadata"]["smf_max_longitudinal_ratio"] = engine.project.smf_max_longitudinal_ratio
    output["metadata"]["hanger_diameter_mm"] = engine.project.sb_hanger_diameter_mm
    if revision_changed:
        history = list(output["metadata"].get("detailing_policy_replay_history", ()))
        history.append({
            "source_policy_id": source_policy_id,
            "source_revision_id": source_revision_id,
            "source_ranking_mode": payload_metadata.get("ranking_mode"),
            "target_policy_id": _engine_detailing_policy(engine),
            "target_revision_id": target_revision_id,
            "target_ranking_mode": getattr(engine, "ranking_mode", "legacy"),
            "revision_change_explicitly_authorized": True,
        })
        output["metadata"]["detailing_policy_replay_history"] = history
    _record_detailing_policy_metadata(output["metadata"], engine)
    if cross_strategy:
        output["metadata"]["raw_snapshot_scope"] = "explicit fresh target-policy raw span schedule supplied to redetail"
        if target_policy_id == DRAWING_V3_POLICY_ID:
            output["metadata"]["region_control_projection_mode"] = DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE
    _apply_current_workbook_metadata(output["metadata"])
    if "rebar_performance" in output["metadata"]:
        output["metadata"]["rebar_performance"]["final_row_projection_seconds"] = time.perf_counter() - finalize_started
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
            "schema_version": metadata.get("schema_version", PAYLOAD_SCHEMA_VERSION),
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
    all_groups = _group_context_indices(contexts)
    handled: set[int] = set()
    for indexes in all_groups.values():
        if not any(int(contexts[i][0].get("object_part_index", 0) or 0) for i in indexes):
            continue
        indexes.sort(key=lambda i: (int(contexts[i][0].get("span_no", 0)),
                                    int(contexts[i][0].get("object_part_index", 0) or 0)))
        contiguous: list[list[int]] = []
        for i in indexes:
            span = int(contexts[i][0].get("span_no", 0))
            if not contiguous or span > int(contexts[contiguous[-1][-1]][0].get("span_no", 0)) + 1:
                contiguous.append([])
            contiguous[-1].append(i)
        for chain in contiguous:
            if not any(int(contexts[i][0].get("object_part_index", 0) or 0) for i in chain):
                continue
            handled.update(chain)
            grouped: list[list[int]] = []
            for i in chain:
                if not grouped or contexts[grouped[-1][0]][0].get("span_no") != contexts[i][0].get("span_no"):
                    grouped.append([])
                grouped[-1].append(i)
            valid_groups = []
            for group in grouped:
                common = contexts[group[0]][0]
                status = ""
                if any(output[i] is None for i in group):
                    status = "PARENT_COORDINATION_INCOMPLETE_SECTION"
                if any(output[i] is not None and "SMF_SUPPORT_FACE_UNRESOLVED" in output[i].errors
                       for i in group):
                    status = "PARENT_SUPPORT_FACE_UNRESOLVED"
                if int(common.get("object_part_index", 0) or 0):
                    expected = int(common.get("parent_segment_count", 0) or 0)
                    if expected != len(group) or any(output[i] is not None and
                            (output[i].input_data is None or output[i].input_data.parent_context is None)
                            for i in group):
                        status = "PARENT_COORDINATION_INCOMPLETE_SECTION_COVERAGE"
                valid_groups.append(not status)
                if status:
                    for i in group:
                        if output[i] is not None:
                            output[i] = None
                            errors[i] = status
            runs: list[list[int]] = []
            for group_index, valid in enumerate(valid_groups):
                if valid:
                    if not runs or group_index != runs[-1][-1] + 1:
                        runs.append([])
                    runs[-1].append(group_index)
            for run in runs:
                span_groups = [grouped[n] for n in run]
                local_chain = [i for group in span_groups for i in group]
                touches_gap = run[0] > 0 or run[-1] < len(grouped) - 1
                try:
                    if any(len(group) > 1 for group in span_groups):
                        from src.design.row_solver import coordinate_parent_line
                        relative = {item: n for n, item in enumerate(local_chain)}
                        coordinated = coordinate_parent_line(
                            tuple(output[i] for i in local_chain),
                            tuple(tuple(relative[i] for i in group) for group in span_groups),
                            engine.project.default_continuity_option, engine)
                    elif len(local_chain) > 1:
                        coordinated = coordinate_span_segment(tuple(output[i] for i in local_chain),
                            engine.project.default_continuity_option, engine)
                    else:
                        coordinated = (output[local_chain[0]],)
                    for i, result in zip(local_chain, coordinated):
                        output[i] = (replace(result, coordination_status="NEIGHBOR_COORDINATION_REVIEW_REQUIRED")
                                     if touches_gap else result)
                except (DetailingError, ValueError) as exc:
                    status = f"BEAM_LINE_COORDINATION_BLOCKED: {exc}"
                    for i in local_chain:
                        output[i] = None
                        errors[i] = status
    groups: dict[tuple[str, str, str], list[int]] = {}
    for index, (common, _) in enumerate(contexts):
        if index in handled or output[index] is None or int(common.get("object_part_index", 0) or 0) > 0:
            continue
        key = tuple(str(common.get(key, "")) for key in ("story", "beam_type", "beam_line"))
        groups.setdefault(key, []).append(index)

    for indexes in groups.values():
        indexes.sort(key=lambda index: int(contexts[index][0].get("span_no", 0)))
        segments: list[list[int]] = []
        for index in indexes:
            span = int(contexts[index][0].get("span_no", 0))
            if not segments or span != int(contexts[segments[-1][-1]][0].get("span_no", 0)) + 1:
                segments.append([])
            segments[-1].append(index)
        for segment in segments:
            if len(segment) < 2:
                continue
            try:
                coordinated = coordinate_span_segment(
                    tuple(output[index] for index in segment),
                    engine.project.default_continuity_option,
                    engine,
                )
                for index, result in zip(segment, coordinated):
                    output[index] = result
            except (DetailingError, ValueError) as exc:
                status = f"BEAM_LINE_COORDINATION_BLOCKED: {exc}"
                for index in segment:
                    output[index] = None
                    errors[index] = status
    return output, errors


def _worker_engine_spec(engine: BeamDetailingEngine) -> dict[str, Any]:
    """Serialize only pure detailing configuration; never pass ETABS objects."""

    code_policy = getattr(engine.code, "base_policy", engine.code)
    if (
        type(engine) is not BeamDetailingEngine
        or type(code_policy) is not ACI318_14_DetailingPolicy
        or type(engine.project) is not ProjectDetailingPolicy
        or "_resolve_physical_layout" in engine.__dict__
    ):
        raise DetailingError("PROCESS_REBAR_REQUIRES_BUILTIN_DETAILING_ENGINE")
    policy_id = _engine_detailing_policy(engine)
    provider = getattr(engine, "candidate_lookup_provider", None)
    v3_coverage_schedule_enabled = bool(
        getattr(engine, "v3_coverage_schedule_enabled", False)
    )
    if v3_coverage_schedule_enabled and policy_id != DRAWING_V3_POLICY_ID:
        raise DetailingError("V3_COVERAGE_SCHEDULE_REQUIRES_DRAWING_V3")
    lookup_path = getattr(engine, "candidate_lookup_path", None)
    descriptor = getattr(engine, "candidate_lookup_descriptor", None)
    if provider is not None and not lookup_path and not descriptor and policy_id != DRAWING_V3_POLICY_ID:
        raise DetailingError("PROCESS_CANDIDATE_LOOKUP_REQUIRES_PREPARED_PATH")
    bank = getattr(engine, "layout_dictionary_bank", None)
    bank_path = getattr(bank, "source_path", None) if bank is not None else None
    return {
        "code_policy": code_policy,
        "project_policy": engine.project,
        "detailing_policy_id": policy_id,
        "detailing_policy_revision_id": engine.detailing_policy_revision_id,
        "rules_path": str(engine.rules_path) if engine.rules_path else None,
        "ranking_mode": engine.ranking_mode,
        "candidate_lookup_path": str(lookup_path) if lookup_path else None,
        "candidate_lookup_descriptor": descriptor,
        "candidate_lookup_mode": getattr(engine, "candidate_lookup_mode", None),
        "v3_coverage_schedule_enabled": v3_coverage_schedule_enabled,
        "layout_dictionary_path": str(bank_path) if bank_path else None,
        "layout_dictionary_data": (
            bank.data if bank is not None and bank_path is None else None
        ),
    }


def _initialize_line_worker(engine_spec: Mapping[str, Any]) -> None:
    global _LINE_WORKER_ENGINE
    init_started = time.perf_counter()
    from src.design.detailing import make_detailing_engine

    bank = None
    policy_id = str(engine_spec["detailing_policy_id"])
    bank_data = engine_spec.get("layout_dictionary_data")
    if bank_data is not None:
        from src.design.layout_dictionary import LayoutDictionaryBank

        bank = LayoutDictionaryBank(bank_data)
    provider = None
    if engine_spec.get("candidate_lookup_descriptor"):
        from src.design.lookup_catalog import LookupCatalog
        descriptor = engine_spec["candidate_lookup_descriptor"]
        provider = LookupCatalog.load_many(descriptor["geometry_paths"])
        for path in descriptor["index_paths"]:
            provider.load_indices(path)
    elif engine_spec.get("candidate_lookup_path"):
        from src.design.lookup_catalog import LookupCatalog
        provider = LookupCatalog.load(engine_spec["candidate_lookup_path"])
    elif policy_id == DRAWING_V3_POLICY_ID:
        from src.design.lookup_catalog import LookupCatalog
        provider = LookupCatalog()
    revision_id = (
        None if policy_id == LEGACY_DETAILING_POLICY_ID
        else str(engine_spec["detailing_policy_revision_id"])
    )
    _LINE_WORKER_ENGINE = make_detailing_engine(
        detailing_policy_id=policy_id,
        detailing_policy_revision_id=revision_id,
        code_policy=engine_spec["code_policy"],
        project_policy=engine_spec["project_policy"],
        rules_path=engine_spec.get("rules_path"),
        ranking_mode=str(engine_spec["ranking_mode"]),
        layout_dictionary_bank=bank,
        layout_dictionary_path=engine_spec.get("layout_dictionary_path"),
        candidate_lookup_provider=provider,
    )
    if policy_id == DRAWING_V3_POLICY_ID:
        _LINE_WORKER_ENGINE.candidate_lookup_mode = str(engine_spec.get("candidate_lookup_mode") or "auto")
    _LINE_WORKER_ENGINE.v3_coverage_schedule_enabled = bool(
        engine_spec.get("v3_coverage_schedule_enabled", False)
    )
    _LINE_WORKER_ENGINE._line_worker_initialization_seconds = time.perf_counter() - init_started


def _run_line_work_item(
    job: tuple[tuple[str, str, str], tuple[int, ...], tuple[BeamDetailingInput | None, ...], tuple[Any, ...]],
    engine: BeamDetailingEngine,
) -> dict[str, Any]:
    line_key, indices, work_inputs, contexts = job
    started_wall = time.time()
    started = time.perf_counter()
    cache_snapshot = getattr(engine, "_r7_cache_statistics_snapshot", None)
    before_cache_statistics = cache_snapshot() if callable(cache_snapshot) else {}
    before_phases = dict(getattr(engine, "_indexed_coordination_phase_seconds", {}))
    lookup = getattr(engine, "candidate_lookup_provider", None)
    before_lookup = lookup.metadata().get("query_audit", {}) if lookup is not None else {}
    initial_results: list[BeamDetailingResult | None] = [None] * len(indices)
    design_errors: dict[int, dict[str, Any]] = {}
    v3_recommendation_set = None
    v3_row_audits: dict[int, dict[str, Any]] = {}
    bank = getattr(engine, "layout_dictionary_bank", None)
    before_bank_audit = dict(bank.query_audit) if bank is not None else {}
    design_started = time.perf_counter()
    if engine.detailing_policy_id == DRAWING_V3_POLICY_ID:
        try:
            from src.design.v3 import design_line_v3

            recommendation = design_line_v3(
                work_inputs,
                _v3_parent_groups(work_inputs, contexts),
                engine.project.default_continuity_option,
                engine,
                k=10,
                research_actual_parent_hoop_signatures=True,
                external_support_faces_confirmed=True,
                coverage_schedule=bool(
                    getattr(engine, "v3_coverage_schedule_enabled", False)
                ),
            )
            initial_results = list(recommendation.selected)
            if len(initial_results) != len(indices):
                raise DetailingError("V3_LINE_RESULT_ALIGNMENT_ERROR")
            final_results = list(recommendation.selected)
            recommendation_errors = tuple(recommendation.errors)
            coordination_errors = {}
            for local_index, (input_data, result) in enumerate(zip(work_inputs, final_results)):
                error = recommendation_errors[local_index] if local_index < len(recommendation_errors) else None
                if input_data is not None and result is None:
                    coordination_errors[local_index] = str(error or "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET")
            v3_recommendation_set, v3_row_audits = _serialize_v3_line_recommendation(
                recommendation, line_key, contexts, work_inputs,
            )
            for local_index, error in enumerate(recommendation_errors):
                if error and work_inputs[local_index] is not None and initial_results[local_index] is not None:
                    v3_row_audits[local_index]["v3_recommendation_warning"] = str(error)
        except (DetailingError, ValueError) as exc:
            for local_index, input_data in enumerate(work_inputs):
                if input_data is not None:
                    design_errors[local_index] = {
                        "status": str(exc),
                        "candidate_search_audit": getattr(exc, "candidate_search_audit", None),
                    }
            final_results = [None] * len(indices)
            coordination_errors = {}
        design_seconds = time.perf_counter() - design_started
        coordination_seconds = 0.0
    else:
        coordination_indices = _coordination_pool_indices(work_inputs, contexts)
        for local_index, input_data in enumerate(work_inputs):
            if input_data is None:
                continue
            try:
                if getattr(engine, "independent_skin_rebar", False) and type(engine) is BeamDetailingEngine and "design" not in engine.__dict__:
                    initial_results[local_index] = engine.design(
                        input_data, prepare_coordination_pool=local_index in coordination_indices,
                    )
                else:
                    initial_results[local_index] = engine.design(input_data)
            except (DetailingError, ValueError) as exc:
                design_errors[local_index] = {
                    "status": str(exc),
                    "candidate_search_audit": getattr(exc, "candidate_search_audit", None),
                }

        design_seconds = time.perf_counter() - design_started
        coordination_started = time.perf_counter()
        final_results, coordination_errors = _coordinate_results_by_beam_line(
            initial_results,
            list(contexts),
            engine,
        )
        coordination_seconds = time.perf_counter() - coordination_started
    bank_audit_delta = {}
    if bank is not None:
        bank_audit_delta = {
            key: int(value) - int(before_bank_audit.get(key, 0))
            for key, value in bank.query_audit.items()
            if isinstance(value, (int, float))
        }
    elapsed_seconds = time.perf_counter() - started
    ended_wall = time.time()
    after_cache_statistics = cache_snapshot() if callable(cache_snapshot) else {}
    candidate_pool_cache = getattr(engine, "_style_candidate_pool_cache", None)
    if isinstance(candidate_pool_cache, dict):
        candidate_pool_cache.clear()
    return {
        "line_key": line_key,
        "indices": indices,
        "contexts": contexts,
        "initial_results": tuple(initial_results),
        "results": tuple(final_results),
        "design_errors": design_errors,
        "coordination_errors": coordination_errors,
        "v3_recommendation_set": v3_recommendation_set,
        "v3_row_audits": v3_row_audits,
        "worker_pid": os.getpid(),
        "started_at_epoch": started_wall,
        "ended_at_epoch": ended_wall,
        "elapsed_seconds": elapsed_seconds,
        "worker_initialization_seconds": getattr(engine, "_line_worker_initialization_seconds", 0.0),
        "design_seconds": design_seconds,
        "coordination_seconds": coordination_seconds,
        "coordination_phase_seconds": {key: value - before_phases.get(key, 0.0)
            for key, value in getattr(engine, "_indexed_coordination_phase_seconds", {}).items()},
        "candidate_lookup_statistics_delta": {key: value - before_lookup.get(key, 0)
            for key, value in (lookup.metadata().get("query_audit", {}) if lookup is not None else {}).items()
            if isinstance(value, (int, float))},
        "r7_cache_statistics_delta": {
            key: int(value) - int(before_cache_statistics.get(key, 0))
            for key, value in after_cache_statistics.items()
        },
        "layout_dictionary_query_audit_delta": bank_audit_delta,
    }


def _record_rebar_dispatch_diagnostics(engine, outcomes, workers, effective_workers, started):
    completed = [outcome for outcome in outcomes if outcome is not None]
    cache_statistics: dict[str, int] = {}
    floors: dict[str, dict[str, Any]] = {}
    for outcome in completed:
        for key, value in outcome.get("r7_cache_statistics_delta", {}).items():
            cache_statistics[key] = cache_statistics.get(key, 0) + int(value)
        story = str(outcome["line_key"][0])
        tower, separator, level = story.partition("-")
        floor = level if separator and tower.startswith("T") and tower[1:].isdigit() else story
        entry = floors.setdefault(floor, {
            "line_jobs": 0, "job_rows": 0, "line_work_seconds_sum": 0.0,
            "first_line_started_at_epoch": outcome["started_at_epoch"],
            "last_line_ended_at_epoch": outcome["ended_at_epoch"],
        })
        entry["line_jobs"] += 1
        entry["job_rows"] += len(outcome["indices"])
        entry["line_work_seconds_sum"] += outcome["elapsed_seconds"]
        entry["first_line_started_at_epoch"] = min(entry["first_line_started_at_epoch"], outcome["started_at_epoch"])
        entry["last_line_ended_at_epoch"] = max(entry["last_line_ended_at_epoch"], outcome["ended_at_epoch"])
    for entry in floors.values():
        entry["line_work_wall_span_seconds"] = entry["last_line_ended_at_epoch"] - entry["first_line_started_at_epoch"]
    slowest_lines = [
        {"line_key": list(outcome["line_key"]), "job_rows": len(outcome["indices"]),
         "elapsed_seconds": outcome["elapsed_seconds"], "worker_pid": outcome["worker_pid"]}
        for outcome in sorted(completed, key=lambda item: item["elapsed_seconds"], reverse=True)[:10]
    ]
    engine._last_rebar_performance = {
        "dispatch_seconds": time.perf_counter() - started,
        "line_jobs": len(completed), "floors": floors, "slowest_lines": slowest_lines,
        "cache_statistics": cache_statistics,
        "cache_statistics_basis": "sum of completed line deltas; engine initialization excluded",
        "timing_basis": "dispatch wall includes worker spawn; floor line-work spans exclude initialization; sums are work accounting only",
    }
    engine._last_rebar_performance["design_worker_seconds_sum"] = sum(o.get("design_seconds", 0.0) for o in completed)
    engine._last_rebar_performance["worker_initialization_seconds_by_pid"] = {
        str(o["worker_pid"]): o.get("worker_initialization_seconds", 0.0) for o in completed}
    engine._last_rebar_performance["coordination_worker_seconds_sum"] = sum(o.get("coordination_seconds", 0.0) for o in completed)
    phase_seconds = {}
    for outcome in completed:
        for key, value in outcome.get("coordination_phase_seconds", {}).items():
            phase_seconds[key] = phase_seconds.get(key, 0.0) + value
    engine._last_rebar_performance["coordination_phase_seconds_sum"] = phase_seconds
    lookup_statistics = {}
    for outcome in completed:
        for key, value in outcome.get("candidate_lookup_statistics_delta", {}).items():
            lookup_statistics[key] = lookup_statistics.get(key, 0) + value
    if lookup_statistics:
        engine._last_rebar_performance["candidate_lookup_statistics"] = lookup_statistics
    if hasattr(engine, "_candidate_lookup_preparation"):
        engine._last_rebar_performance["candidate_lookup_preparation"] = engine._candidate_lookup_preparation
    _LOGGER.info(
        "Rebar dispatch workers_requested=%s workers_effective=%s elapsed_seconds=%.3f slowest_lines=%s cache_statistics=%s",
        workers, effective_workers, engine._last_rebar_performance["dispatch_seconds"],
        json.dumps(slowest_lines, ensure_ascii=False), json.dumps(cache_statistics, ensure_ascii=False),
    )


def _coordination_pool_indices(work_inputs, contexts) -> set[int]:
    """Only contiguous eligible original-span runs need boundary alternatives."""

    eligible = sorted(
        (index for index, item in enumerate(work_inputs)
         if item is not None and int(contexts[index][0].get("object_part_index", 0) or 0) == 0),
        key=lambda index: int(contexts[index][0].get("span_no", 0)),
    )
    selected: set[int] = set()
    run: list[int] = []
    for index in eligible:
        span = int(contexts[index][0].get("span_no", 0))
        if run and span != int(contexts[run[-1]][0].get("span_no", 0)) + 1:
            if len(run) >= 2:
                selected.update(run)
            run = []
        run.append(index)
    if len(run) >= 2:
        selected.update(run)
    return selected


def _run_line_worker_job(job: tuple[Any, ...]) -> dict[str, Any]:
    if _LINE_WORKER_ENGINE is None:
        raise RuntimeError("REBAR_LINE_WORKER_ENGINE_NOT_INITIALIZED")
    return _run_line_work_item(job, _LINE_WORKER_ENGINE)


def _group_context_indices(
    contexts: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
) -> dict[tuple[str, str, str], list[int]]:
    grouped: dict[tuple[str, str, str], list[int]] = {}
    for index, (common, _controls) in enumerate(contexts):
        key = tuple(str(common.get(field, "")) for field in ("story", "beam_type", "beam_line"))
        grouped.setdefault(key, []).append(index)
    return grouped


def _v3_parent_groups(work_inputs, contexts) -> tuple[tuple[int, ...], ...]:
    """Return only complete source-order section groups for one parent span."""

    by_parent: dict[str, dict[str, Any]] = {}
    for index, (common, _controls) in enumerate(contexts):
        part_index = int(common.get("object_part_index", 0) or 0)
        part_count = int(common.get("parent_segment_count", 0) or 0)
        if part_index <= 0 and part_count <= 1:
            continue
        parent_id = str(common.get("parent_span_id", "")).strip()
        if not parent_id:
            parent_id = f"{common.get('beam_line', '')}:{common.get('span_no', '')}"
        group = by_parent.setdefault(parent_id, {"indices": [], "expected_count": part_count})
        group["indices"].append(index)
        group["expected_count"] = max(int(group["expected_count"]), part_count)
    complete_groups = []
    for group in by_parent.values():
        indices = group["indices"]
        expected_count = int(group["expected_count"])
        actual_parts = {
            int(contexts[index][0].get("object_part_index", 0) or 0)
            for index in indices
        }
        if expected_count > 1 and len(indices) == expected_count and actual_parts == set(range(1, expected_count + 1)):
            complete_groups.append(tuple(indices))
    return tuple(complete_groups)


def _serialize_v3_line_recommendation(recommendation, line_key, contexts, work_inputs):
    """Keep up to ten complete, aligned beam-line proposals in the JSON audit."""

    audit = dict(recommendation.audit or {})
    alternatives = tuple(recommendation.alternatives or ())[:10]
    selected_rank = 1 if recommendation.selected and alternatives else None
    solver_selected_rank = audit.get("selected_rank")
    no_design_indices = {
        index for index, (common, _controls) in enumerate(contexts)
        if (
            "no design" in str(common.get("design_status_message", "")).casefold()
            or "excluded_no_design" in str(common.get("design_status_message", "")).casefold()
        )
    }
    known_no_design_gap_indices = {
        index for index in no_design_indices
        if index < len(work_inputs) and work_inputs[index] is None
    }
    other_input_gap_indices = {
        index for index, item in enumerate(work_inputs)
        if item is None and index not in known_no_design_gap_indices
    }
    recommendation_errors = list(recommendation.errors or ())
    for index in known_no_design_gap_indices:
        if index < len(recommendation_errors):
            recommendation_errors[index] = "EXCLUDED_NO_DESIGN"
    has_input_gap = any(item is None for item in work_inputs)
    unsolved_span_indices = tuple(
        int(index) for index in audit.get("unsolved_span_indices", ())
    )
    has_unsolved_span = bool(unsolved_span_indices)
    parent_coverage: dict[str, dict[str, Any]] = {}
    for index, (common, _controls) in enumerate(contexts):
        expected_count = int(common.get("parent_segment_count", 0) or 0)
        part_index = int(common.get("object_part_index", 0) or 0)
        if part_index <= 0 and expected_count <= 1:
            continue
        parent_id = str(common.get("parent_span_id", "")).strip()
        if not parent_id:
            parent_id = f"{common.get('beam_line', '')}:{common.get('span_no', '')}"
        coverage = parent_coverage.setdefault(parent_id, {"expected_count": expected_count, "indices": []})
        coverage["expected_count"] = max(int(coverage["expected_count"]), expected_count)
        coverage["indices"].append(index)
    incomplete_parent_ids = set()
    external_no_design_parent_ids = set()
    for parent_id, coverage in parent_coverage.items():
        expected_count = int(coverage["expected_count"])
        indices = coverage["indices"]
        actual_parts = {
            int(contexts[index][0].get("object_part_index", 0) or 0)
            for index in indices
        }
        incomplete = (
            len(indices) != expected_count
            or actual_parts != set(range(1, expected_count + 1))
            or any(work_inputs[index] is None for index in indices)
            or any(
                index < len(recommendation_errors)
                and str(recommendation_errors[index] or "").startswith("PARENT_COORDINATION_INCOMPLETE")
                for index in indices
            )
        )
        if incomplete:
            incomplete_parent_ids.add(parent_id)
            missing = [index for index in indices if work_inputs[index] is None]
            if (
                missing
                and all(index in known_no_design_gap_indices for index in missing)
                and len(indices) == expected_count
                and actual_parts == set(range(1, expected_count + 1))
            ):
                external_no_design_parent_ids.add(parent_id)
    external_no_design_parent_indices = {
        index
        for parent_id, coverage in parent_coverage.items()
        if parent_id in external_no_design_parent_ids
        for index in coverage["indices"]
    }
    for index in external_no_design_parent_indices - known_no_design_gap_indices:
        if index < len(recommendation_errors) and str(recommendation_errors[index] or "").startswith(
            "PARENT_COORDINATION_INCOMPLETE"
        ):
            recommendation_errors[index] = "V3_PARENT_COORDINATION_EXTERNAL_SCOPE_NO_DESIGN"
    recommendation_errors = tuple(recommendation_errors)
    recommendation_set_id = "|".join(str(value) for value in line_key)
    serialized_alternatives = []
    for rank, path in enumerate(alternatives, start=1):
        spans = []
        for index, result in enumerate(path):
            common = contexts[index][0]
            span_record: dict[str, Any] = {
                "input_index": index,
                "beam_no": str(common.get("beam_no", "")),
                "parent_span_id": str(common.get("parent_span_id", "")),
                "span_no": common.get("span_no"),
                "section": str(common.get("section", "")),
                "beam_type": str(common.get("beam_type", "")),
                "parent_section_group_complete": (
                    str(common.get("parent_span_id", "")).strip()
                    or f"{common.get('beam_line', '')}:{common.get('span_no', '')}"
                ) not in incomplete_parent_ids,
                "is_selected": rank == selected_rank,
            }
            if result is None:
                errors = recommendation_errors
                span_record["status"] = (
                    str(errors[index]) if index < len(errors) and errors[index]
                    else "INPUT_BLOCKED" if work_inputs[index] is None
                    else "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET"
                )
                span_record["input_available"] = work_inputs[index] is not None
                if index in known_no_design_gap_indices:
                    span_record["input_exclusion_status"] = "EXCLUDED_NO_DESIGN"
                    span_record["geometry_and_numbering_retained"] = True
            else:
                layout = _row_layout_fields(result)
                side = result.face_rebar.bars_each_side
                errors = recommendation_errors
                span_record.update({
                    "status": (
                        "RECOMMENDED_EXTERNAL_SCOPE_NO_DESIGN"
                        if index in external_no_design_parent_indices
                        and index < len(errors)
                        and errors[index] == "V3_PARENT_COORDINATION_EXTERNAL_SCOPE_NO_DESIGN"
                        else
                        "RECOMMENDED_WITH_REVIEW"
                        if index < len(errors) and errors[index]
                        else "RECOMMENDED"
                    ),
                    "review_reason": str(errors[index]) if index < len(errors) and errors[index] else None,
                    "coordination_status": (
                        "V3_PARENT_COORDINATION_EXTERNAL_SCOPE_NO_DESIGN"
                        if index in external_no_design_parent_indices
                        and result.coordination_status == "V3_PARENT_COORDINATION_UNKNOWN_INPUT_BLOCKED"
                        else result.coordination_status
                    ),
                    "signature": list(_result_layout_signature(result)),
                    "longitudinal_rows": layout.get("longitudinal_rows", {}),
                    "region_stirrups": layout.get("region_stirrups", {}),
                    "row_coordination_audit": layout.get("row_coordination_audit", []),
                    "side_reinforcement": (
                        {
                            "diameter_mm": side.diameter_mm,
                            "count_each_side": side.count,
                            "construction_only": True,
                        }
                        if side is not None else None
                    ),
                })
            spans.append(span_record)
        serialized_alternatives.append({"rank": rank, "spans": spans})

    audit.setdefault("candidate_limit", 10)
    audit.setdefault("candidate_count", len(alternatives))
    audit.setdefault("search_scope", "RECOMMENDATION_SET_ONLY")
    audit.setdefault("hard_infeasible", False)
    audit.setdefault("global_optimum_proven", False)
    audit.setdefault("global_optimality_claim", "NOT_PROVEN")
    has_unresolved_span = any(result is None for result in (recommendation.selected or ()))
    has_any_recommendation = any(result is not None for result in (recommendation.selected or ()))
    has_incomplete_parent = bool(incomplete_parent_ids)
    external_scope_only = (
        bool(known_no_design_gap_indices)
        and not other_input_gap_indices
        and not has_unsolved_span
        and not (incomplete_parent_ids - external_no_design_parent_ids)
        and not audit.get("partial_components")
    )
    audit["no_design_input_indices"] = sorted(known_no_design_gap_indices)
    audit["continuity_unknown_due_to_missing_input"] = bool(other_input_gap_indices)
    audit["continuity_external_scope_due_to_no_design"] = bool(known_no_design_gap_indices)
    audit["parent_spans_with_no_design_members"] = sorted(external_no_design_parent_ids)
    complete_line_recommendation = (
        not has_input_gap and not has_unresolved_span and not has_incomplete_parent
        and bool(recommendation.selected)
    )
    solver_status = str(recommendation.status)
    if complete_line_recommendation:
        output_status = solver_status
    elif has_any_recommendation and external_scope_only:
        output_status = "PARTIAL_RECOMMENDATION_OUT_OF_DESIGN_SCOPE"
    elif has_any_recommendation and has_input_gap:
        output_status = "PARTIAL_RECOMMENDATION_UNKNOWN_ACROSS_INPUT_GAP"
    elif has_any_recommendation and has_unsolved_span:
        output_status = "PARTIAL_RECOMMENDATION_UNKNOWN_ACROSS_UNSOLVED_SPAN"
    elif has_any_recommendation and has_incomplete_parent:
        output_status = "PARTIAL_RECOMMENDATION_UNKNOWN_PARENT_SECTION"
    elif has_input_gap or has_incomplete_parent:
        output_status = "NO_COMPLETE_LINE_RECOMMENDATION_UNKNOWN_CONTINUITY"
    elif has_unsolved_span:
        output_status = "NO_COMPLETE_LINE_RECOMMENDATION_UNSOLVED_SPAN"
    else:
        output_status = "NO_COMPLETE_LINE_RECOMMENDATION"
    record = {
        "recommendation_set_id": recommendation_set_id,
        "beam_line": {"story": line_key[0], "beam_type": line_key[1], "beam_line": line_key[2]},
        "status": output_status,
        "solver_status": solver_status,
        "line_recommendation_complete": complete_line_recommendation,
        "aligned_paths_are_complete": complete_line_recommendation,
        "path_scope": (
            "FULL_LINE" if complete_line_recommendation
            else "ALIGNED_WITH_EXTERNAL_DESIGN_SCOPE_BREAKS" if external_scope_only
            else "ALIGNED_WITH_UNRESOLVED_GAPS"
        ),
        "policy_id": DRAWING_V3_POLICY_ID,
        "policy_revision_id": audit.get("policy_revision_id", DRAWING_V3_REVISION_ID),
        "solver_revision_id": audit.get("solver_revision_id", DRAWING_V3_SOLVER_REVISION_ID),
        "candidate_limit": min(10, int(audit.get("candidate_limit", 10) or 10)),
        "candidate_count": int(audit.get("candidate_count", len(alternatives)) or 0),
        "selected_rank": selected_rank,
        "solver_selected_rank": solver_selected_rank,
        "recommendation_set_rank": audit.get("recommendation_set_rank", selected_rank),
        "selection_scope": "RANKED_WITHIN_RECOMMENDATION_SET",
        "global_optimum_proven": False,
        "full_specification_compliance_claimed": False,
        "line_continuity_across_input_gap": (
            "EXTERNAL_SCOPE" if external_scope_only
            else "UNKNOWN" if has_input_gap or has_incomplete_parent
            else "EVALUATED_BY_V3_SOLVER"
        ),
        "no_design_input_indices": sorted(known_no_design_gap_indices),
        "unresolved_span_indices": list(unsolved_span_indices),
        "line_continuity_across_unsolved_span": "UNKNOWN" if has_unsolved_span else None,
        "parent_spans_with_missing_sections": sorted(
            value for value in incomplete_parent_ids - external_no_design_parent_ids if value
        ),
        "parent_continuity_for_missing_section": (
            "UNKNOWN" if incomplete_parent_ids - external_no_design_parent_ids else None
        ),
        "parent_spans_with_no_design_members": sorted(external_no_design_parent_ids),
        "parent_continuity_for_no_design_member": (
            "EXTERNAL_SCOPE" if external_no_design_parent_ids else None
        ),
        "audit": audit,
        "alternatives": serialized_alternatives,
    }
    row_audits = {
        index: {
            "v3_recommendation_set_id": recommendation_set_id,
            "v3_recommendation_status": record["status"],
            "v3_recommendation_rank": selected_rank,
            "v3_candidate_count": record["candidate_count"],
            "v3_candidate_limit": record["candidate_limit"],
            "v3_global_optimum_proven": False,
            "v3_recommendation_audit_json": json.dumps(audit, ensure_ascii=False),
            "v3_parent_continuity_unknown": (
                str(contexts[index][0].get("parent_span_id", "")).strip()
                or f"{contexts[index][0].get('beam_line', '')}:{contexts[index][0].get('span_no', '')}"
            ) in (incomplete_parent_ids - external_no_design_parent_ids),
            "v3_parent_continuity_external_scope": (
                str(contexts[index][0].get("parent_span_id", "")).strip()
                or f"{contexts[index][0].get('beam_line', '')}:{contexts[index][0].get('span_no', '')}"
            ) in external_no_design_parent_ids,
        }
        for index in range(len(contexts))
    }
    return record, row_audits


def _prepare_candidate_lookup(work_inputs, engine):
    """Prepare candidate contexts once in the main process before dispatch."""
    from src.design.lookup_catalog import LookupCatalog
    from src.design.row_solver import _style_hoop_states, _style_hoop, _parent_hoop_potential

    started = time.perf_counter()
    provider = engine.candidate_lookup_provider
    if provider is None:
        raise DetailingError("DICT_MISS: R9 requires a candidate lookup provider")
    inputs = [item for item in work_inputs if item is not None]
    if not inputs:
        return
    paths = []
    index_paths = []
    asset_dir = Path(os.environ.get("BEAM_CANDIDATE_LOOKUP_DIR", str(Path(__file__).resolve().parent / "design" / "lookup_assets")))
    if not provider.contexts and not provider.template_prefixes():
        widths = sorted({item.section.width_mm for item in inputs})
        paths = [asset_dir / f"width_{int(width)}.json.gz" for width in widths if float(width).is_integer() and (asset_dir / f"width_{int(width)}.json.gz").is_file()]
        provider = LookupCatalog.load_many(paths)
        engine.candidate_lookup_provider = provider
    initial_geometry = set(provider._geometry_templates) if paths else set()
    load_seconds = time.perf_counter() - started
    compile_start = time.perf_counter()
    prefixes = provider.template_prefixes()
    requests = {}
    def add_request(section, hoops, **kwargs):
        key = provider._key(section, hoops, engine, first_diameter=kwargs.get("first_diameter"),
                            max_layers=kwargs["max_layers"], face=kwargs["face"],
                            preserve_support_alternatives=kwargs["preserve_support_alternatives"])
        requests.setdefault(key, (section, hoops, kwargs))

    for item in inputs:
        section = item.section
        prefix = provider._prefix(section, engine)
        if prefix in prefixes:
            continue
        from src.design.detailing import StirrupDesign
        for diameter in engine.project.ordered_stirrup_diameters():
            hoop = StirrupDesign(diameter, 100, 2, 0, 0, 100, "LOOKUP_PREPARATION")
            provider.prepare_geometry(replace(section, detailing_profile=BeamDetailingProfile.NON_SWAY), hoop, engine,
                                      max_layers=engine.project.max_main_bar_layers)
        prefixes.add(prefix)
    geometry_prepare_seconds = time.perf_counter() - compile_start
    for item in inputs:
        if item.section.detailing_profile is BeamDetailingProfile.UNRESOLVED:
            continue
        applicable = engine.offline_style_library.applicable_fields(item, engine)
        states = list(_style_hoop_states(item, engine, applicable))
        if item.parent_context is not None:
            section = item.section
            diameters = engine.project.ordered_longitudinal_diameters_for(section)
            for hoop_diameter in engine.project.ordered_stirrup_diameters():
                if hoop_diameter < engine.project.minimum_stirrup_diameter_for(section):
                    continue
                capacity = max(engine._capacity_per_layer(section, hoop_diameter, db) for db in diameters)
                minimum = max(2, *(engine.code.minimum_legs(section, demand.name) for demand in item.regions))
                minimum += minimum % 2
                for legs in range(minimum, capacity + 1, 2):
                    chosen = []
                    for demand in item.regions:
                        hoop = next((h for spacing in sorted(engine.project.allowed_spacings_for(section, demand.name), reverse=True)
                                     if (h := _style_hoop(section, demand, {"diameter_mm": hoop_diameter,
                                         "spacing_mm": spacing, "legs": legs}, engine)) is not None
                                     and _parent_hoop_potential(item, engine, demand, h)), None)
                        if hoop is None:
                            break
                        chosen.append(hoop)
                    if len(chosen) == 3:
                        states.append(tuple(chosen))
        for hoops in states:
            add_request(item.section, hoops, max_layers=engine.project.max_main_bar_layers,
                        face="bottom", preserve_support_alternatives=False)
            for hoop in hoops:
                add_request(item.section, (hoop,), max_layers=engine.project.max_main_bar_layers,
                            face="top", preserve_support_alternatives=True)
                for diameter in engine.project.ordered_longitudinal_diameters_for(item.section):
                    add_request(item.section, (hoop,), first_diameter=diameter,
                                max_layers=engine.project.max_main_bar_layers,
                                face="top", preserve_support_alternatives=True)
    index_dir = os.environ.get("BEAM_CANDIDATE_LOOKUP_INDEX_DIR")
    index_load_started = time.perf_counter()
    for root in dict.fromkeys([asset_dir, *([Path(index_dir)] if index_dir else [])]):
        candidates = [path for path in root.glob("context_indices*.jsonl.*")
                      if path.name.endswith((".jsonl.xz", ".jsonl.gz"))]
        source_indices_by_name = {}
        for path in candidates:
            logical_name = path.name.rsplit(".", 1)[0]
            existing = source_indices_by_name.get(logical_name)
            if existing is None or path.suffix.lower() == ".xz":
                source_indices_by_name[logical_name] = path
        source_indices = list(source_indices_by_name.values())
        for width in {item.section.width_mm for item in inputs}:
            if not float(width).is_integer():
                continue
            name = f"width_{int(width)}.jsonl"
            candidates = [root / "standard_indices" / f"{name}.xz",
                          root / "standard_indices" / f"{name}.gz"]
            selected = next((path for path in candidates if path.is_file()), None)
            if selected is not None:
                source_indices.append(selected)
        for index_path in sorted(source_indices):
            provider.load_indices(index_path, only_keys=set(requests))
    index_load_seconds = time.perf_counter() - index_load_started
    source_keys = set(provider.contexts) & set(requests)
    source_hits = len(source_keys)
    compiled_keys = sorted(set(requests) - source_keys)
    actual_compile_started = time.perf_counter()
    for section, hoops, kwargs in requests.values():
        provider.prepare(section, hoops, engine, **kwargs)
    actual_compile_seconds = time.perf_counter() - actual_compile_started
    compile_seconds = time.perf_counter() - compile_start
    save_start = time.perf_counter()
    output_dir = getattr(engine, "candidate_lookup_output_dir", None)
    if output_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="beam-r9-lookup-")
        engine._candidate_lookup_temporary = temporary
        target_dir = Path(temporary.name)
    else:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        target_dir = Path(tempfile.mkdtemp(prefix="candidate_lookup_", dir=output_dir))
    extra = LookupCatalog()
    extra.contexts = {key: provider.contexts[key] for key in requests}
    if extra.contexts:
        index_path = target_dir / "selected_indices.json.gz"
        extra.save_indices(index_path, compresslevel=1)
        index_paths.append(index_path)
    extra.contexts = {}
    extra._geometry_templates = {key: value for key, value in provider._geometry_templates.items() if key not in initial_geometry}
    if extra._geometry_templates:
        geometry_path = target_dir / "extra_geometry.json.gz"
        extra.save(geometry_path)
        paths.append(geometry_path)
    engine.candidate_lookup_descriptor = {"geometry_paths": [str(path) for path in paths],
                                          "index_paths": [str(path) for path in index_paths]}
    engine._candidate_lookup_preparation = {"asset_load_seconds": load_seconds,
        "request_index_and_compile_seconds": compile_seconds, "save_seconds": time.perf_counter() - save_start,
        "index_load_seconds": index_load_seconds, "actual_compile_seconds": actual_compile_seconds,
        "geometry_prepare_seconds": geometry_prepare_seconds,
        "source_hits": source_hits, "compiled_contexts": len(requests) - source_hits,
        "compiled_keys": compiled_keys,
        "contexts": len(provider.contexts), "artifact_directory": str(target_dir) if output_dir is not None else None}


def _dispatch_beam_line_work(
    work_inputs: list[BeamDetailingInput | None],
    contexts: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    engine: BeamDetailingEngine,
    *,
    workers: int,
    on_line_complete: Any | None = None,
) -> tuple[
    list[BeamDetailingResult | None],
    list[BeamDetailingResult | None],
    dict[int, str],
    dict[int, dict[str, Any]],
    int,
]:
    """Design and coordinate complete BeamLines, with bounded ordered merge."""

    if isinstance(workers, bool) or int(workers) < 1:
        raise ValueError("rebar_workers must be a positive integer")
    if len(work_inputs) != len(contexts):
        raise ValueError("REBAR_LINE_WORK_INPUT_ALIGNMENT_ERROR")

    dispatch_started = time.perf_counter()
    engine._last_v3_recommendation_sets = []
    engine._last_v3_recommendation_row_audits = {}
    if getattr(engine, "coordination_solver_revision_id", None) == "coordination_20261031_indexed_candidates_v1":
        _prepare_candidate_lookup(work_inputs, engine)
    grouped = _group_context_indices(contexts)
    jobs = [
        (
            key,
            tuple(indices),
            tuple(work_inputs[index] for index in indices),
            tuple(contexts[index] for index in indices),
        )
        for key, indices in grouped.items()
        if any(work_inputs[index] is not None for index in indices)
    ]
    initial_results: list[BeamDetailingResult | None] = [None] * len(contexts)
    final_results: list[BeamDetailingResult | None] = [None] * len(contexts)
    coordination_errors: dict[int, str] = {}
    design_errors: dict[int, dict[str, Any]] = {}
    if not jobs:
        _record_rebar_dispatch_diagnostics(engine, [], workers, 1, dispatch_started)
        return initial_results, final_results, coordination_errors, design_errors, 1

    cpu_limit = max(1, (os.cpu_count() or 1) - 1)
    effective_workers = min(int(workers), cpu_limit, len(jobs))
    if sys.platform == "win32":
        # CPython ProcessPoolExecutor on Windows supports at most 61 workers.
        effective_workers = min(effective_workers, 61)
    if effective_workers > 1 and getattr(engine, "coordination_solver_revision_id", None) in {
        "coordination_20261031_indexed_candidates_v1", DRAWING_V3_SOLVER_REVISION_ID,
    }:
        # Independent lines may start in any order; their rows retain their
        # original positions and their internal order during the final merge.
        jobs.sort(key=lambda job: -sum(item is not None for item in job[2]))
    outcomes: list[dict[str, Any] | None] = [None] * len(jobs)
    if effective_workers <= 1:
        for job_index, job in enumerate(jobs):
            outcomes[job_index] = _run_line_work_item(job, engine)
            if on_line_complete is not None:
                on_line_complete(outcomes[job_index])
    else:
        executor = ProcessPoolExecutor(
            max_workers=effective_workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize_line_worker,
            initargs=(_worker_engine_spec(engine),),
        )
        pending: dict[Any, int] = {}
        next_job = 0
        max_inflight = max(effective_workers, effective_workers * 2)
        try:
            while next_job < len(jobs) and len(pending) < max_inflight:
                future = executor.submit(_run_line_worker_job, jobs[next_job])
                pending[future] = next_job
                next_job += 1
            while pending:
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    job_index = pending.pop(future)
                    outcome = future.result()
                    outcomes[job_index] = outcome
                    if on_line_complete is not None:
                        on_line_complete(outcome)
                    if next_job < len(jobs):
                        following = executor.submit(_run_line_worker_job, jobs[next_job])
                        pending[following] = next_job
                        next_job += 1
        except BaseException:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    for outcome in outcomes:
        if outcome is None:
            raise RuntimeError("REBAR_LINE_WORKER_RESULT_MISSING")
        indices = outcome["indices"]
        for local_index, global_index in enumerate(indices):
            initial_results[global_index] = outcome["initial_results"][local_index]
            final_results[global_index] = outcome["results"][local_index]
        for local_index, error in outcome["coordination_errors"].items():
            coordination_errors[indices[int(local_index)]] = str(error)
        for local_index, error in outcome["design_errors"].items():
            design_errors[indices[int(local_index)]] = error
        if effective_workers > 1 and engine.layout_dictionary_bank is not None:
            for key, value in outcome["layout_dictionary_query_audit_delta"].items():
                engine.layout_dictionary_bank.query_audit[key] = (
                    engine.layout_dictionary_bank.query_audit.get(key, 0) + int(value)
                )
        recommendation_set = outcome.get("v3_recommendation_set")
        if recommendation_set is not None:
            engine._last_v3_recommendation_sets.append(recommendation_set)
            for local_index, row_audit in outcome.get("v3_row_audits", {}).items():
                engine._last_v3_recommendation_row_audits[indices[int(local_index)]] = row_audit
    _record_rebar_dispatch_diagnostics(engine, outcomes, workers, effective_workers, dispatch_started)
    return initial_results, final_results, coordination_errors, design_errors, effective_workers


def _mark_multi_section_continuity_review(
    raw: dict[str, Any],
    review: dict[str, Any],
    final: dict[str, Any],
) -> None:
    """Keep segment candidates, but do not claim parent-span coordination."""

    status = "MULTI_SECTION_CONTINUITY_REVIEW_REQUIRED"
    strategy = "MULTI_SECTION_SEGMENT_CANDIDATE_REQUIRES_PARENT_COORDINATION"
    note = (
        "本行是源 Object 子段候选；父结构跨底筋通长统一及上部第一排直径协调尚未完成，"
        "不得按各子段独立结果作为正式配筋交付。"
    )
    for row in (raw, review, final):
        row["engine_status"] = status
        row["parent_continuity_status"] = status
        row["parent_continuity_review_required"] = True
        row["bottom_reinforcement_strategy"] = strategy
        row["bottom_uniform_regions_match"] = None
        row["review_note"] = note
    raw["engine_warnings"] = "; ".join(
        value for value in (str(raw.get("engine_warnings", "")).strip(), status) if value
    )
    for row in (review, final):
        row["engine_warnings"] = "; ".join(
            value for value in (str(row.get("engine_warnings", "")).strip(), note) if value
        )


def _result_layout_signature(result: BeamDetailingResult | None) -> tuple[str, ...]:
    if result is None:
        return ()
    # Coordination/audit signatures use physical row order.  Display text is
    # face-aware and reverses only bottom rows at the projection boundary.
    return tuple(
        value
        for region in result.regions
        for value in (region.top.physical_notation, region.bottom.physical_notation)
    )


def _uniform_bottom_fields(result: BeamDetailingResult) -> dict[str, Any]:
    """Flatten the actual full-span bottom layout for audit and projection."""

    bottoms = tuple(region.bottom for region in result.regions)
    notations = tuple(design.notation_for_face("bottom") for design in bottoms)
    first = bottoms[0]
    diameters = tuple(
        dict.fromkeys(
            row.diameter_mm
            for design in bottoms
            for row in design.rows
            if row.role != "hanger"
        )
    )
    return {
        "bottom_reinforcement_strategy": BOTTOM_REINFORCEMENT_STRATEGY,
        "bottom_uniform_regions_match": len(set(notations)) == 1,
        "bottom_uniform_text": first.notation_for_face("bottom"),
        "bottom_uniform_required_mm2": max(design.required_area_mm2 for design in bottoms),
        "bottom_uniform_provided_mm2": first.provided_area_mm2,
        "bottom_uniform_diameters_mm": ",".join(str(value) for value in diameters),
        "bottom_uniform_layers": first.layers,
        "bottom_uniform_rows": [
            {"count": row.count, "diameter_mm": row.diameter_mm, "role": row.role}
            for row in first.rows
        ],
    }


def _record_initial_layout(
    raw: dict[str, Any],
    result: BeamDetailingResult,
) -> None:
    raw["iteration_status"] = result.iteration_status
    raw["iteration_count"] = result.iteration_count
    raw["coordination_initial_signature"] = "|".join(_result_layout_signature(result))
    raw.update(_row_layout_fields(result))
    raw.update(_uniform_bottom_fields(result))
    raw.update(_constructability_row_fields(result))


def _row_layout_fields(result: BeamDetailingResult) -> dict[str, Any]:
    """Serialize every physical row for all regions and faces.

    Notation remains a display convenience; replay and every downstream
    consumer can use this lossless row payload instead.
    """
    regions: dict[str, Any] = {}
    stirrups = _region_stirrup_fields(result)["region_stirrups"]
    audit: list[dict[str, Any]] = []
    for region in result.regions:
        region_rows: dict[str, list[dict[str, Any]]] = {}
        for face_name, design in (("top", region.top), ("bottom", region.bottom)):
            rows = [
                {
                    "count": row.count,
                    "diameter_mm": row.diameter_mm,
                    "role": row.role,
                    "credited_area_mm2": row.credited_area_mm2,
                }
                for row in design.rows
            ]
            region_rows[face_name] = rows
            audit.append({
                "region": region.name,
                "face": face_name,
                "required_area_mm2": design.required_area_mm2,
                "provided_area_mm2": design.provided_area_mm2,
                "effective_depth_mm": design.effective_depth_mm,
                "row_centers_mm": list(design.row_centers_mm),
                "row_x_mm": [list(row) for row in design.row_x_mm],
                "constraint_sources": list(design.constraint_sources),
                "rows": rows,
            })
        regions[region.name] = region_rows
    fields = {
        "longitudinal_rows": regions,
        "region_stirrups": stirrups,
        **_detailing_policy_audit_fields(result),
        "row_coordination_audit": audit,
        "row_coordination_policy": "same_physical_row_same_diameter; distinct_rows_may_differ",
    }
    if result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE":
        search = next((item for item in reversed(result.coordination_audit)
                       if isinstance(item, dict) and item.get("search_complete") is False), {})
        fields.update({key: search.get(key) for key in ("search_complete", "termination_stage", "termination_reason", "budget_limit", "states_used", "attempted_over_budget")})
    candidate_search_audit = next(
        (
            item
            for item in reversed(result.coordination_audit)
            if isinstance(item, dict)
            and item.get("strategy") == "R6_EXACT_CANDIDATE_STATE_DEDUP_V1"
        ),
        None,
    )
    if candidate_search_audit is not None:
        fields["candidate_search_audit_json"] = json.dumps(
            candidate_search_audit,
            ensure_ascii=False,
        )
    return fields


def _record_candidate_search_audit(raw: dict[str, Any], error: Exception) -> None:
    audit = getattr(error, "candidate_search_audit", None)
    if audit is not None:
        raw["candidate_search_audit_json"] = json.dumps(audit, ensure_ascii=False)


def _region_stirrup_fields(result: BeamDetailingResult) -> dict[str, Any]:
    return {
        "region_stirrups": {
            region.name: {
                "diameter_mm": region.stirrup.diameter_mm,
                "spacing_mm": region.stirrup.spacing_mm,
                "legs": region.stirrup.legs,
            }
            for region in result.regions
        }
    }


def _detailing_policy_audit_fields(result: BeamDetailingResult) -> dict[str, Any]:
    return {
        "detailing_policy_audit_json": json.dumps(
            result.coordination_audit,
            ensure_ascii=False,
        )
    }


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
    if capacity_review is not None and capacity_review.status == "ETABS_DESIGN_RESULTS_ONLY":
        capacity_fields.update({
            "side_reinforcement_status": "OK" if result.face_rebar.bars_each_side else "NOT_APPLICABLE",
            "side_reinforcement_policy": "INDEPENDENT_CONSTRUCTION_ONLY",
            "side_reinforcement_strength_credit": False,
            "side_reinforcement_review_message": result.face_rebar.control_reason,
        })
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


def _rebase_support_face(
    context: SupportFaceContext | None,
    parent_offset_mm: float,
    part_length_mm: float,
) -> SupportFaceContext | None:
    if context is None:
        return None
    face_location = context.face_location_mm - parent_offset_mm
    if face_location < -1.0 or face_location > part_length_mm + 1.0:
        return None
    centerline = context.centerline_location_mm
    local_centerline = None if centerline is None else centerline - parent_offset_mm
    if local_centerline is not None and not (-1.0 <= local_centerline <= part_length_mm + 1.0):
        local_centerline = None
    return replace(
        context,
        face_location_mm=max(0.0, min(part_length_mm, face_location)),
        centerline_location_mm=(
            max(0.0, min(part_length_mm, local_centerline))
            if local_centerline is not None
            else None
        ),
    )


def _translate_support_face_to_domain(
    context: SupportFaceContext | None,
    domain_start_mm: float,
    domain_length_mm: float,
) -> SupportFaceContext | None:
    """Rebase a real support face to calculation mileage if it lies in-domain."""
    if context is None:
        return None
    face_location = context.face_location_mm - domain_start_mm
    if face_location < -1.0 or face_location > domain_length_mm + 1.0:
        return None
    centerline = context.centerline_location_mm
    if centerline is not None:
        centerline -= domain_start_mm
        if centerline < -1.0 or centerline > domain_length_mm + 1.0:
            centerline = None
    return replace(
        context,
        face_location_mm=max(0.0, min(domain_length_mm, face_location)),
        centerline_location_mm=(
            max(0.0, min(domain_length_mm, centerline)) if centerline is not None else None
        ),
    )


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


def _engine_demand(name: str, control: Mapping[str, Any]) -> BeamRegionDemand:
    return BeamRegionDemand(
        name=name,
        location_mm=float(control.get("top_demand_station_mm", control["top_total_station_mm"])),
        as_top_total_mm2=float(control.get("top_demand_mm2", control["top_total_mm2"])),
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
        top_combo=str(control.get("top_demand_combo", control["top_total_combo"])),
        bottom_combo=str(control["bottom_total_combo"]),
        shear_combo=str(control["av_combo"]),
        torsion_combo=str(control["al_combo"]),
        source_objects=tuple(control.get("source_objects", ())),
        units=str(control.get("units", "N_mm_C")),
        shear_source=(
            f"{control.get('result_source') or 'GetSummaryResultsBeam_2'}.VMajorArea (Av/s)"
        ),
        as_flex_top_override_mm2=(
            float(control["top_flex_mm2"]) if control.get("top_flex_mm2") is not None else None
        ),
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
            "engine_status": ("FEASIBLE_OPTIMIZATION_INCOMPLETE" if result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE" else "OK"),
            "mode": result.mode.value,
            "legs": max(region.stirrup.legs for region in result.regions),
            "face_required_mm2": result.face_rebar.required_each_side_mm2,
            "face_text": result.face_rebar.notation,
            "engine_warnings": _engine_warning_text(common, result.warnings),
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
        if face == "top" and region_name == "middle" and result.mode.value == "NORMAL":
            demand_key = "top_demand_mm2" if "top_demand_mm2" in controls[region_name] else demand_key
        row[f"{field}_required_mm2"] = controls[region_name][demand_key]
        row[f"{field}_text"] = design.notation_for_face(face)
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
        **_region_stirrup_fields(result),
        **_detailing_policy_audit_fields(result),
        **_constructability_row_fields(result),
        **_uniform_bottom_fields(result),
        "engine_status": ("FEASIBLE_OPTIMIZATION_INCOMPLETE" if result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE" else "OK"),
        "mode": result.mode.value,
        "et1": by_name["left"].top.notation_for_face("top"),
        "t1": by_name["middle"].top.notation_for_face("top"),
        "et2": by_name["right"].top.notation_for_face("top"),
        "eb1": by_name["left"].bottom.notation_for_face("bottom"),
        "b1": by_name["middle"].bottom.notation_for_face("bottom"),
        "eb2": by_name["right"].bottom.notation_for_face("bottom"),
        "s2": support.notation,
        "s1": by_name["middle"].stirrup.notation,
        "legs": max(region.stirrup.legs for region in result.regions),
        "face": result.face_rebar.notation,
        "engine_warnings": _engine_warning_text(common, result.warnings),
        "iteration_status": result.iteration_status,
        "iteration_count": result.iteration_count,
        "coordination_status": result.coordination_status,
        "continuity_option": (result.continuity_option.value if result.continuity_option is not None else ""),
    }


def _fallback_rebar_notation(area: Any, *, effective_frame_type: str = "", project: Any = None) -> str:
    try:
        value = float(area)
    except (TypeError, ValueError):
        return "需复核（需求缺失）"
    if not math.isfinite(value) or value < 0:
        return "需复核（需求无效）"
    floor = max(project.minimum_main_diameter_mm, 20 if str(effective_frame_type).upper() == "SMF" else 16) if project is not None else 0
    diameters = sorted(d for d in project.ordered_longitudinal_diameters() if d >= floor) if project is not None else []
    if not diameters:
        return "需复核（项目纵筋目录不可用）"
    for diameter in diameters:
        bar_area = math.pi * diameter * diameter / 4.0
        count = max(2, math.ceil(value / bar_area))
        if count <= 8:
            return f"{count}T{diameter}"
    diameter = diameters[-1]
    count = max(2, math.ceil(value / (math.pi * diameter * diameter / 4.0)))
    return f"{count}T{diameter}"


def _fallback_stirrup_text(value: Any, *, source: str, project: Any = None) -> str:
    try:
        av = float(value)
    except (TypeError, ValueError):
        return "需复核（需求缺失）"
    if not math.isfinite(av) or av < 0:
        return "需复核（需求无效）"
    if project is None:
        return "需复核（项目箍筋目录不可用）"
    diameters = sorted(set(project.preferred_stirrup_diameters_mm) & set(project.enabled_diameters_mm))
    spacings = [s for s in project.allowed_spacings_mm if math.isfinite(s) and s > 0]
    if not diameters or not spacings:
        return "需复核（项目箍筋目录不可用）"
    spacing = min(spacings)
    for legs in (2, 4):
        for diameter in diameters:
            if legs * math.pi * diameter ** 2 / 4 / spacing + 1e-9 >= av:
                return f"{legs}T{diameter}@{spacing}"
    diameter = diameters[-1]
    legs = max(4, 2 * math.ceil(av * spacing / (math.pi * diameter ** 2 / 2)))
    return f"{legs}T{diameter}@{spacing}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _fallback_region_texts(
    controls: Mapping[str, Any] | None,
    common: Mapping[str, Any] | None = None,
    project: Any = None,
) -> dict[str, str]:
    if not controls:
        effective_frame_type = str((common or {}).get("effective_frame_type", ""))
        minimum_bar = _fallback_rebar_notation(None, effective_frame_type=effective_frame_type, project=project)
        return {
            "et1": minimum_bar,
            "t1": minimum_bar,
            "et2": minimum_bar,
            "eb1": minimum_bar,
            "b1": minimum_bar,
            "eb2": minimum_bar,
            "s2": _fallback_stirrup_text(None, source="ETABS横筋需求", project=project),
            "s1": _fallback_stirrup_text(None, source="ETABS横筋需求", project=project),
            "face": f"{minimum_bar}（面筋候选）",
        }
    mapping = {
        "et1": ("left", "top_total_mm2"), "t1": ("middle", "top_demand_mm2"), "et2": ("right", "top_total_mm2"),
        "eb1": ("left", "bottom_total_mm2"), "b1": ("middle", "bottom_total_mm2"), "eb2": ("right", "bottom_total_mm2"),
    }
    effective_frame_type = str((common or {}).get("effective_frame_type", ""))
    result = {
        field: _fallback_rebar_notation(
            controls.get(region, {}).get(key, controls.get(region, {}).get("top_total_mm2")),
            effective_frame_type=effective_frame_type, project=project,
        )
        for field, (region, key) in mapping.items()
        if field not in {"eb1", "b1", "eb2"}
    }
    bottom_values = [controls.get(region, {}).get("bottom_total_mm2") for region in REGIONS]
    try:
        bottom_required = max(float(v) for v in bottom_values) if all(v is not None and math.isfinite(float(v)) and float(v) >= 0 for v in bottom_values) else None
    except (TypeError, ValueError):
        bottom_required = None
    bottom_text = _fallback_rebar_notation(
        bottom_required,
        effective_frame_type=effective_frame_type, project=project,
    )
    result.update({"eb1": bottom_text, "b1": bottom_text, "eb2": bottom_text})
    end_values = [controls.get(name, {}).get("av_over_s") for name in ("left", "right")]
    try:
        end_demand = max(float(v) for v in end_values) if all(v is not None and math.isfinite(float(v)) and float(v) >= 0 for v in end_values) else None
    except (TypeError, ValueError):
        end_demand = None
    result["s2"] = _fallback_stirrup_text(end_demand, source="ETABS横筋需求", project=project)
    result["s1"] = _fallback_stirrup_text(controls.get("middle", {}).get("av_over_s"), source="ETABS横筋需求", project=project)
    result["face"] = "面筋：按项目规则需人工复核"
    return result


def _empty_review(
    common: Mapping[str, Any],
    status: str,
    controls: Mapping[str, Any] | None = None,
    project: Any = None,
) -> dict[str, Any]:
    candidates = _fallback_region_texts(controls, common, project)
    required = {}
    if controls:
        required = {
            "et1_required_mm2": controls["left"].get("top_total_mm2"),
            "t1_required_mm2": controls["middle"].get(
                "top_demand_mm2", controls["middle"].get("top_total_mm2")
            ),
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
    project: Any = None,
) -> dict[str, Any]:
    candidates = _fallback_region_texts(controls, common, project)
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


_V3_UNSELECTED_DISPLAY_FIELDS = (
    "et1", "t1", "et2", "eb1", "b1", "eb2", "s2", "s1", "face", "stirrups", "legs",
    "et1_text", "t1_text", "et2_text", "eb1_text", "b1_text", "eb2_text",
    "s2_text", "s1_text", "s2_left_text", "s2_right_text", "face_text",
    "bottom_uniform_text", "bottom_uniform_diameters_mm", "bottom_uniform_layers",
)


def _clear_v3_unselected_design_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Keep demands and errors while removing any layout that was not selected by V3."""

    for field in _V3_UNSELECTED_DISPLAY_FIELDS:
        row[field] = ""
    if row.get("engine_status") == "EXCLUDED_NO_DESIGN":
        for field in _V3_UNSELECTED_DISPLAY_FIELDS:
            row[field] = "N.A."
        row.update({
            "mode": "EXCLUDED_NO_DESIGN",
            "bottom_reinforcement_strategy": "EXCLUDED_NO_DESIGN",
            "engine_warnings": "EXCLUDED_NO_DESIGN",
            "review_note": "Excluded from concrete design by ETABS; retained in source geometry and numbering only.",
        })
        return row
    row.update({
        "mode": "NO_V3_RECOMMENDATION",
        "bottom_reinforcement_strategy": "",
        "bottom_uniform_regions_match": None,
        "bottom_uniform_provided_mm2": None,
        "bottom_uniform_rows": [],
        "region_stirrups": {},
        "longitudinal_rows": {},
        "row_coordination_audit": [],
        "row_coordination_policy": "",
        "detailing_policy_audit_json": "[]",
        "coordination_status": "",
        "coordination_initial_signature": "",
        "coordination_final_signature": "",
        "continuity_option": "",
        "rebar_layout_geometry_json": "{}",
        "v3_result_selected": False,
        "engine_warnings": f"V3未生成推荐配筋；具体状态：{row.get('engine_status', '')}",
        "review_note": "本行没有实际选定的V3纵筋与箍筋方案；请按具体状态复核，不能据此施工。",
    })
    return row


def _empty_review_for_policy(
    engine: BeamDetailingEngine,
    common: Mapping[str, Any],
    status: str,
    controls: Mapping[str, Any] | None = None,
    project: Any = None,
) -> dict[str, Any]:
    row = _empty_review(common, status, controls, project)
    if _engine_detailing_policy(engine) == DRAWING_V3_POLICY_ID:
        return _clear_v3_unselected_design_fields(row)
    return row


def _empty_final_for_policy(
    engine: BeamDetailingEngine,
    common: Mapping[str, Any],
    status: str,
    controls: Mapping[str, Any] | None = None,
    project: Any = None,
) -> dict[str, Any]:
    row = _empty_final(common, status, controls, project)
    if _engine_detailing_policy(engine) == DRAWING_V3_POLICY_ID:
        return _clear_v3_unselected_design_fields(row)
    return row
