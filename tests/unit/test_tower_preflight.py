import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src import cli
from src.config import load_config
from src.core.etabs_tower import TowerGroupMutationResult
from src.core.tower_assignment import TowerAssignmentReport, TowerGroupPlan, TowerObjectRecord
from src.tower_preflight import TowerPreflightRequired, run_tower_preflight, validate_tower_options


class Source:
    def __init__(self, root, report=None, locked=False):
        self.report = report or TowerAssignmentReport()
        self.sap = SimpleNamespace(GetModelIsLocked=lambda: locked)
        self.durable_model_path = root / "model.EDB"
        self.calls = []

    def tower_assignment_report(self, **kwargs):
        self.calls.append(kwargs)
        return self.report

    def identity(self):
        return {"source_model_sha256": "abc"}


@pytest.mark.parametrize(
    "flags, error",
    [
        (["--tower-create-groups"], PermissionError),
        (["--allow-tower-group-mutation"], ValueError),
        (["--tower-check", "--snapshot-input", "snapshot.sqlite"], ValueError),
        (["--tower-check", "--writeback"], ValueError),
        (["--tower-preflight-only", "--run-analysis"], ValueError),
        (["--tower-check", "--run-design"], ValueError),
        (["--tower-preflight-only", "--tower-create-groups", "--allow-tower-group-mutation"], ValueError),
    ],
)
def test_unsafe_tower_options_fail_before_attachment(flags, error, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_attach", lambda *args: pytest.fail("must not attach"))
    args = cli._parser().parse_args(flags)
    with pytest.raises(error):
        cli._run_impl(args, None, tmp_path)


def test_group_mode_cannot_be_called_read_only():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["--read-only", "--tower-create-groups"])


@pytest.mark.parametrize(
    "status, confirmed, raises",
    [
        ("PASS", False, False),
        ("REVIEW_REQUIRED", False, True),
        ("REVIEW_REQUIRED", True, False),
        ("BLOCKED", True, True),
    ],
)
def test_reports_persist_and_confirmation_never_bypasses_blocking(tmp_path, status, confirmed, raises):
    args = cli._parser().parse_args(["--tower-check", "--selection", "selected"])
    args.tower_continue_on_review = confirmed
    source = Source(tmp_path, TowerAssignmentReport(status=status, issue_count=1))
    if raises:
        with pytest.raises(TowerPreflightRequired):
            run_tower_preflight(args, source, [SimpleNamespace(name="B1")], tmp_path)
    else:
        result = run_tower_preflight(args, source, [SimpleNamespace(name="B1")], tmp_path)
        assert result["tower_assignment"]["status"] == status
    assert source.calls == [{"selection": "selected", "selected_beams": ("B1",)}]
    reports = list(tmp_path.glob("tower_assignment_check_*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text(encoding="utf-8"))["status"] == status
    assert len(list(tmp_path.glob("tower_assignment_check_*.csv"))) == 1


def test_disabled_preflight_does_not_read_source(tmp_path):
    assert run_tower_preflight(cli._parser().parse_args([]), None, [], tmp_path) is None


def test_cli_display_summary_keeps_records_out_of_plugin_stdout(tmp_path):
    report = TowerAssignmentReport(
        checked_beams=1,
        issue_count=1,
        records=(TowerObjectRecord(object_type="Beam", name="B1", status="REVIEW_REQUIRED"),),
    )
    displayed = cli._display_summary(
        {
            "status": "OK",
            "outputs": {"tower_assignment_json": str(tmp_path / "tower.json")},
            "tower_assignment": report.to_dict(),
        }
    )

    assert "records" not in displayed["tower_assignment"]
    assert displayed["tower_assignment"]["review_beam_names"] == ["B1"]


def test_group_ownership_persists_across_runs_but_is_model_scoped(tmp_path, monkeypatch):
    args = cli._parser().parse_args(["--tower-create-groups", "--allow-tower-group-mutation"])
    validate_tower_options(args)
    source = Source(tmp_path)
    calls = []

    def create(sap, report, **kwargs):
        calls.append(kwargs)
        return TowerGroupMutationResult("SUCCESS")

    monkeypatch.setattr("src.tower_preflight.create_tower_review_groups", create)
    for index in range(3):
        output = tmp_path / f"run{index}"
        output.mkdir()
        if index == 2:
            source.durable_model_path = tmp_path / "other.EDB"
        result = run_tower_preflight(args, source, [], output)
        audit = json.loads(next(output.glob("tower_group_audit_*.json")).read_text(encoding="utf-8"))
        assert audit["saved"] is False and audit["user_confirmed"] is True
        assert "tower_group_result" in result
    assert calls[0]["ownership_path"] == calls[1]["ownership_path"]
    assert calls[0]["ownership_path"] != calls[2]["ownership_path"]


@pytest.mark.parametrize("reason", ["locked", "hash", "members"])
def test_group_validation_precedes_mutation(tmp_path, monkeypatch, reason):
    args = cli._parser().parse_args(["--tower-create-groups", "--allow-tower-group-mutation"])
    source = Source(
        tmp_path,
        TowerAssignmentReport(
            status="REVIEW_REQUIRED",
            model_identity={"source_model_sha256": "abc"},
            groups=(TowerGroupPlan("CHECK_T1", "T1", ("B1",)),),
        ),
        locked=reason == "locked",
    )
    plan = source.report.to_dict()
    if reason == "hash":
        plan["model_identity"]["source_model_sha256"] = "other"
    if reason == "members":
        plan["groups"] = [replace(source.report.groups[0], frame_names=("B2",)).__dict__]
    args.tower_plan = tmp_path / "plan.json"
    args.tower_plan.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(
        "src.tower_preflight.create_tower_review_groups", lambda *a, **kw: pytest.fail("must not mutate")
    )
    with pytest.raises(RuntimeError):
        run_tower_preflight(args, source, [], tmp_path)


@pytest.mark.parametrize("status", ["PASS", "BLOCKED"])
def test_cli_tower_only_returns_before_geometry_design_and_restores_units(tmp_path, monkeypatch, status):
    model = tmp_path / "model.EDB"
    model.write_bytes(b"offline fake")
    args = cli._parser().parse_args(["--pid", "123", "--tower-preflight-only", "--export-rebar-workbook"])
    config = load_config("config.example.yaml")
    unit_state = ["original"]
    sap = SimpleNamespace(
        GetModelIsLocked=lambda: False,
        GetPresentUnits=lambda: unit_state[-1],
        SetPresentUnits=lambda unit: unit_state.append(unit) or 0,
    )
    source = Source(tmp_path, TowerAssignmentReport(status=status))
    source.extract_beams = lambda selection: []
    # No column_supports/design/writeback APIs: reaching them fails the test.
    monkeypatch.setattr(cli, "_attach", lambda *a: (sap, model))
    monkeypatch.setattr(cli, "_etabs_version", lambda sap: "23")
    monkeypatch.setattr(cli, "_api_environment", lambda path: {})
    monkeypatch.setattr(cli, "EtabsOapiReadSource", lambda *a, **kw: source)
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eUnits=SimpleNamespace(N_mm_C="N_mm_C")))
    if status == "BLOCKED":
        with pytest.raises(TowerPreflightRequired):
            cli._run_impl(args, config, tmp_path)
    else:
        result = cli._run_impl(args, config, tmp_path)
        assert result["mode"] == "tower-preflight"
        assert set(result["outputs"]) == {"tower_assignment_json", "tower_assignment_csv"}
    assert unit_state == ["original", "N_mm_C", "original"]


def test_review_confirmation_does_not_cover_new_group_members(tmp_path):
    args = cli._parser().parse_args(["--tower-check", "--tower-continue-on-review"])
    source = Source(
        tmp_path,
        TowerAssignmentReport(
            status="REVIEW_REQUIRED",
            model_identity={"source_model_sha256": "abc"},
            groups=(TowerGroupPlan("CHECK_T1", "T1", ("B1",)),),
        ),
    )
    args.tower_plan = tmp_path / "review.json"
    args.tower_plan.write_text(json.dumps(source.report.to_dict()), encoding="utf-8")
    assert run_tower_preflight(args, source, [], tmp_path)
    source.report = replace(source.report, groups=(TowerGroupPlan("CHECK_T1", "T1", ("B1", "B2")),))
    with pytest.raises(RuntimeError, match="TOWER_PLAN_GROUP_SET_MISMATCH"):
        run_tower_preflight(args, source, [], tmp_path)


def test_model_source_passes_selection_and_source_identity_to_tower_adapter(tmp_path, monkeypatch):
    from src.model_source import EtabsOapiReadSource

    model = tmp_path / "model.EDB"
    model.write_bytes(b"offline fixture")
    captured = []
    result = TowerAssignmentReport()
    monkeypatch.setattr(
        "src.model_source.EtabsTowerReader.read_report", lambda self, **kwargs: captured.append(kwargs) or result
    )
    source = EtabsOapiReadSource(SimpleNamespace(), model)
    assert source.tower_assignment_report(selection="selected", selected_beams=("B1",)) is result
    assert captured[0]["selected_beams"] == ("B1",)
    assert captured[0]["model_identity"]["source_model_sha256"] == source.assert_current()
