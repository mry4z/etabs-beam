"""Audit and ETABS review helpers for structural Spans with mixed sections."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import AbstractSet, Any, Iterable, Mapping

from src.core.beam_line_span_analyzer import BeamLineSpanResult
from src.core.etabs_connector import api_values
from src.core.geometry_extractor import BeamGeometry
from src.core.numbering import canonical_span_id
from src.utils.geometry import Point3D


@dataclass(frozen=True)
class MultiSectionSpanRecord:
    """One structural Span whose source Objects use multiple Frame sections."""

    span_id: str
    story: str
    beam_type: str
    line_number: int
    span_index: int
    sections: tuple[str, ...]
    section_objects: tuple[tuple[str, tuple[str, ...]], ...]
    source_objects: tuple[str, ...]
    path_points: tuple[Point3D, ...]
    etabs_group_name: str
    status: str = "REQUIRES_SECTION_REVIEW"
    reason: str = "同一结构跨包含多个截面，必须由用户确认 step beam 或调整分跨。"


def build_multi_section_span_records(
    result: BeamLineSpanResult,
    beams: Iterable[BeamGeometry],
    *,
    confirmed_span_ids: AbstractSet[str] | None = None,
) -> tuple[MultiSectionSpanRecord, ...]:
    """Return deterministic review records for every mixed-section Span."""

    by_name = {beam.name: beam for beam in beams}
    confirmed_span_ids = confirmed_span_ids or set()
    records: list[MultiSectionSpanRecord] = []
    for span in result.spans:
        section_members: dict[str, list[str]] = {}
        source_objects = tuple(dict.fromkeys(span.source_objects))
        for name in source_objects:
            beam = by_name.get(name)
            section = beam.section.strip() if beam is not None else "<MISSING>"
            section_members.setdefault(section or "<NONE>", []).append(name)
        if len(section_members) <= 1:
            continue
        span_id = canonical_span_id(
            span.story,
            span.beam_type,
            span.line_number,
            span.span_index,
        )
        section_objects = tuple((section, tuple(names)) for section, names in sorted(section_members.items()))
        records.append(
            MultiSectionSpanRecord(
                span_id=span_id,
                story=span.story,
                beam_type=span.beam_type,
                line_number=span.line_number,
                span_index=span.span_index,
                sections=tuple(section for section, _ in section_objects),
                section_objects=section_objects,
                source_objects=source_objects,
                path_points=span.path_points,
                etabs_group_name=_review_group_name(span_id),
                status=("CONFIRMED_STEP_BEAM" if span_id in confirmed_span_ids else "REQUIRES_SECTION_REVIEW"),
                reason=(
                    "用户已确认该多截面 Span 为同一 step beam。"
                    if span_id in confirmed_span_ids
                    else "同一结构跨包含多个截面，必须由用户确认 step beam 或调整分跨。"
                ),
            )
        )
    return tuple(records)


def write_multi_section_span_audit(
    records: Iterable[MultiSectionSpanRecord],
    output_dir: str | Path,
    stem: str = "multi_section_span_review",
) -> tuple[Path, Path]:
    """Write JSON and CSV lists for user review before model rename."""

    rows = tuple(records)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    csv_path = directory / f"{stem}.csv"
    json_path.write_text(
        json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "SpanID",
                "Story",
                "BeamType",
                "Sections",
                "SectionObjects",
                "SourceObjects",
                "ETABSGroup",
                "Status",
                "Reason",
                "PathPoints",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.span_id,
                    row.story,
                    row.beam_type,
                    ";".join(row.sections),
                    ";".join(f"{section}:{'+'.join(names)}" for section, names in row.section_objects),
                    "+".join(row.source_objects),
                    row.etabs_group_name,
                    row.status,
                    row.reason,
                    ";".join(f"{point[0]:.3f},{point[1]:.3f},{point[2]:.3f}" for point in row.path_points),
                ]
            )
    return json_path, csv_path


def create_etabs_section_review_groups(
    sap_model: Any,
    records: Iterable[MultiSectionSpanRecord],
    *,
    allow_mutation: bool = False,
    colors: Mapping[str, int] | None = None,
) -> tuple[str, ...]:
    """Create colored native ETABS Groups and assign each review Span.

    This changes group metadata in the active model and therefore requires an
    explicit mutation flag.  It does not save the model or change geometry.
    In ETABS the user can display colors ``by group`` or select one group to
    highlight all member Frame Objects.
    """

    if not allow_mutation:
        raise PermissionError("创建 ETABS 审查 Group 必须显式 allow_mutation=True。")
    from ETABSv1 import eItemType

    created: list[str] = []
    palette = (42495, 65535, 16753920, 16711935, 8454016, 33023)
    for index, record in enumerate(records):
        if record.status != "REQUIRES_SECTION_REVIEW":
            continue
        color = (colors or {}).get(
            record.etabs_group_name,
            palette[index % len(palette)],
        )
        api_values(
            sap_model.GroupDef.SetGroup(
                record.etabs_group_name,
                int(color),
                True,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
            f"GroupDef.SetGroup({record.etabs_group_name})",
        )
        for object_name in record.source_objects:
            api_values(
                sap_model.FrameObj.SetGroupAssign(
                    object_name,
                    record.etabs_group_name,
                    False,
                    eItemType.Objects,
                ),
                f"FrameObj.SetGroupAssign({object_name})",
            )
        created.append(record.etabs_group_name)
    return tuple(created)


def select_etabs_review_group(
    sap_model: Any,
    group_name: str,
    *,
    clear_existing: bool = True,
) -> None:
    """Select/highlight one previously created ETABS review Group."""

    if clear_existing:
        api_values(sap_model.SelectObj.ClearSelection(), "SelectObj.ClearSelection")
    api_values(
        sap_model.SelectObj.Group(group_name, False),
        f"SelectObj.Group({group_name})",
    )


def _review_group_name(span_id: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", span_id)
    return f"CODX_MS_{safe}"[:79]
