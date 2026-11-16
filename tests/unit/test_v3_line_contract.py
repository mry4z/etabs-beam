from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from src.design.detailing import (
    ContinuityOption,
    BeamDetailingProfile,
    DRAWING_V3_POLICY_ID,
    DetailingError,
    BeamSection,
    LongitudinalDesign,
    PhysicalRow,
    RegionDetailing,
    StirrupDesign,
    make_fresh_detailing_engine,
)
from src.design.v3 import (
    _common_hoop_legs,
    _parent_join_hoop_signature,
    _validate_final_line,
    _unit_options,
    design_line_v3,
)
from tools.drawing_v3_validation_data import (
    decode_frozen_line_inputs,
    read_frozen_line,
)


@dataclass(frozen=True)
class _ParentProofResult:
    beam_name: str
    section: BeamSection
    input_data: object
    constructability: object
    regions: tuple
    coordination_status: str = "V3_RECOMMENDATION_SET_ONLY"
    coordination_audit: tuple = ()
    smf_checks: tuple = ()

    @property
    def errors(self):
        return self.constructability.blocking_statuses


def _parent_proof_result(name, *, start, profile, role, legs=4, parent_context=True):
    from src.design.constructability import ConstructabilityResult

    section = BeamSection(
        name, 400.0, 800.0, 40.0, 30.0, 420.0, 420.0, 20.0, 6000.0,
        detailing_profile=profile, beam_role=role,
    )
    top = LongitudinalDesign(rows=(PhysicalRow(2, 20),), effective_depth_mm=700.0)
    bottom = LongitudinalDesign(rows=(PhysicalRow(2, 20),), effective_depth_mm=700.0)
    regions = tuple(
        RegionDetailing(
            region,
            SimpleNamespace(name=region),
            0.0,
            0.0,
            0.0,
            StirrupDesign(10, 100, legs, 0.0, 0.0, 100.0, "fixture"),
            top,
            bottom,
        )
        for region in ("left", "middle", "right")
    )
    context = (
        SimpleNamespace(
            parent_span_id="P1",
            parent_length_mm=12000.0,
            segment_start_mm=float(start),
            left_depth_mm=800.0,
            right_depth_mm=800.0,
        )
        if parent_context else None
    )
    return _ParentProofResult(
        name,
        section,
        SimpleNamespace(parent_context=context, left_support=None, right_support=None),
        ConstructabilityResult("OK"),
        regions,
    )


def test_missing_input_reports_unknown_break_without_claiming_infeasibility():
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")

    result = design_line_v3(
        (None,),
        (),
        engine.project.default_continuity_option,
        engine,
    )

    assert result.status == "CONTINUITY_UNKNOWN_PARTIAL_SEGMENTS"
    assert result.selected == (None,)
    assert result.alternatives == ()
    assert result.errors == ("INPUT_MISSING_CONTINUITY_BREAK",)
    assert result.audit["hard_infeasible"] is False
    assert result.audit["complete_materializations"] == 0


def test_unresolved_profile_blocks_v3_instead_of_falling_back_to_ordinary_beam():
    frozen = read_frozen_line(("T1-L1", "PB", "T1-L1-PB2"))
    data = decode_frozen_line_inputs(frozen)[0]
    unresolved = replace(
        data,
        section=replace(data.section, detailing_profile=BeamDetailingProfile.UNRESOLVED),
    )
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")

    result = design_line_v3(
        (unresolved,),
        (),
        engine.project.default_continuity_option,
        engine,
    )

    assert result.selected == (None,)
    assert result.alternatives == ()
    assert result.errors == ("DETAILING_PROFILE_REVIEW_REQUIRED",)
    assert result.audit["complete_materializations"] == 0


@pytest.mark.parametrize("k", (0, 11, True))
def test_v3_refuses_recommendation_limits_outside_one_to_ten(k):
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")

    with pytest.raises(ValueError, match="between 1 and 10"):
        design_line_v3(
            (),
            (),
            engine.project.default_continuity_option,
            engine,
            k=k,
        )


def test_research_actual_parent_hoop_signature_is_opt_in_and_keeps_seed_legs():
    seed_left = ((10, 2, 100),) * 3
    seed_right = ((10, 4, 100),) * 3
    required_legs = (3, 3, 3)

    # The normal production path retains the seed-signature behavior and the
    # old common-leg normalization. Research mode uses actual normalized
    # regions for parent joins and never reduces the seed leg count.
    assert _common_hoop_legs(
        required_legs, seed_right, research_seed_lower_bound=False
    ) == 3
    assert _common_hoop_legs(
        required_legs, seed_right, research_seed_lower_bound=True
    ) == 4

    actual_regions = tuple(
        SimpleNamespace(stirrup=SimpleNamespace(
            diameter_mm=10, legs=4, spacing_mm=100
        ))
        for _ in range(3)
    )
    assert _parent_join_hoop_signature(
        seed_left, actual_regions, use_actual_signature=False
    ) == seed_left
    assert _parent_join_hoop_signature(
        seed_right, actual_regions, use_actual_signature=False
    ) == seed_right
    actual_signature = _parent_join_hoop_signature(
        seed_left, actual_regions, use_actual_signature=True
    )
    actual_signature_other_seed = _parent_join_hoop_signature(
        seed_right, actual_regions, use_actual_signature=True
    )
    assert actual_signature == actual_signature_other_seed

    def record(index, hoop_signature):
        return SimpleNamespace(
            index=index,
            input_data=SimpleNamespace(section=SimpleNamespace(is_smf=False)),
            left_support=((2, 16),),
            right_support=((2, 16),),
            pb_row_diameters=(16,),
            bottom_signature=((4, 20, "main"),),
            hoop_signature=hoop_signature,
            preference_tier=(0, 0, 0, 0),
            heuristic=(index,),
        )

    audit = {
        "parent_shared_state_count": 0,
        "parent_join_combinations": 0,
        "parent_interface_candidates_skipped_after_coverage": 0,
        "parent_longitudinal_checks": 0,
    }
    default_join = _unit_options(
        (0, 1),
        {0: (record(0, seed_left),), 1: (record(1, seed_right),)},
        option=None,
        engine=None,
        limit=10,
        audit=dict(audit),
    )
    research_join = _unit_options(
        (0, 1),
        {
            0: (record(0, actual_signature),),
            1: (record(1, actual_signature_other_seed),),
        },
        option=None,
        engine=None,
        limit=10,
        audit=dict(audit),
    )
    assert default_join == ()
    assert len(research_join) == 1


def test_coverage_parent_join_orders_records_by_actual_nominal_strength(monkeypatch):
    from src.design.v3 import _SpanRecord

    def record(index, top_count, top_diameter, *, width_mm=400.0):
        section = BeamSection(
            f"PB{index}", width_mm, 800.0, 40.0, 30.0, 420.0, 420.0, 20.0, 6000.0,
            detailing_profile=BeamDetailingProfile.SMF,
            beam_role="PB",
        )
        top = LongitudinalDesign(
            rows=(PhysicalRow(top_count, top_diameter),),
            effective_depth_mm=700.0,
        )
        bottom = LongitudinalDesign(
            rows=(PhysicalRow(2, 20),),
            effective_depth_mm=700.0,
        )
        regions = tuple(
            RegionDetailing(
                name,
                SimpleNamespace(name=name),
                0.0,
                0.0,
                0.0,
                StirrupDesign(10, 100, 2, 0.0, 0.0, 100.0, "fixture"),
                top,
                bottom,
            )
            for name in ("left", "middle", "right")
        )
        bottom_signature = tuple(
            (row.count, row.diameter_mm, row.role) for row in bottom.rows
        )
        hoop_signature = ((10, 2, 100),) * 3
        return _SpanRecord(
            index=index,
            input_data=SimpleNamespace(section=section),
            regions=regions,
            face_rebar=None,
            left_support=((2, 20),),
            right_support=((2, 20),),
            pb_row_diameters=(20,),
            bottom_signature=bottom_signature,
            hoop_signature=hoop_signature,
            seed_hoop_signature=hoop_signature,
            preference_tier=(0, 0, 0, 0),
            heuristic=(index, top_count, top_diameter),
        )

    audit = {
        "parent_shared_state_count": 0,
        "parent_join_combinations": 0,
        "parent_interface_candidates_skipped_after_coverage": 0,
        "parent_longitudinal_checks": 0,
    }
    records = {
        0: (record(0, 8, 25), record(0, 2, 12)),
        1: (record(1, 2, 12), record(1, 8, 25)),
    }
    assert records[0][0].bottom_signature == records[1][0].bottom_signature
    assert records[0][0].hoop_signature == records[1][0].hoop_signature
    assert records[0][0].input_data.section.is_smf is True
    legacy = _unit_options(
        (0, 1), records, option=None, engine=None, limit=2, audit=dict(audit)
    )
    coverage_audit = {}
    coverage_join_audit = dict(audit)
    covered = _unit_options(
        (0, 1),
        records,
        option=None,
        engine=None,
        limit=2,
        audit=coverage_join_audit,
        coverage_schedule=True,
        coverage_audit=coverage_audit,
    )
    assert legacy == ()
    assert len(covered) == 1
    assert coverage_audit["parent_longitudinal_check_passes"] == 1

    from src.design import constructability

    near_boundary = 4.000000008
    monkeypatch.setattr(
        constructability,
        "_nominal_moment",
        lambda _tension, _compression, width, *_args: (
            1.0 if width == 400.0 else near_boundary
        ),
    )
    boundary_audit = {}
    boundary_options = _unit_options(
        (0, 1),
        {
            0: (record(0, 2, 12, width_mm=400.0),),
            1: (record(1, 2, 12, width_mm=500.0),),
        },
        option=None,
        engine=None,
        limit=2,
        audit=dict(audit),
        coverage_schedule=True,
        coverage_audit=boundary_audit,
    )
    assert 1.0 / near_boundary == pytest.approx(0.25 - 5e-10)
    assert len(boundary_options) == 1
    assert boundary_audit["parent_longitudinal_check_passes"] == 1


def test_terminal_parent_proof_marks_complete_ordinary_parent_verified():
    results = [
        _parent_proof_result(
            f"SB{index}", start=(index - 1) * 6000,
            profile=BeamDetailingProfile.NON_SWAY, role="SB",
        )
        for index in (1, 2)
    ]
    audit = {}

    _validate_final_line(
        results,
        (SimpleNamespace(index=0), SimpleNamespace(index=1)),
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        ((0, 1),),
        audit=audit,
    )

    proof = audit["parent_final_validation"][0]
    assert all(result.coordination_status == "PARENT_COORDINATION_VERIFIED" for result in results)
    assert all(result.coordination_audit[-1] == proof for result in results)
    assert proof["status"] == "PASS"
    assert proof["checks"]["parent_bottom_uniformity"] == "PASS"
    assert proof["checks"]["parent_hoop_uniformity"] == "PASS"
    assert proof["checks"]["parent_interface_hoop_grid"]["status"] == "NOT_APPLICABLE"
    assert proof["checks"]["parent_longitudinal_strength"]["status"] == "NOT_APPLICABLE"


def test_terminal_smf_parent_proof_records_strength_checks_and_external_scope():
    results = [
        _parent_proof_result(
            f"PB{index}", start=(index - 1) * 6000,
            profile=BeamDetailingProfile.SMF, role="PB",
        )
        for index in (1, 2)
    ]
    audit = {}

    _validate_final_line(
        results,
        (SimpleNamespace(index=0), SimpleNamespace(index=1)),
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        ((0, 1),),
        external_support_faces_confirmed=True,
        audit=audit,
    )

    proof = audit["parent_final_validation"][0]
    assert all(result.coordination_status == "PARENT_COORDINATION_VERIFIED" for result in results)
    interface = proof["checks"]["parent_interface_hoop_grid"]
    assert interface["status"] == "EXTERNAL_SCOPE"
    assert interface["workflow_status"] == "EXTERNALLY_CONFIRMED"
    assert interface["support_face_coordinates_synthesized"] is False
    strength = proof["checks"]["parent_longitudinal_strength"]
    assert strength["status"] == "PASS"
    assert strength["checks"]
    assert all(check["status"] == "PASS" for check in strength["checks"])
    assert all(result.smf_checks for result in results)


def test_terminal_parent_proof_never_marks_missing_or_failed_groups_verified():
    first = _parent_proof_result(
        "SB1", start=0.0, profile=BeamDetailingProfile.NON_SWAY, role="SB",
    )
    second = _parent_proof_result(
        "SB2", start=6000.0, profile=BeamDetailingProfile.NON_SWAY, role="SB",
    )
    incomplete_audit = {}
    incomplete = [first, None]
    _validate_final_line(
        incomplete,
        (SimpleNamespace(index=0), SimpleNamespace(index=1)),
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        ((0, 1),),
        audit=incomplete_audit,
    )
    assert first.coordination_status == "V3_RECOMMENDATION_SET_ONLY"
    assert "parent_final_validation" not in incomplete_audit
    assert incomplete_audit["parent_final_validation_not_verified"][0]["status"] == (
        "NOT_VERIFIED_INCOMPLETE_PARENT_GROUP"
    )

    missing_context = replace(
        second,
        input_data=SimpleNamespace(parent_context=None, left_support=None, right_support=None),
    )
    context_audit = {}
    context_results = [first, missing_context]
    _validate_final_line(
        context_results,
        (SimpleNamespace(index=0), SimpleNamespace(index=1)),
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        ((0, 1),),
        audit=context_audit,
    )
    assert all(result.coordination_status == "V3_RECOMMENDATION_SET_ONLY" for result in context_results)
    assert context_audit["parent_final_validation_not_verified"][0]["status"] == (
        "UNVERIFIED_PARENT_CONTEXT_MISSING"
    )

    mismatched = replace(
        second,
        regions=tuple(
            replace(region, stirrup=replace(region.stirrup, legs=6))
            for region in second.regions
        ),
    )
    with pytest.raises(DetailingError, match="V3_PARENT_COMMON_BOTTOM_OR_HOOP_CHECK_FAILED"):
        _validate_final_line(
            [first, mismatched],
            (SimpleNamespace(index=0), SimpleNamespace(index=1)),
            ContinuityOption.OPTION_1_STRICT_EQUAL,
            ((0, 1),),
            audit={},
        )
    assert first.coordination_status == mismatched.coordination_status == "V3_RECOMMENDATION_SET_ONLY"

    pb_first = _parent_proof_result(
        "PB1", start=0.0, profile=BeamDetailingProfile.NON_SWAY, role="PB",
    )
    pb_second = _parent_proof_result(
        "PB2", start=6000.0, profile=BeamDetailingProfile.NON_SWAY, role="PB",
    )
    pb_second = replace(
        pb_second,
        regions=tuple(
            replace(
                region,
                top=LongitudinalDesign(rows=(PhysicalRow(2, 25),), effective_depth_mm=700.0),
            )
            for region in pb_second.regions
        ),
    )
    pb_audit = {}
    with pytest.raises(DetailingError, match="V3_PB_TOP_ROW_DIAMETER_VECTOR_CHANGED"):
        _validate_final_line(
            [pb_first, pb_second],
            (SimpleNamespace(index=0), SimpleNamespace(index=1)),
            ContinuityOption.OPTION_1_STRICT_EQUAL,
            ((0, 1),),
            audit=pb_audit,
        )
    assert "parent_final_validation" not in pb_audit
    assert pb_first.coordination_status == pb_second.coordination_status == "V3_RECOMMENDATION_SET_ONLY"


def test_external_support_scope_keeps_numeric_parent_results_and_audits_unknown_grid(monkeypatch):
    from src.design import constructability, row_solver
    from src.design.detailing import PhysicalRow

    monkeypatch.setattr(row_solver, "_parent_candidate_blockers", lambda _result: {"SMF_SUPPORT_FACE_UNRESOLVED"})
    monkeypatch.setattr(constructability, "ParentLongitudinalRun", lambda *args, **kwargs: None)
    monkeypatch.setattr(constructability, "parent_longitudinal_checks", lambda **kwargs: ())

    @dataclass
    class Result:
        beam_name: str
        section: object
        input_data: object
        constructability: object
        regions: tuple
        smf_checks: tuple = ()

    top = SimpleNamespace(rows=(PhysicalRow(2, 20),))
    bottom = SimpleNamespace(rows=(PhysicalRow(2, 20),))
    regions = tuple(
        SimpleNamespace(
            name=name,
            top=top,
            bottom=bottom,
            stirrup=SimpleNamespace(diameter_mm=10, legs=4, spacing_mm=100),
        )
        for name in ("left", "middle", "right")
    )
    results = [
        Result(
            beam_name=f"PB{index}",
            section=SimpleNamespace(is_smf=True, is_secondary=False, width_mm=300, fc_mpa=30, fy_mpa=420),
            input_data=SimpleNamespace(
                left_support=None,
                right_support=None,
                parent_context=SimpleNamespace(segment_start_mm=index * 600),
            ),
            constructability=SimpleNamespace(hoop_positions=(), zones=()),
            regions=regions,
        )
        for index in range(2)
    ]
    audit = {"external_support_scope_checks": []}

    _validate_final_line(
        results,
        (SimpleNamespace(index=0), SimpleNamespace(index=1)),
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        ((0, 1),),
        external_support_faces_confirmed=True,
        audit=audit,
    )

    assert audit["external_support_scope_checks"] == [{
        "parent_group_indices": [0, 1],
        "left_beam_name": "PB0",
        "right_beam_name": "PB1",
        "boundary_mm": 600,
        "check": "PARENT_INTERFACE_HOOP_GRID",
        "status": "EXTERNAL_SCOPE",
        "workflow_status": "EXTERNALLY_CONFIRMED",
        "support_face_coordinates_synthesized": False,
        "note": "The external support-face workflow is confirmed; this engine has no coordinates to check this interface hoop grid.",
    }]


@pytest.mark.parametrize(
    "missing_support,missing_parent_context",
    [(False, False), (True, True)],
)
def test_external_support_scope_does_not_skip_other_parent_grid_failures(
    monkeypatch, missing_support, missing_parent_context,
):
    from src.design import row_solver
    from src.design.detailing import PhysicalRow

    monkeypatch.setattr(
        row_solver,
        "_parent_interface_hoops_valid",
        lambda _results, **_kwargs: False,
    )
    top = SimpleNamespace(rows=(PhysicalRow(2, 20),))
    bottom = SimpleNamespace(rows=(PhysicalRow(2, 20),))
    regions = tuple(
        SimpleNamespace(
            top=top,
            bottom=bottom,
            stirrup=SimpleNamespace(diameter_mm=10, legs=4, spacing_mm=100),
        )
        for _ in range(3)
    )
    support = None if missing_support else SimpleNamespace(resolved=True)
    parent_context = None if missing_parent_context else SimpleNamespace(segment_start_mm=0)
    results = [
        SimpleNamespace(
            section=SimpleNamespace(is_smf=True, is_secondary=False),
            input_data=SimpleNamespace(
                left_support=support,
                right_support=support,
                parent_context=parent_context,
            ),
            constructability=SimpleNamespace(),
            regions=regions,
            smf_checks=(),
        )
        for _ in range(2)
    ]

    with pytest.raises(DetailingError, match="PARENT_INTERFACE_HOOP_SPACING_FAILED"):
        _validate_final_line(
            results,
            (SimpleNamespace(index=0), SimpleNamespace(index=1)),
            ContinuityOption.OPTION_1_STRICT_EQUAL,
            ((0, 1),),
            external_support_faces_confirmed=True,
            audit={},
        )
