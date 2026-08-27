from __future__ import annotations

from src.core.tower_assignment import (
    BLOCKED,
    PASS,
    REVIEW_REQUIRED,
    TowerAssignmentChecker,
    TowerAssignmentReport,
    TowerGroupPlan,
    TowerObjectRecord,
    build_tower_assignment_report,
    compact_tower_assignment_summary,
    report_group_summary,
)


def _table(fields, rows):
    return type("Table", (), {"fields": tuple(fields), "rows": tuple(tuple(row) for row in rows)})()


def _base_tables(tower_i="T1", tower_j="T1"):
    story = _table(("Tower", "Story", "Height"), [("T1", "L1", "3000"), ("T2", "L1", "3000")])
    points = _table(
        ("UniqueName", "Tower", "Story", "X", "Y", "Z", "GUID"),
        [("P1", tower_i, "L1", "0", "0", "0", "GP1"), ("P2", tower_j, "L1", "5000", "0", "0", "GP2")],
    )
    beams = _table(
        ("UniqueName", "Story", "UniquePtI", "UniquePtJ", "GUID"),
        [("B1", "L1", "P1", "P2", "GB1")],
    )
    columns = _table(
        ("UniqueName", "Story", "UniquePtI", "UniquePtJ", "GUID"),
        [("C1", "L1", "P1", "P1A", "GC1")],
    )
    return story, points, beams, columns


def test_unique_story_tower_passes():
    story = _table(("Tower", "Story", "Height"), [("T1", "L1", "3000")])
    points = _table(
        ("UniqueName", "Tower", "Story", "X", "Y", "Z", "GUID"),
        [("P1", "T1", "L1", "0", "0", "0", "GP1"), ("P2", "T1", "L1", "5000", "0", "0", "GP2")],
    )
    beams = _table(("UniqueName", "Story", "UniquePtI", "UniquePtJ", "GUID"), [("B1", "L1", "P1", "P2", "GB1")])
    report = build_tower_assignment_report(story_rows=story, point_rows=points, beam_rows=beams)
    assert report.status == PASS
    assert report.issue_count == 0
    assert report.groups == ()


def test_mixed_endpoint_assignment_is_grouped_without_reassignment():
    story, points, beams, columns = _base_tables("T1", "T2")
    report = build_tower_assignment_report(
        story_rows=story, point_rows=points, beam_rows=beams, column_rows=columns
    )
    assert report.status == REVIEW_REQUIRED
    assert any(record.status == "TOWER_MIXED_ENDPOINT_ASSIGNMENT" for record in report.records)
    assert any(plan.group_name == "CODX_TOWER_CHECK_UNRESOLVED" for plan in report.groups)
    assert report_group_summary(report)[0]["object_count"] >= 1


def test_selected_scope_keeps_topology_context_but_only_checks_selected_beam():
    story = _table(("Tower", "Story", "Height"), [("T1", "L1", "3000"), ("T2", "L1", "3000")])
    points = _table(
        ("UniqueName", "Tower", "Story", "X", "Y", "Z", "GUID"),
        [
            ("P1", "T1", "L1", "0", "0", "0", "GP1"),
            ("P2", "T1", "L1", "5000", "0", "0", "GP2"),
            ("P3", "T2", "L1", "10000", "0", "0", "GP3"),
        ],
    )
    beams = _table(
        ("UniqueName", "Story", "UniquePtI", "UniquePtJ", "GUID"),
        [("B1", "L1", "P1", "P2", "GB1"), ("B2", "L1", "P2", "P3", "GB2")],
    )
    report = build_tower_assignment_report(
        story_rows=story,
        point_rows=points,
        beam_rows=beams,
        selection="selected",
        selected_beams=("B1",),
    )
    assert report.checked_beams == 1
    assert {record.name for record in report.records if record.object_type == "Beam"} == {"B1"}


def test_missing_required_table_is_blocked():
    report = build_tower_assignment_report(story_rows=(), point_rows=(), beam_rows=())
    assert report.status == BLOCKED
    assert any("TOWER_TABLE_MISSING" in item for item in report.diagnostics)


def test_point_elevation_can_disambiguate_same_story_towers():
    story = _table(
        ("Tower", "Story", "Elevation"),
        [("T1", "L1", "3000"), ("T2", "L1", "6000")],
    )
    points = _table(
        ("UniqueName", "Tower", "Story", "Z", "GUID"),
        [("P1", "T1", "L1", "3000", "GP1"), ("P2", "T1", "L1", "3000", "GP2")],
    )
    beams = _table(
        ("UniqueName", "Story", "UniquePtI", "UniquePtJ", "GUID"),
        [("B1", "L1", "P1", "P2", "GB1")],
    )
    report = build_tower_assignment_report(story_rows=story, point_rows=points, beam_rows=beams)
    assert report.status == PASS
    assert report.records[0].candidate_towers == ("T1",)


def test_named_checker_facade_reuses_catalog_and_identity():
    story, points, beams, columns = _base_tables("T1", "T1")
    checker = TowerAssignmentChecker(model_identity={"model": "demo"})
    report = checker.check(
        story_rows=story,
        point_rows=points,
        beam_rows=beams,
        column_rows=columns,
        selection="all",
    )
    assert report.status == PASS
    assert report.model_identity == {"model": "demo"}


def test_compact_summary_keeps_counts_names_and_groups_without_records():
    report = TowerAssignmentReport(
        status=REVIEW_REQUIRED,
        checked_beams=300,
        checked_points=600,
        issue_count=11,
        records=(
            *(
                TowerObjectRecord(object_type="Beam", name=f"B{i:03d}", status=PASS)
                for i in range(1, 290)
            ),
            TowerObjectRecord(object_type="Beam", name="B290", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B291", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B292", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B293", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B294", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B295", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B296", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B297", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B298", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B299", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord(object_type="Beam", name="B300", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
        ),
        groups=(
            TowerGroupPlan(
                group_name="CODX_TOWER_CHECK_EXPECTED_T1",
                expected_tower="T1",
                frame_names=tuple(f"B{i:03d}" for i in range(290, 301)),
            ),
        ),
    )

    summary = compact_tower_assignment_summary(
        report,
        created_group_names=("CODX_TOWER_CHECK_EXPECTED_T1",),
    )

    assert summary["checked_beams"] == 300
    assert summary["passed_beams"] == 289
    assert summary["review_beams"] == 11
    assert summary["review_beam_names"] == [f"B{i:03d}" for i in range(290, 301)]
    assert summary["created_group_names"] == ["CODX_TOWER_CHECK_EXPECTED_T1"]
    assert "records" not in summary
    assert "289/300" in summary["message"]
