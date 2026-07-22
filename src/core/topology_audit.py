"""Serializable topology audit records for blocked multi-Span Objects."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

from src.core.beam_line_span_analyzer import BeamLineSpanResult
from src.core.geometry_extractor import BeamGeometry
from src.core.model_writeback import FrameState, WritebackPlan


@dataclass(frozen=True)
class RequiresSplitRecord:
    """One unique ETABS Object that contributes to multiple structural Spans."""

    object_name: str
    guid: str
    label: str
    story: str
    section: str
    point_i: str
    point_j: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    span_ids: tuple[str, ...]
    beam_types: tuple[str, ...]
    cantilevers: tuple[bool, ...]
    status: str
    reason: str


def build_requires_split_records(
    result: BeamLineSpanResult,
    beams: Iterable[BeamGeometry],
    states: Mapping[str, FrameState],
    plan: WritebackPlan,
) -> tuple[RequiresSplitRecord, ...]:
    """Build one deterministic audit row per blocked Object."""

    by_name = {beam.name: beam for beam in beams}
    span_map: dict[str, list[object]] = {}
    for span in result.spans:
        span_id = f"{span.story}-{span.beam_type}{span.line_number}-{span.span_index}"
        for source_name in span.source_objects:
            span_map.setdefault(source_name, []).append((span_id, span.beam_type, span.is_cantilever))

    blocked_by_name = {}
    for action in plan.renames:
        if action.status == "REQUIRES_SPLIT":
            blocked_by_name.setdefault(action.name, action)

    records: list[RequiresSplitRecord] = []
    for name in sorted(blocked_by_name):
        action = blocked_by_name[name]
        beam = by_name.get(name)
        state = states.get(name)
        if beam is None:
            continue
        occurrences = span_map.get(name, [])
        records.append(
            RequiresSplitRecord(
                object_name=name,
                guid=state.guid if state else action.guid,
                label=state.label if state else beam.label,
                story=state.story if state else beam.story,
                section=state.section if state else beam.section,
                point_i=state.point_i if state else beam.point_i,
                point_j=state.point_j if state else beam.point_j,
                start=beam.start,
                end=beam.end,
                span_ids=tuple(item[0] for item in occurrences),
                beam_types=tuple(item[1] for item in occurrences),
                cantilevers=tuple(bool(item[2]) for item in occurrences),
                status=action.status,
                reason=action.reason,
            )
        )
    return tuple(records)


def write_requires_split_audit(
    records: Iterable[RequiresSplitRecord], output_dir: str | Path, stem: str
) -> tuple[Path, Path]:
    """Write machine-readable JSON and review-friendly CSV audit files."""

    rows = tuple(records)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    csv_path = directory / f"{stem}.csv"
    json_payload = [asdict(row) for row in rows]
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Object",
                "GUID",
                "Label",
                "Story",
                "Section",
                "PointI",
                "PointJ",
                "Start",
                "End",
                "SpanIDs",
                "BeamTypes",
                "Cantilevers",
                "Status",
                "Reason",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.object_name,
                    row.guid,
                    row.label,
                    row.story,
                    row.section,
                    row.point_i,
                    row.point_j,
                    _point_text(row.start),
                    _point_text(row.end),
                    ";".join(row.span_ids),
                    ";".join(row.beam_types),
                    ";".join("1" if value else "0" for value in row.cantilevers),
                    row.status,
                    row.reason,
                ]
            )
    return json_path, csv_path


def _point_text(point: tuple[float, float, float]) -> str:
    return ",".join(f"{value:.3f}" for value in point)
