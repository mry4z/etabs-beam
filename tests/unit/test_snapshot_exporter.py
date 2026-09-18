from pathlib import Path
from types import SimpleNamespace

import pytest
import json

from src.config import load_config
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignStation
from src.model_source import SqliteReadSource
from src.snapshot.exporter import SnapshotProfile, export_snapshot_bundle
from src.snapshot.sqlite_snapshot import OapiTable, SnapshotTableError, SqliteSnapshotSource
from src.snapshot.workflow import run_snapshot_workflow


def _bundle_inputs():
    points = OapiTable(
        "Point Object Connectivity",
        "Point Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.POINT_FIELDS,
        (
            ("P0", "No", "T1", "F1", "0", "No", "0", "0", "0", "g-p0"),
            ("P1", "No", "T1", "F1", "1", "No", "0", "0", "3000", "g-p1"),
            ("P2", "No", "T1", "F1", "2", "No", "6000", "0", "3000", "g-p2"),
        ),
    )
    beams = OapiTable(
        "Beam Object Connectivity",
        "Beam Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.BEAM_FIELDS,
        (("B1", "F1", "B1", "P1", "P2", "6000", "g-b1"),),
    )
    columns = OapiTable(
        "Column Object Connectivity",
        "Column Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.COLUMN_FIELDS,
        (("C1", "F1", "C1", "P0", "P1", "3000", "g-c1"),),
    )
    assignments = OapiTable(
        "Frame Assignments - Section Properties",
        "Frame Assignments - Section Properties",
        2,
        1,
        SqliteSnapshotSource.ASSIGNMENT_FIELDS,
        (
            ("F1", "B1", "B1", "Concrete Rectangular", "N.A.", "B300X600"),
            ("F1", "C1", "C1", "Concrete Rectangular", "N.A.", "C400X400"),
        ),
    )
    rectangular = OapiTable(
        "Frame Section Property Definitions - Concrete Rectangular",
        "Frame Section Property Definitions - Concrete Rectangular",
        2,
        1,
        SqliteSnapshotSource.RECTANGULAR_SECTION_FIELDS,
        (
            (
                "B300X600",
                "C30",
                "No",
                "600",
                "300",
                "No",
                "Auto",
                "1",
                "Beam",
                "1",
                "1",
                "1",
                "1",
                "1",
                "1",
                "1",
                "1",
                "Red",
                "g-section",
                "None",
            ),
        ),
    )
    overwrites = OapiTable(
        "Concrete Beam Overwrites - ACI 318-14",
        "Concrete Beam Overwrites - ACI 318-14",
        2,
        1,
        SqliteSnapshotSource.BEAM_OVERWRITE_FIELDS,
        (("F1", "B1", "B1", "Beam", "B300X600", "Sway Special", "0", "0", "0", "0", "No"),),
    )
    beam_reinforcing = OapiTable(
        "Frame Section Property Definitions - Concrete Beam Reinforcing",
        "Frame Section Property Definitions - Concrete Beam Reinforcing",
        2,
        1,
        SqliteSnapshotSource.BEAM_REINFORCING_FIELDS,
        (("B300X600", "R60", "R60", "40", "45", "0", "0", "0", "0"),),
    )
    concrete = OapiTable(
        "Material Properties - Concrete Data",
        "Material Properties - Concrete Data",
        2,
        1,
        SqliteSnapshotSource.CONCRETE_MATERIAL_FIELDS,
        (("C30", "30", "No", "No", "Mander", "Concrete", "0.002", "0.005", "-0.1", "0", "0"),),
    )
    rebar = OapiTable(
        "Material Properties - Rebar Data",
        "Material Properties - Rebar Data",
        2,
        1,
        SqliteSnapshotSource.REBAR_MATERIAL_FIELDS,
        (("R60", "420", "550", "455", "605", "Simple", "Kinematic", "0.01", "0.1", "-0.1"),),
    )
    raw_tables = (
        points,
        beams,
        columns,
        assignments,
        rectangular,
        overwrites,
        beam_reinforcing,
        concrete,
        rebar,
    )
    live_beams = (
        BeamGeometry(
            "B1",
            "B1",
            "F1",
            "P1",
            "P2",
            (0.0, 0.0, 3000.004),
            (6000.004, 0.0, 3000.004),
            "B300X600",
            600.0,
        ),
    )
    station = DesignStation(
        0.0,
        "U1",
        100.123456789,
        "U2",
        110.987654321,
        "U3",
        0.5,
        "U4",
        20.0,
        "U5",
        0.1,
        source_object="B1",
        result_source="GetSummaryResultsBeam_2",
    )
    live_designs = {
        "B1": BeamDesignResult(
            "B1",
            (station,),
            source_objects=("B1",),
            result_source="GetSummaryResultsBeam_2",
        )
    }
    profile = SnapshotProfile("test", "1.0", tuple(table.key for table in raw_tables))
    return (
        raw_tables,
        live_beams,
        {"P0": (0.0, 0.0, 0.0), "P1": (0.0, 0.0, 3000.004)},
        live_designs,
        profile,
    )


def _export(tmp_path: Path, *, live_beams=None):
    source_model = tmp_path / "working.EDB"
    api_dll = tmp_path / "ETABSv1.dll"
    source_model.write_bytes(b"model")
    api_dll.write_bytes(b"api")
    raw_tables, expected_beams, supports, designs, profile = _bundle_inputs()
    return export_snapshot_bundle(
        destination=tmp_path / "domain.sqlite",
        source_model_path=source_model,
        api_dll_path=api_dll,
        etabs_version="22.7.0",
        api_version="2.10.0.0",
        design_code="ACI 318-14",
        raw_tables=raw_tables,
        live_beams=live_beams or expected_beams,
        live_supports=supports,
        live_designs=designs,
        profile=profile,
        coordinate_tolerance_mm=0.01,
    )


def test_export_snapshot_bundle_writes_validated_non_overwriting_manifest(tmp_path) -> None:
    summary = _export(tmp_path)

    assert summary["status"] == "OK"
    assert summary["validation"]["geometry"]["equivalent"] is True
    assert summary["validation"]["design_results"]["exact_round_trip"] is True
    assert (tmp_path / "domain.sqlite").is_file()
    assert (tmp_path / "domain.sqlite.manifest.json").is_file()

    with pytest.raises(FileExistsError):
        _export(tmp_path)


def test_export_snapshot_bundle_removes_failed_replay(tmp_path) -> None:
    _, beams, _, _, _ = _bundle_inputs()
    bad_beam = BeamGeometry(
        **{
            **beams[0].__dict__,
            "end": (7000.0, 0.0, 3000.0),
        }
    )

    with pytest.raises(SnapshotTableError, match="SNAPSHOT_COORDINATE_MISMATCH"):
        _export(tmp_path, live_beams=(bad_beam,))

    assert not (tmp_path / "domain.sqlite").exists()
    assert not (tmp_path / "domain.sqlite.manifest.json").exists()


def test_export_snapshot_bundle_rejects_source_change_during_capture(tmp_path) -> None:
    source_model = tmp_path / "working.EDB"
    api_dll = tmp_path / "ETABSv1.dll"
    source_model.write_bytes(b"model")
    api_dll.write_bytes(b"api")
    raw_tables, beams, supports, designs, profile = _bundle_inputs()

    with pytest.raises(SnapshotTableError, match="SNAPSHOT_SOURCE_CHANGED_DURING_CAPTURE"):
        export_snapshot_bundle(
            destination=tmp_path / "domain.sqlite",
            source_model_path=source_model,
            api_dll_path=api_dll,
            etabs_version="22.7.0",
            api_version="2.10.0.0",
            design_code="ACI 318-14",
            raw_tables=raw_tables,
            live_beams=beams,
            live_supports=supports,
            live_designs=designs,
            profile=profile,
            coordinate_tolerance_mm=0.01,
            expected_source_sha256="0" * 64,
        )

    assert not (tmp_path / "domain.sqlite").exists()


def test_sqlite_read_source_exposes_complete_normalized_contract(tmp_path) -> None:
    _export(tmp_path)
    source = SqliteReadSource(tmp_path / "domain.sqlite", tmp_path / "working.EDB")
    beams = source.extract_beams()

    assert source.assert_current()
    assert [beam.name for beam in beams] == ["B1"]
    assert beams[0].start == (0.0, 0.0, 3000.004)
    assert beams[0].end == (6000.004, 0.0, 3000.004)
    assert set(source.column_supports()) == {"P0", "P1"}
    assert source.column_supports()["P1"] == (0.0, 0.0, 3000.004)
    assert source.frame_states(beams)["B1"].guid == "g-b1"
    assert source.designs(beams)["B1"].stations[0].top_flexural_area == 100.123456789
    assert source.frame_types() == {"B1": "Sway Special"}
    assert source.section_info("B300X600")["fy_mpa"] == 420.0
    assert source.design_code() == "ACI 318-14"
    assert source.identity()["kind"] == "sqlite-snapshot"


def test_snapshot_workflow_runs_topology_and_excel_without_etabs(tmp_path) -> None:
    _export(tmp_path)
    output = tmp_path / "offline-run"
    args = SimpleNamespace(
        snapshot_input=tmp_path / "domain.sqlite",
        expected_model=tmp_path / "working.EDB",
        pid=None,
        selection="all",
        writeback=False,
        prepare_section_review_groups=False,
        select_section_review_group=None,
        run_analysis=False,
        run_design=False,
        verify_full_writeback=False,
        verify_demand_equivalence=False,
        confirmed_multi_section_spans=None,
        exclude_writeback_objects=None,
        export_excel=True,
        export_rebar_workbook=False,
        export_beam_result_dxf=True,
        dxf_text_height=25,
        artifact_node="node",
        artifact_node_modules=None,
        artifact_node_max_old_space_mb=1024,
    )

    progress_events = []
    summary = run_snapshot_workflow(
        args,
        load_config(),
        output,
        progress=lambda percent, message: progress_events.append((percent, message)),
    )

    assert summary["mode"] == "read-only-snapshot"
    assert summary["data_source"]["kind"] == "sqlite-snapshot"
    assert summary["beams"] == 1
    assert summary["spans"] == 1
    assert Path(summary["outputs"]["spans_csv"]).is_file()
    assert Path(summary["outputs"]["excel"]).is_file()
    assert len(summary["outputs"]["beam_result_dxf"]) == 1
    assert len(summary["outputs"]["beam_section_dxf"]) == 1
    assert Path(summary["outputs"]["beam_result_dxf"][0]).is_file()
    assert Path(summary["outputs"]["beam_section_dxf"][0]).is_file()
    assert progress_events[0][0] == 0
    assert progress_events[-1][0] == 86
    assert [percent for percent, _ in progress_events] == sorted(percent for percent, _ in progress_events)


def test_snapshot_workflow_builds_rebar_payload_from_same_domain_contract(
    tmp_path,
    monkeypatch,
) -> None:
    _export(tmp_path)
    output = tmp_path / "offline-rebar"

    def fake_builder(_payload, workbook, *, preview_dir, **_kwargs):
        Path(workbook).write_bytes(b"workbook")
        Path(preview_dir).mkdir(parents=True)
        return Path(workbook)

    monkeypatch.setattr("src.snapshot.workflow.run_workbook_builder", fake_builder)
    monkeypatch.setattr("src.snapshot.workflow.run_secondary_workbook_builder", fake_builder)
    args = SimpleNamespace(
        snapshot_input=tmp_path / "domain.sqlite",
        expected_model=tmp_path / "working.EDB",
        pid=None,
        selection="all",
        writeback=False,
        prepare_section_review_groups=False,
        select_section_review_group=None,
        run_analysis=False,
        run_design=False,
        verify_full_writeback=False,
        verify_demand_equivalence=False,
        confirmed_multi_section_spans=None,
        exclude_writeback_objects=None,
        export_excel=False,
        export_rebar_workbook=True,
        artifact_node="node",
        artifact_node_modules=None,
        artifact_node_max_old_space_mb=1024,
    )

    summary = run_snapshot_workflow(args, load_config(), output)
    payload = json.loads(Path(summary["outputs"]["rebar_payload_json"]).read_text(encoding="utf-8"))

    assert payload["metadata"]["design_code"] == "ACI 318-14"
    assert payload["metadata"]["frame_objects"] == 1
    assert payload["raw_rows"][0]["fc_mpa"] == 30.0
    assert payload["raw_rows"][0]["fy_mpa"] == 420.0
    assert payload["raw_rows"][0]["frame_type"] == "Sway Special"
