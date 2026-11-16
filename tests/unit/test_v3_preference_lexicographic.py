from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from src.design import row_solver, v3
from src.design.detailing import (
    BeamDetailingProfile,
    DRAWING_V3_POLICY_ID,
    make_fresh_detailing_engine,
)
from tools.drawing_v3_validation_data import decode_frozen_line_inputs, read_frozen_line


def test_longitudinal_diameter_preference_precedes_hoop_and_style_rank(monkeypatch):
    source_data = decode_frozen_line_inputs(
        read_frozen_line(("T1-L1", "PB", "T1-L1-PB2"))
    )[0]
    data = replace(
        source_data,
        section=replace(
            source_data.section,
            detailing_profile=BeamDetailingProfile.NON_SWAY,
        ),
    )
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    bottom_signature = ((4, 20, "longitudinal"),)

    def make_record(index, *, top_diameter, hoop_diameter, label, style_rank):
        top_row = SimpleNamespace(diameter_mm=top_diameter, role="longitudinal")
        bottom_row = SimpleNamespace(diameter_mm=20, role="longitudinal")
        top = SimpleNamespace(rows=(top_row,))
        bottom = SimpleNamespace(rows=(bottom_row,))
        hoops = tuple((hoop_diameter, 100, 4) for _ in range(3))
        tier = v3._preference_tier((top, top, top), bottom, data.section, hoops)
        return v3._SpanRecord(
            index=index,
            input_data=data,
            regions=(),
            face_rebar=None,
            left_support=((4, top_diameter),),
            right_support=((4, top_diameter),),
            pb_row_diameters=(top_diameter,),
            bottom_signature=bottom_signature,
            hoop_signature=hoops,
            preference_tier=tier,
            heuristic=(*tier, style_rank, index),
        ), label, style_rank

    all_preferred = make_record(
        0, top_diameter=20, hoop_diameter=12, label="all20+T12", style_rank=100
    )
    diameter_fallback = make_record(
        0, top_diameter=25, hoop_diameter=10, label="top25+T10", style_rank=0
    )
    records_by_index = {
        0: (all_preferred[0], diameter_fallback[0]),
        1: (
            replace(all_preferred[0], index=1, heuristic=(*all_preferred[0].heuristic[:-1], 1)),
            replace(diameter_fallback[0], index=1, heuristic=(*diameter_fallback[0].heuristic[:-1], 1)),
        ),
    }

    def local_records(index, _data, _engine, *, audit, **_kwargs):
        records = records_by_index[index]
        audit["local_combo_checks"] += len(records)
        audit["local_records_seen"] += len(records)
        audit["interface_state_count"] += len(records)
        audit.setdefault("local_search_by_input", {})[str(index)] = {
            "truncated": False,
            "hard_valid_records": len(records),
        }
        return records

    labels = {"all20+T12": 100, "top25+T10": 0}

    def materialize(record, _option, _engine, *, search_index, parent_unknown=False):
        label = "all20+T12" if record.preference_tier[0] == 0 else "top25+T10"
        top_count, top_diameter = record.left_support[0]
        top = SimpleNamespace(
            rows=(SimpleNamespace(
                count=top_count,
                diameter_mm=top_diameter,
                role="longitudinal",
            ),),
            row_centers_mm=(60.0,),
            row_x_mm=(tuple(float(value) for value in range(top_count)),),
        )
        bottom_count, bottom_diameter, bottom_role = record.bottom_signature[0]
        bottom = SimpleNamespace(
            rows=(SimpleNamespace(
                count=bottom_count,
                diameter_mm=bottom_diameter,
                role=bottom_role,
            ),),
            row_centers_mm=(60.0,),
            row_x_mm=(tuple(float(value) for value in range(bottom_count)),),
        )
        regions = tuple(SimpleNamespace(
            name=name,
            top=top,
            bottom=bottom,
            stirrup=SimpleNamespace(
                diameter_mm=diameter,
                spacing_mm=spacing,
                legs=legs,
            ),
        ) for name, (diameter, spacing, legs) in zip(
            ("left", "middle", "right"), record.hoop_signature
        ))
        return SimpleNamespace(
            beam_name=record.input_data.section.name,
            index=record.index,
            section=record.input_data.section,
            regions=regions,
            face_rebar=_engine._construction_face_rebar(record.input_data.section),
            preference_label=label,
            style_rank=labels[label],
            search_index=search_index,
        )

    monkeypatch.setattr(v3, "_local_records", local_records)
    monkeypatch.setattr(v3, "_materialize_record", materialize)
    monkeypatch.setattr(v3, "_validate_final_line", lambda *_args: None)
    monkeypatch.setattr(
        row_solver,
        "_style_path_rank",
        lambda results, _engine: sum(result.style_rank for result in results),
    )

    result = v3.design_line_v3(
        (data, data),
        ((0, 1),),
        engine.project.default_continuity_option,
        engine,
        k=10,
        _catalog_tier="full",
    )

    assert result.alternatives
    assert result.alternatives[0][0].preference_label == "all20+T12"
    assert result.alternatives[1][0].preference_label == "top25+T10"
    assert result.alternatives[0][0].style_rank > result.alternatives[1][0].style_rank
    assert result.audit["complete_materializations"] == 2


def test_sb_diameter_fallback_flags_follow_16_then_12_then_20_then_25():
    section = SimpleNamespace(is_secondary=True)
    hoops = ((10, 100, 2),) * 3

    def tier(diameter):
        row = SimpleNamespace(diameter_mm=diameter, role="longitudinal")
        design = SimpleNamespace(rows=(row,))
        return v3._preference_tier((design, design, design), design, section, hoops)[:3]

    assert tier(16) == (0, 0, 0)
    assert tier(12) == (0, 0, 1)
    assert tier(20) == (0, 1, 0)
    assert tier(25) == (1, 0, 0)
