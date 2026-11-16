from __future__ import annotations

from types import SimpleNamespace

from src.design.detailing import (
    DRAWING_V3_POLICY_ID,
    DetailingError,
    make_fresh_detailing_engine,
)
from src.design import row_solver, v3
from tools.drawing_v3_validation_data import (
    decode_frozen_line_inputs,
    read_frozen_line,
)


def test_failed_preferred_final_checks_share_materialization_budget_with_full_tier(monkeypatch):
    data = decode_frozen_line_inputs(
        read_frozen_line(("T1-L1", "PB", "T1-L1-PB2"))
    )[0]
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    local_tiers = []
    materialized = []
    final_validation_calls = []

    def fake_local_records(index, input_data, _engine, *, audit, allowed_main_diameters, **_kwargs):
        preferred = tuple(allowed_main_diameters) == (20,)
        local_tiers.append("preferred" if preferred else "full")
        records = []
        for ordinal in range(2 if preferred else 3):
            records.append(SimpleNamespace(
                index=index,
                input_data=input_data,
                regions=(),
                face_rebar=None,
                left_support=((2, 20),),
                right_support=((2, 20),),
                pb_row_diameters=(20,),
                bottom_signature=((3, 16, "longitudinal"),),
                hoop_signature=((10, 150, 2),),
                preference_tier=(0, 0, 0, 0),
                heuristic=(ordinal,),
            ))
        audit["local_combo_checks"] += 1
        audit["local_records_seen"] += len(records)
        audit["interface_state_count"] += len(records)
        return tuple(records)

    def fake_validate_full_path(path, *_args):
        return [
            record
            for component_path in path
            for unit in component_path
            for record in unit.records
        ]

    def fake_materialize(record, _option, _engine, **_kwargs):
        materialized.append(record.heuristic)
        return SimpleNamespace(
            beam_name=record.input_data.section.name,
            section=record.input_data.section,
            regions=(),
            face_rebar=_engine._construction_face_rebar(record.input_data.section),
            input_data=record.input_data,
        )

    def fake_validate_final_line(*_args):
        final_validation_calls.append(len(final_validation_calls) + 1)
        raise DetailingError("INJECTED_FINAL_PATH_REJECTION")

    monkeypatch.setattr(v3, "_local_records", fake_local_records)
    monkeypatch.setattr(v3, "_validate_full_path", fake_validate_full_path)
    monkeypatch.setattr(v3, "_materialize_record", fake_materialize)
    monkeypatch.setattr(v3, "_validate_final_line", fake_validate_final_line)
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda _results, _engine: ())

    result = v3.design_line_v3(
        (data,),
        (),
        engine.project.default_continuity_option,
        engine,
        k=2,
    )

    assert local_tiers == ["preferred"]
    assert len(materialized) == len(final_validation_calls) == 2
    assert result.audit["materialization_budget"] == 2
    assert result.audit["complete_materializations"] == 2
    assert result.audit["complete_validation_calls"] == 2
    assert result.audit["complete_validation_passes"] == 0
    assert result.alternatives == ()
    assert result.status == "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET"
    assert result.audit["hard_infeasible"] is False
    assert result.audit["preference_tier_search"]["preferred_feasibility_status"] == (
        "PATHS_FOUND_BUT_NO_VALID_MATERIALIZED_RECOMMENDATION"
    )
    assert result.audit["preference_tier_search"]["global_infeasibility_proven"] is False
    assert result.audit["preference_tier_search"]["expanded_to_full_catalog"] is False


def test_full_tier_fallback_uses_remaining_original_budget_after_preferred_has_no_path(monkeypatch):
    data = decode_frozen_line_inputs(
        read_frozen_line(("T1-L1", "PB", "T1-L1-PB2"))
    )[0]
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    local_tiers = []
    materialized = []
    final_validation_calls = []

    def fake_local_records(index, input_data, _engine, *, audit, allowed_main_diameters, **_kwargs):
        preferred = tuple(allowed_main_diameters) == (20,)
        local_tiers.append("preferred" if preferred else "full")
        records = []
        for ordinal in range(0 if preferred else 2):
            records.append(SimpleNamespace(
                index=index,
                input_data=input_data,
                regions=(),
                face_rebar=None,
                left_support=((2, 20),),
                right_support=((2, 20),),
                pb_row_diameters=(20,),
                bottom_signature=((3, 16, "longitudinal"),),
                hoop_signature=((10, 150, 2),),
                preference_tier=(0, 0, 0, 0),
                heuristic=(ordinal,),
            ))
        audit["local_combo_checks"] += 1
        audit["local_records_seen"] += len(records)
        audit["interface_state_count"] += len(records)
        return tuple(records)

    def fake_validate_full_path(path, *_args):
        return [
            record
            for component_path in path
            for unit in component_path
            for record in unit.records
        ]

    def fake_materialize(record, _option, _engine, **_kwargs):
        materialized.append(record.heuristic)
        return SimpleNamespace(
            beam_name=record.input_data.section.name,
            section=record.input_data.section,
            regions=(),
            face_rebar=_engine._construction_face_rebar(record.input_data.section),
            input_data=record.input_data,
        )

    def fail_first_complete_validation(*_args):
        final_validation_calls.append(len(final_validation_calls) + 1)
        if len(final_validation_calls) == 1:
            raise DetailingError("INJECTED_FIRST_FULL_TIER_REJECTION")

    monkeypatch.setattr(v3, "_local_records", fake_local_records)
    monkeypatch.setattr(v3, "_validate_full_path", fake_validate_full_path)
    monkeypatch.setattr(v3, "_materialize_record", fake_materialize)
    monkeypatch.setattr(v3, "_validate_final_line", fail_first_complete_validation)
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda _results, _engine: ())

    result = v3.design_line_v3(
        (data,),
        (),
        engine.project.default_continuity_option,
        engine,
        k=2,
    )

    assert local_tiers == ["preferred", "full"]
    assert len(materialized) == len(final_validation_calls) == 2
    assert result.audit["materialization_budget"] == 2
    assert result.audit["complete_materializations"] == 2
    assert result.audit["complete_validation_calls"] == 2
    assert result.audit["complete_validation_passes"] == 1
    assert len(result.alternatives) == 1
    assert result.status == "RECOMMENDATION_SET_ONLY"
    assert result.audit["preference_tier_search"]["expanded_to_full_catalog"] is True
