import sqlite3
import hashlib
from types import SimpleNamespace

import pytest

from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.snapshot.sqlite_snapshot import (
    FIELD_MAPPING_VERSION,
    PROJECT_BEAM_DESIGN_MAPPING_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    OapiTable,
    PublicOapiTableSource,
    SnapshotMetadata,
    SnapshotReader,
    SnapshotSchemaError,
    SnapshotStaleError,
    SnapshotTableError,
    SqliteDesignProvider,
    SqliteSnapshotSource,
    build_project_beam_geometry_table,
    build_project_design_table,
    write_snapshot,
)


class FakeDatabaseTables:
    def __init__(self) -> None:
        self.tables = {
            "Beam Object Connectivity": (
                7,
                ("UniqueName", "Story", "PointI", "PointJ"),
                (
                    ("B2", "F1", "P2", "P3"),
                    ("B1", "F1", "P1", "P2"),
                ),
            ),
            "Story Definitions": (
                2,
                ("Name", "Height"),
                (("F1", "3000"),),
            ),
        }

    def GetAvailableTables(self, *_args):
        keys = tuple(self.tables)
        return (
            0,
            len(keys),
            keys,
            keys,
            tuple(2 for _ in keys),
        )

    def GetTableForDisplayArray(self, table_key, *_args):
        version, fields, rows = self.tables[table_key]
        return (
            0,
            None,
            version,
            fields,
            len(rows),
            tuple(value for row in rows for value in row),
        )


def _metadata(**overrides) -> SnapshotMetadata:
    values = {
        "source_model_path": "working-copy.EDB",
        "source_model_sha256": "model-sha",
        "etabs_version": "22.7.0",
        "api_version": "2.10.0.0",
        "api_sha256": "api-sha",
        "units": "N_mm_C",
        "design_code": "ACI 318-14",
        "created_at_utc": "2026-07-30T00:00:00+00:00",
    }
    values.update(overrides)
    return SnapshotMetadata(
        **values,
    )


def test_public_oapi_source_lists_and_reads_exact_rows() -> None:
    source = PublicOapiTableSource(SimpleNamespace(DatabaseTables=FakeDatabaseTables()))

    assert tuple(table.key for table in source.available_tables()) == (
        "Beam Object Connectivity",
        "Story Definitions",
    )
    table = source.read_table("Beam Object Connectivity")

    assert table.version == 7
    assert table.fields == ("UniqueName", "Story", "PointI", "PointJ")
    assert table.rows[0] == ("B2", "F1", "P2", "P3")


def test_public_oapi_source_rejects_missing_table_and_bad_row_width() -> None:
    database = FakeDatabaseTables()
    source = PublicOapiTableSource(SimpleNamespace(DatabaseTables=database))
    with pytest.raises(SnapshotTableError, match="SNAPSHOT_TABLE_NOT_AVAILABLE"):
        source.read_table("Missing")

    database.tables["Bad"] = (1, ("A", "B"), (("only-one",),))
    source = PublicOapiTableSource(SimpleNamespace(DatabaseTables=database))
    with pytest.raises(SnapshotTableError, match="数据宽度异常"):
        source.read_table("Bad")


def test_snapshot_round_trip_preserves_table_order_fields_and_rows(tmp_path) -> None:
    source = PublicOapiTableSource(SimpleNamespace(DatabaseTables=FakeDatabaseTables()))
    tables = source.read_tables(("Story Definitions", "Beam Object Connectivity"))
    destination = tmp_path / "snapshot.sqlite"

    write_snapshot(destination, _metadata(), tables)
    reader = SnapshotReader(destination)

    assert reader.metadata()["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert reader.metadata()["field_mapping_version"] == FIELD_MAPPING_VERSION
    assert reader.table_keys() == (
        "Story Definitions",
        "Beam Object Connectivity",
    )
    assert (
        reader.read_table(
            "Beam Object Connectivity",
            expected_fields=("UniqueName", "Story", "PointI", "PointJ"),
        )
        == tables[1]
    )
    assert not (tmp_path / "snapshot.sqlite.partial").exists()


def test_snapshot_rejects_overwrite_edb_extension_and_duplicate_tables(tmp_path) -> None:
    table = OapiTable("T", "T", 0, 1, ("A",), (("1",),))
    destination = write_snapshot(tmp_path / "snapshot.sqlite", _metadata(), (table,))

    with pytest.raises(FileExistsError):
        write_snapshot(destination, _metadata(), (table,))
    with pytest.raises(SnapshotSchemaError, match=".EDB"):
        write_snapshot(tmp_path / "not-a-model.EDB", _metadata(), (table,))
    with pytest.raises(SnapshotTableError, match="不能重复"):
        write_snapshot(tmp_path / "duplicate.sqlite", _metadata(), (table, table))


def test_reader_fails_closed_for_missing_table_and_field_drift(tmp_path) -> None:
    table = OapiTable("T", "T", 0, 1, ("A", "B"), (("1", "2"),))
    destination = write_snapshot(tmp_path / "snapshot.sqlite", _metadata(), (table,))
    reader = SnapshotReader(destination)

    with pytest.raises(SnapshotTableError, match="SNAPSHOT_TABLE_MISSING"):
        reader.read_table("Missing")
    with pytest.raises(SnapshotTableError, match="SNAPSHOT_FIELD_DRIFT"):
        reader.read_table("T", expected_fields=("B", "A"))


def test_reader_rejects_unknown_snapshot_schema(tmp_path) -> None:
    table = OapiTable("T", "T", 0, 1, ("A",), (("1",),))
    destination = write_snapshot(tmp_path / "snapshot.sqlite", _metadata(), (table,))
    with sqlite3.connect(destination) as connection:
        connection.execute(
            "UPDATE snapshot_metadata SET value_json = ? WHERE key = ?",
            ('"9.0"', "snapshot_schema_version"),
        )

    with pytest.raises(SnapshotSchemaError, match="UNSUPPORTED_SNAPSHOT_SCHEMA"):
        SnapshotReader(destination).metadata()


def test_sqlite_source_reconstructs_only_supported_concrete_beams(tmp_path) -> None:
    points = OapiTable(
        "Point Object Connectivity",
        "Point Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.POINT_FIELDS,
        (
            ("P1", "No", "T1", "F1", "1", "No", "0", "0", "3000", "g-p1"),
            ("P2", "No", "T1", "F1", "2", "No", "6000", "0", "3000", "g-p2"),
            ("P3", "No", "T1", "F1", "3", "No", "9000", "0", "3000", "g-p3"),
        ),
    )
    beams = OapiTable(
        "Beam Object Connectivity",
        "Beam Object Connectivity",
        2,
        2,
        SqliteSnapshotSource.BEAM_FIELDS,
        (
            ("B1", "F1", "B1", "P1", "P2", "6000", "g-b1"),
            ("S1", "F1", "S1", "P2", "P3", "3000", "g-s1"),
        ),
    )
    assignments = OapiTable(
        "Frame Assignments - Section Properties",
        "Frame Assignments - Section Properties",
        2,
        1,
        SqliteSnapshotSource.ASSIGNMENT_FIELDS,
        (
            ("F1", "B1", "B1", "Concrete Rectangular", "N.A.", "B300X600"),
            ("F1", "S1", "S1", "Steel I/Wide Flange", "N.A.", "W14"),
        ),
    )
    rectangular = OapiTable(
        "Frame Section Property Definitions - Concrete Rectangular",
        "Frame Section Property Definitions - Concrete Rectangular",
        2,
        2,
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
    destination = write_snapshot(
        tmp_path / "domain.sqlite",
        _metadata(),
        (
            points,
            beams,
            assignments,
            rectangular,
            beam_reinforcing,
            concrete,
            rebar,
        ),
    )

    source = SqliteSnapshotSource(SnapshotReader(destination))
    result = source.extract_beams()

    assert len(result) == 1
    assert result[0].name == "B1"
    assert result[0].start == (0.0, 0.0, 3000.0)
    assert result[0].end == (6000.0, 0.0, 3000.0)
    assert result[0].section == "B300X600"
    assert result[0].depth == 600.0
    assert source.frame_states(result)["B1"].guid == "g-b1"
    assert source.section_info("B300X600") == {
        "section": "B300X600",
        "width_mm": 300.0,
        "depth_mm": 600.0,
        "etabs_top_longitudinal_cover_mm": 40.0,
        "etabs_bottom_longitudinal_cover_mm": 45.0,
        "etabs_longitudinal_cover_mm": 45.0,
        "etabs_longitudinal_cover_source": (
            "ETABS PropFrame.GetRebarBeam(B300X600) CoverTop/CoverBot; "
            "concrete face to longitudinal-reinforcement centroid; "
            "governing maximum used"
        ),
        "fc_mpa": 30.0,
        "fy_mpa": 420.0,
        "fyt_mpa": 420.0,
    }


def test_project_geometry_table_preserves_public_oapi_sequence() -> None:
    first = BeamGeometry(
        "B2",
        "B2",
        "F1",
        "P2",
        "P3",
        (1000.004, 0.0, 3000.0),
        (2000.004, 0.0, 3000.0),
        "B300X600",
        600.0,
    )
    second = BeamGeometry(
        "B1",
        "B1",
        "F1",
        "P1",
        "P2",
        (0.004, 0.0, 3000.0),
        (1000.004, 0.0, 3000.0),
        "B300X600",
        600.0,
    )

    table = build_project_beam_geometry_table((first, second))

    assert [row[0] for row in table.rows] == ["B2", "B1"]
    assert float(table.rows[0][5]) == first.start[0]


def test_project_design_table_round_trips_exact_normalized_results(tmp_path) -> None:
    station = DesignStation(
        location=123.45678901234567,
        top_flexural_combo="U1",
        top_flexural_area=452.593123456789,
        bottom_flexural_combo="U2",
        bottom_flexural_area=492.025987654321,
        shear_combo="U3",
        shear_area=0.437654321987,
        torsion_longitudinal_combo="U4",
        torsion_longitudinal_area=807.211612345678,
        torsion_trans_combo="U5",
        torsion_trans_area=0.125678901234,
        warning="review",
        top_total_area=452.593123456789,
        bottom_total_area=492.025987654321,
        top_min_area=56.1849,
        bottom_min_area=138.864,
        top_provided_area=None,
        bottom_provided_area=502.654824574367,
        raw_top_provided_area=-1.0,
        raw_bottom_provided_area=502.654824574367,
        source_object="B1",
        units="N_mm_C",
        result_source="GetSummaryResultsBeam_2",
    )
    expected = {
        "B1": BeamDesignResult(
            "B1",
            (station,),
            DesignResultStatus.AVAILABLE,
            "WARNING: review",
            ("B1",),
            "N_mm_C",
            "GetSummaryResultsBeam_2",
        ),
        "B2": BeamDesignResult(
            "B2",
            status=DesignResultStatus.API_ERROR,
            status_message="read failed",
            source_objects=("B2",),
            units="N_mm_C",
            result_source="GetSummaryResultsBeam_2",
        ),
    }
    destination = write_snapshot(
        tmp_path / "design.sqlite",
        _metadata(domain_mapping_version=PROJECT_BEAM_DESIGN_MAPPING_VERSION),
        (build_project_design_table(expected),),
    )
    provider = SqliteDesignProvider(SnapshotReader(destination))

    assert provider.beam_names() == ("B1", "B2")
    assert provider.get_beam_design("B1") == expected["B1"]
    assert provider.get_beam_design("B2") == expected["B2"]
    assert provider.get_beam_design("missing").status is DesignResultStatus.NO_RESULTS


def test_reader_rejects_stale_source_and_changed_snapshot(tmp_path) -> None:
    source = tmp_path / "working.EDB"
    source.write_bytes(b"model-v1")
    source_hash = hashlib.sha256(b"model-v1").hexdigest()
    destination = write_snapshot(
        tmp_path / "snapshot.sqlite",
        _metadata(source_model_path=str(source), source_model_sha256=source_hash),
        (OapiTable("T", "T", 0, 1, ("A",), (("1",),)),),
    )
    reader = SnapshotReader(destination)

    assert reader.assert_source_current(source) == source_hash
    source.write_bytes(b"model-v2")
    with pytest.raises(SnapshotStaleError, match="SNAPSHOT_STALE_SOURCE_MODEL"):
        reader.assert_source_current(source)

    reader.metadata()
    destination.write_bytes(destination.read_bytes() + b"x")
    with pytest.raises(SnapshotSchemaError, match="SNAPSHOT_FILE_CHANGED"):
        reader.table_keys()


def test_sqlite_source_reconstructs_anchored_rigid_supports_and_frame_types(tmp_path) -> None:
    coordinates = {
        "P0": (0, 0, 0),
        "P1": (0, 0, 3000),
        "P2": (1000, 0, 3000),
        "P3": (2000, 0, 3000),
        "P4": (3000, 0, 3000),
        "P5": (4000, 0, 3000),
    }
    points = OapiTable(
        "Point Object Connectivity",
        "Point Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.POINT_FIELDS,
        tuple(
            (
                name,
                "No",
                "T1",
                "F1",
                str(index),
                "No",
                str(coordinates[name][0]),
                str(coordinates[name][1]),
                str(coordinates[name][2]),
                f"g-{name}",
            )
            for index, name in enumerate(("P0", "P1", "P2", "P3", "P4", "P5"))
        ),
    )
    beams = OapiTable(
        "Beam Object Connectivity",
        "Beam Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.BEAM_FIELDS,
        (
            ("R1", "F1", "R1", "P1", "P2", "1000", "g-r1"),
            ("U1", "F1", "U1", "P4", "P5", "1000", "g-u1"),
        ),
    )
    columns = OapiTable(
        "Column Object Connectivity",
        "Column Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.COLUMN_FIELDS,
        (("C1", "F1", "C1", "P0", "P1", "1000", "g-c1"),),
    )
    braces = OapiTable(
        "Brace Object Connectivity",
        "Brace Object Connectivity",
        2,
        1,
        SqliteSnapshotSource.BRACE_FIELDS,
        (("R2", "F1", "R2", "P2", "P3", "1000", "g-r2"),),
    )
    assignments = OapiTable(
        "Frame Assignments - Section Properties",
        "Frame Assignments - Section Properties",
        2,
        1,
        SqliteSnapshotSource.ASSIGNMENT_FIELDS,
        (
            ("F1", "R1", "R1", "Concrete Rectangular", "N.A.", "Rigid"),
            ("F1", "U1", "U1", "Concrete Rectangular", "N.A.", "Rigid"),
            ("F1", "C1", "C1", "Concrete Rectangular", "N.A.", "C400"),
            ("F1", "R2", "R2", "Concrete Rectangular", "N.A.", "Rigid"),
        ),
    )
    overwrites = OapiTable(
        "Concrete Beam Overwrites - ACI 318-14",
        "Concrete Beam Overwrites - ACI 318-14",
        2,
        1,
        SqliteSnapshotSource.BEAM_OVERWRITE_FIELDS,
        (
            ("F1", "B1", "B1", "Beam", "B300X600", "Sway Special", "0", "0", "0", "0", "No"),
            ("F1", "B2", "B2", "Beam", "B300X600", "Program Determined", "0", "0", "0", "0", "No"),
        ),
    )
    destination = write_snapshot(
        tmp_path / "supports.sqlite",
        _metadata(),
        (points, beams, columns, braces, assignments, overwrites),
    )
    source = SqliteSnapshotSource(SnapshotReader(destination))

    assert set(source.column_supports()) == {"P1", "P2", "P3"}
    assert source.column_supports()["P3"] == (2000.0, 0.0, 3000.0)
    assert source.raw_table_column_anchor_names() == {"P1"}
    selected = BeamGeometry("SELECTED", "SELECTED", "F1", "P3", "P4", (2000, 0, 3000), (3000, 0, 3000), "B", 600)
    assert {link.name for link in source.rigid_links_for_beams([selected])} == {"R1", "R2"}
    with pytest.raises(SnapshotTableError, match="SNAPSHOT_COLUMN_OUTLINES_UNAVAILABLE"):
        source.column_outlines()
    assert source.frame_types() == {"B1": "Sway Special", "B2": "Program Determined"}
