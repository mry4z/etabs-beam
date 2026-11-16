from __future__ import annotations

from types import SimpleNamespace

from src.design import row_solver, v3
from src.design.detailing import DRAWING_V3_POLICY_ID, make_fresh_detailing_engine
from tools.drawing_v3_validation_data import decode_frozen_line_inputs, read_frozen_line


def _run_three_span_partial_line(monkeypatch, pb_diameters):
    data = decode_frozen_line_inputs(
        read_frozen_line(("T1-L1", "PB", "T1-L1-PB2"))
    )[0]
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")

    def local_records(index, input_data, _engine, *, audit, **_kwargs):
        if index == 1:
            audit.setdefault("local_search_by_input", {})[str(index)] = {
                "truncated": False,
                "hard_valid_records": 0,
                "terminal_blocker": {
                    "reason": "NO_ALLOWED_SPACING",
                    "minimum_allowed_spacing_mm": 100,
                    "maximum_possible_spacing_mm": 75,
                },
            }
            return ()
        record = v3._SpanRecord(
            index=index,
            input_data=input_data,
            regions=(),
            face_rebar=None,
            left_support=((2, 20),),
            right_support=((2, 20),),
            pb_row_diameters=(pb_diameters[index],),
            bottom_signature=(),
            hoop_signature=(),
            preference_tier=(0, 0, 0, 0),
            heuristic=(index,),
        )
        audit.setdefault("local_search_by_input", {})[str(index)] = {
            "truncated": False,
            "hard_valid_records": 1,
        }
        return (record,)

    monkeypatch.setattr(v3, "_local_records", local_records)
    monkeypatch.setattr(
        v3,
        "_materialize_record",
        lambda record, _option, _engine, *, search_index, parent_unknown=False: SimpleNamespace(
            index=record.index,
            section=record.input_data.section,
            regions=(),
            coordination_status=("UNKNOWN" if parent_unknown else "STABLE"),
            search_index=search_index,
        ),
    )
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda _results, _engine: 0)

    return v3.design_line_v3(
        (data, data, data),
        (),
        engine.project.default_continuity_option,
        engine,
        k=10,
        _catalog_tier="full",
    )


def test_matching_pb_vectors_keep_partial_blocks_separated_by_no_spacing(monkeypatch):
    result = _run_three_span_partial_line(monkeypatch, {0: 20, 2: 20})

    assert result.status == "CONTINUITY_UNKNOWN_PARTIAL_SEGMENTS"
    assert len(result.alternatives) == 1
    assert result.selected[0] is not None
    assert result.selected[1] is None
    assert result.selected[2] is not None
    assert result.errors == (None, "NO_ALLOWED_SPACING", None)
    assert result.audit["solved_components"] == [[0], [2]]
    assert result.audit["continuity_unknown_due_to_unsolved_span"] is True
    assert result.audit["complete_materializations"] == 0
    assert result.audit["partial_materialization_attempts"] == 1
    assert result.audit["partial_validation_passes"] == 1
    assert result.audit["cross_component_pb_vector_conflicts"] == 0


def test_conflicting_pb_vectors_do_not_stitch_partial_blocks(monkeypatch):
    result = _run_three_span_partial_line(monkeypatch, {0: 20, 2: 25})

    assert result.status == "CONTINUITY_UNKNOWN_PARTIAL_SEGMENTS"
    assert result.alternatives == ()
    assert result.selected == (None, None, None)
    assert result.errors == (
        "PB_ROW_DIAMETER_CONFLICT_ACROSS_SOLVED_COMPONENTS",
        "NO_ALLOWED_SPACING",
        "PB_ROW_DIAMETER_CONFLICT_ACROSS_SOLVED_COMPONENTS",
    )
    assert result.audit["cross_component_pb_vector_conflicts"] > 0
    assert result.audit["partial_materialization_attempts"] == 0
    assert result.audit["complete_materializations"] == 0
