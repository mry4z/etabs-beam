"""Project-owned SQLite snapshots of public ETABS DatabaseTables output.

This module stores raw public-OAPI tables plus explicitly versioned
project-normalized domain tables.  It never reads or writes EDB internals.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from src.core.etabs_connector import EtabsApiError, api_values
from src.core.geometry_extractor import BeamGeometry, expand_rigid_support_points
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.utils.geometry import Point3D

SNAPSHOT_SCHEMA_VERSION = "1.0"
FIELD_MAPPING_VERSION = "raw-oapi-table/1.0"
PROJECT_BEAM_DESIGN_TABLE_KEY = "__project__.BeamDesignResult.v1"
PROJECT_DOMAIN_MAPPING_VERSION = "beam-domain/2.0"
# Compatibility alias for callers that previously imported the design-only
# mapping constant.  Version 2.0 covers exact geometry, supports and design.
PROJECT_BEAM_DESIGN_MAPPING_VERSION = PROJECT_DOMAIN_MAPPING_VERSION
PROJECT_BEAM_GEOMETRY_TABLE_KEY = "__project__.BeamGeometry.v1"
PROJECT_COLUMN_SUPPORT_TABLE_KEY = "__project__.ColumnSupport.v1"
PROJECT_BEAM_GEOMETRY_FIELDS = (
    "Name",
    "Label",
    "Story",
    "PointI",
    "PointJ",
    "StartX",
    "StartY",
    "StartZ",
    "EndX",
    "EndY",
    "EndZ",
    "Section",
    "Depth",
)
PROJECT_COLUMN_SUPPORT_FIELDS = (
    "PointName",
    "X",
    "Y",
    "Z",
)
PROJECT_BEAM_DESIGN_FIELDS = (
    "BeamName",
    "ResultStatus",
    "StatusMessage",
    "SourceObjectsJson",
    "ResultUnits",
    "ResultSource",
    "StationIndex",
    "Location",
    "TopFlexuralCombo",
    "TopFlexuralArea",
    "BottomFlexuralCombo",
    "BottomFlexuralArea",
    "ShearCombo",
    "ShearArea",
    "TorsionLongitudinalCombo",
    "TorsionLongitudinalArea",
    "TorsionTransCombo",
    "TorsionTransArea",
    "Error",
    "Warning",
    "TopTotalArea",
    "BottomTotalArea",
    "TopMinArea",
    "BottomMinArea",
    "TopProvidedArea",
    "BottomProvidedArea",
    "RawTopProvidedArea",
    "RawBottomProvidedArea",
    "SourceObject",
    "StationUnits",
    "StationResultSource",
)


class SnapshotError(RuntimeError):
    """Base exception for snapshot export and replay failures."""


class SnapshotSchemaError(SnapshotError):
    """Raised when the SQLite container does not match the supported schema."""


class SnapshotTableError(SnapshotError):
    """Raised when an OAPI table or its field contract is invalid."""


class SnapshotStaleError(SnapshotError):
    """Raised when a snapshot no longer matches its source model."""


@dataclass(frozen=True)
class SnapshotMetadata:
    source_model_path: str
    source_model_sha256: str
    etabs_version: str
    api_version: str
    api_sha256: str
    units: str
    design_code: str
    export_profile: str = "custom"
    export_profile_version: str = "1.0"
    domain_mapping_version: str = ""
    created_at_utc: str = ""
    snapshot_schema_version: str = SNAPSHOT_SCHEMA_VERSION
    field_mapping_version: str = FIELD_MAPPING_VERSION

    def __post_init__(self) -> None:
        required = {
            "source_model_path": self.source_model_path,
            "source_model_sha256": self.source_model_sha256,
            "etabs_version": self.etabs_version,
            "api_version": self.api_version,
            "api_sha256": self.api_sha256,
            "units": self.units,
            "design_code": self.design_code,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise SnapshotSchemaError(f"快照 metadata 缺少必需字段：{', '.join(missing)}")
        if self.snapshot_schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotSchemaError(
                f"UNSUPPORTED_SNAPSHOT_SCHEMA: expected {SNAPSHOT_SCHEMA_VERSION}, "
                f"got {self.snapshot_schema_version}"
            )
        if self.field_mapping_version != FIELD_MAPPING_VERSION:
            raise SnapshotSchemaError(
                f"UNSUPPORTED_FIELD_MAPPING: expected {FIELD_MAPPING_VERSION}, got {self.field_mapping_version}"
            )
        if not self.created_at_utc:
            object.__setattr__(
                self,
                "created_at_utc",
                datetime.now(timezone.utc).isoformat(),
            )


@dataclass(frozen=True)
class AvailableTable:
    key: str
    name: str
    import_type: int

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.name.strip():
            raise SnapshotTableError("ETABS 可用表的 key/name 不能为空。")


@dataclass(frozen=True)
class OapiTable:
    key: str
    name: str
    import_type: int
    version: int
    fields: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.name.strip():
            raise SnapshotTableError("ETABS 表的 key/name 不能为空。")
        if not self.fields:
            raise SnapshotTableError(f"{self.key}: 字段列表为空。")
        if any(not field.strip() for field in self.fields):
            raise SnapshotTableError(f"{self.key}: 字段 key 不能为空。")
        if len(set(self.fields)) != len(self.fields):
            raise SnapshotTableError(f"{self.key}: 字段 key 重复。")
        wrong_width = [index for index, row in enumerate(self.rows) if len(row) != len(self.fields)]
        if wrong_width:
            raise SnapshotTableError(f"{self.key}: 第 {wrong_width[0]} 行字段数量不一致。")


class PublicOapiTableSource:
    """Read caller-selected tables through CSI's public DatabaseTables API."""

    def __init__(self, sap_model: Any) -> None:
        self.sap = sap_model
        self._available: dict[str, AvailableTable] | None = None

    def available_tables(self) -> tuple[AvailableTable, ...]:
        try:
            values = api_values(
                self.sap.DatabaseTables.GetAvailableTables(0, None, None, None),
                "DatabaseTables.GetAvailableTables",
            )
        except EtabsApiError as exc:
            raise SnapshotTableError(str(exc)) from exc
        if len(values) != 4:
            raise SnapshotTableError("DatabaseTables.GetAvailableTables: 返回参数数量异常。")
        count, keys, names, import_types = values
        arrays = (list(keys or ()), list(names or ()), list(import_types or ()))
        if int(count) != len(arrays[0]) or any(len(values_array) != int(count) for values_array in arrays):
            raise SnapshotTableError("DatabaseTables.GetAvailableTables: 数组长度不一致。")
        tables = tuple(AvailableTable(str(key), str(name), int(import_type)) for key, name, import_type in zip(*arrays))
        if len({table.key for table in tables}) != len(tables):
            raise SnapshotTableError("DatabaseTables.GetAvailableTables: 表 key 重复。")
        self._available = {table.key: table for table in tables}
        return tables

    def read_table(self, table_key: str) -> OapiTable:
        available = self._available
        if available is None:
            self.available_tables()
            available = self._available or {}
        table_info = available.get(table_key)
        if table_info is None:
            raise SnapshotTableError(f"SNAPSHOT_TABLE_NOT_AVAILABLE: {table_key}")
        try:
            values = api_values(
                self.sap.DatabaseTables.GetTableForDisplayArray(
                    table_key,
                    None,
                    "",
                    0,
                    None,
                    0,
                    None,
                ),
                f"DatabaseTables.GetTableForDisplayArray({table_key})",
            )
        except EtabsApiError as exc:
            raise SnapshotTableError(str(exc)) from exc
        if len(values) != 5:
            raise SnapshotTableError(f"{table_key}: GetTableForDisplayArray 返回参数数量异常。")
        _requested_fields, table_version, included_fields, record_count, flat_data = values
        fields = tuple(str(field) for field in list(included_fields or ()))
        data = tuple(str(value) for value in list(flat_data or ()))
        count = int(record_count)
        if count < 0:
            raise SnapshotTableError(f"{table_key}: 记录数不能为负数。")
        if not fields:
            raise SnapshotTableError(f"{table_key}: ETABS 未返回字段。")
        expected_values = count * len(fields)
        if len(data) != expected_values:
            raise SnapshotTableError(f"{table_key}: 数据宽度异常；expected {expected_values}, got {len(data)}")
        rows = tuple(tuple(data[offset : offset + len(fields)]) for offset in range(0, len(data), len(fields)))
        return OapiTable(
            table_info.key,
            table_info.name,
            table_info.import_type,
            int(table_version),
            fields,
            rows,
        )

    def read_tables(self, table_keys: Iterable[str]) -> tuple[OapiTable, ...]:
        keys = tuple(str(key).strip() for key in table_keys)
        if not keys or any(not key for key in keys):
            raise SnapshotTableError("至少需要一个非空 ETABS 表 key。")
        if len(set(keys)) != len(keys):
            raise SnapshotTableError("ETABS 表 key 不能重复。")
        return tuple(self.read_table(key) for key in keys)


def write_snapshot(
    destination: str | Path,
    metadata: SnapshotMetadata,
    tables: Sequence[OapiTable],
) -> Path:
    """Write a new snapshot through a verified same-directory partial file."""

    target = Path(destination).resolve()
    if target.suffix.casefold() == ".edb":
        raise SnapshotSchemaError("SQLite 快照输出不能使用 .EDB 扩展名。")
    if target.exists():
        raise FileExistsError(f"快照已存在，拒绝覆盖：{target}")
    if not tables:
        raise SnapshotTableError("快照至少需要一张表。")
    if len({table.key for table in tables}) != len(tables):
        raise SnapshotTableError("快照表 key 不能重复。")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.partial")
    if partial.exists():
        raise FileExistsError(f"partial 文件已存在，拒绝覆盖：{partial}")

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(partial)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _create_schema(connection)
        _insert_metadata(connection, metadata)
        _insert_tables(connection, tables)
        connection.commit()
        integrity = tuple(row[0] for row in connection.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise SnapshotSchemaError(f"SQLite integrity_check 失败：{integrity!r}")
        connection.close()
        connection = None
        os.rename(partial, target)
    except Exception:
        if connection is not None:
            connection.close()
        if partial.exists():
            partial.unlink()
        raise
    return target


class SnapshotReader:
    """Strict read-only access to one project-owned SQLite snapshot."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        stat = self.path.stat()
        self._file_identity = (stat.st_size, stat.st_mtime_ns)
        self._metadata_cache: dict[str, Any] | None = None
        self._table_keys_cache: tuple[str, ...] | None = None
        self._table_cache: dict[str, OapiTable] = {}

    def _connect(self) -> sqlite3.Connection:
        self._ensure_unchanged()
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        return connection

    def metadata(self) -> dict[str, Any]:
        self._ensure_unchanged()
        if self._metadata_cache is not None:
            return dict(self._metadata_cache)
        with closing(self._connect()) as connection:
            self._verify_database(connection)
            rows = connection.execute("SELECT key, value_json FROM snapshot_metadata ORDER BY key")
            metadata = {str(key): json.loads(str(value)) for key, value in rows}
        schema = str(metadata.get("snapshot_schema_version", ""))
        if schema != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotSchemaError(
                f"UNSUPPORTED_SNAPSHOT_SCHEMA: expected {SNAPSHOT_SCHEMA_VERSION}, got {schema or 'missing'}"
            )
        mapping = str(metadata.get("field_mapping_version", ""))
        if mapping != FIELD_MAPPING_VERSION:
            raise SnapshotSchemaError(
                f"UNSUPPORTED_FIELD_MAPPING: expected {FIELD_MAPPING_VERSION}, got {mapping or 'missing'}"
            )
        self._metadata_cache = metadata
        return dict(metadata)

    def table_keys(self) -> tuple[str, ...]:
        self._ensure_unchanged()
        if self._table_keys_cache is not None:
            return self._table_keys_cache
        self.metadata()
        with closing(self._connect()) as connection:
            result = tuple(
                str(row[0])
                for row in connection.execute("SELECT table_key FROM snapshot_tables ORDER BY table_ordinal")
            )
        self._table_keys_cache = result
        return result

    def has_table(self, table_key: str) -> bool:
        """Return whether an exact table key exists in the verified snapshot."""

        return str(table_key) in self.table_keys()

    def assert_source_current(self, source_model_path: str | Path) -> str:
        """Verify the current durable model content against snapshot metadata."""

        source = Path(source_model_path).resolve()
        if not source.is_file():
            raise SnapshotStaleError(f"SNAPSHOT_SOURCE_MODEL_MISSING: {source}")
        actual_sha256 = _sha256_file(source)
        expected_sha256 = str(self.metadata().get("source_model_sha256", "")).casefold()
        if not expected_sha256 or actual_sha256.casefold() != expected_sha256:
            raise SnapshotStaleError(
                "SNAPSHOT_STALE_SOURCE_MODEL: " f"expected {expected_sha256 or 'missing'}, got {actual_sha256}"
            )
        return actual_sha256

    def read_table(
        self,
        table_key: str,
        *,
        expected_fields: Sequence[str] | None = None,
    ) -> OapiTable:
        self.metadata()
        cached = self._table_cache.get(table_key)
        if cached is not None:
            if expected_fields is not None and cached.fields != tuple(expected_fields):
                raise SnapshotTableError(
                    f"SNAPSHOT_FIELD_DRIFT: {table_key}; " f"expected {tuple(expected_fields)!r}, got {cached.fields!r}"
                )
            return cached
        with closing(self._connect()) as connection:
            header = connection.execute(
                """
                SELECT table_name, import_type, table_version, field_count, row_count
                FROM snapshot_tables
                WHERE table_key = ?
                """,
                (table_key,),
            ).fetchone()
            if header is None:
                raise SnapshotTableError(f"SNAPSHOT_TABLE_MISSING: {table_key}")
            name, import_type, version, field_count, row_count = header
            fields = tuple(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT field_key
                    FROM snapshot_fields
                    WHERE table_key = ?
                    ORDER BY field_ordinal
                    """,
                    (table_key,),
                )
            )
            if len(fields) != int(field_count):
                raise SnapshotSchemaError(f"{table_key}: 快照字段计数不一致。")
            if expected_fields is not None and fields != tuple(expected_fields):
                raise SnapshotTableError(
                    f"SNAPSHOT_FIELD_DRIFT: {table_key}; expected {tuple(expected_fields)!r}, got {fields!r}"
                )
            cells = tuple(
                (int(row_index), int(field_ordinal), str(value))
                for row_index, field_ordinal, value in connection.execute(
                    """
                    SELECT row_index, field_ordinal, value
                    FROM snapshot_cells
                    WHERE table_key = ?
                    ORDER BY row_index, field_ordinal
                    """,
                    (table_key,),
                )
            )
        expected_cells = int(row_count) * len(fields)
        if len(cells) != expected_cells:
            raise SnapshotSchemaError(f"{table_key}: 快照单元格计数不一致。")
        rows: list[list[str]] = [[""] * len(fields) for _ in range(int(row_count))]
        for row_index, field_ordinal, value in cells:
            if not 0 <= row_index < len(rows) or not 0 <= field_ordinal < len(fields):
                raise SnapshotSchemaError(f"{table_key}: 快照单元格坐标越界。")
            rows[row_index][field_ordinal] = value
        table = OapiTable(
            str(table_key),
            str(name),
            int(import_type),
            int(version),
            fields,
            tuple(tuple(row) for row in rows),
        )
        self._table_cache[table_key] = table
        return table

    def _ensure_unchanged(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError as exc:
            raise SnapshotSchemaError(f"SNAPSHOT_FILE_CHANGED: {self.path} was removed") from exc
        current = (stat.st_size, stat.st_mtime_ns)
        if current != self._file_identity:
            raise SnapshotSchemaError(
                "SNAPSHOT_FILE_CHANGED: " f"expected size/mtime {self._file_identity}, got {current}"
            )

    @staticmethod
    def _verify_database(connection: sqlite3.Connection) -> None:
        integrity = tuple(row[0] for row in connection.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise SnapshotSchemaError(f"SQLite integrity_check 失败：{integrity!r}")
        required = {
            "snapshot_metadata",
            "snapshot_tables",
            "snapshot_fields",
            "snapshot_cells",
        }
        actual = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = sorted(required - actual)
        if missing:
            raise SnapshotSchemaError(f"SQLite 快照缺少表：{', '.join(missing)}")


class SqliteSnapshotSource:
    """Reconstruct the current rectangular concrete-beam geometry contract.

    The adapter is intentionally strict: field order drift and unsupported
    concrete section shapes block extraction instead of silently dropping
    domain objects.
    """

    POINT_FIELDS = (
        "UniqueName",
        "IsAuto",
        "Tower",
        "Story",
        "PointBay",
        "IsSpecial",
        "X",
        "Y",
        "Z",
        "GUID",
    )
    BEAM_FIELDS = (
        "UniqueName",
        "Story",
        "BeamBay",
        "UniquePtI",
        "UniquePtJ",
        "Length",
        "GUID",
    )
    COLUMN_FIELDS = (
        "UniqueName",
        "Story",
        "ColumnBay",
        "UniquePtI",
        "UniquePtJ",
        "Length",
        "GUID",
    )
    BRACE_FIELDS = (
        "UniqueName",
        "Story",
        "BraceBay",
        "UniquePtI",
        "UniquePtJ",
        "Length",
        "GUID",
    )
    ASSIGNMENT_FIELDS = (
        "Story",
        "Label",
        "UniqueName",
        "Shape",
        "AutoSelect",
        "SectProp",
    )
    RECTANGULAR_SECTION_FIELDS = (
        "Name",
        "Material",
        "FromFile",
        "t3",
        "t2",
        "RigidZone",
        "NotSizeType",
        "NotAutoFact",
        "DesignType",
        "AMod",
        "A2Mod",
        "A3Mod",
        "JMod",
        "I2Mod",
        "I3Mod",
        "MMod",
        "WMod",
        "Color",
        "GUID",
        "Notes",
    )
    FRAME_SUMMARY_FIELDS = (
        "Story",
        "Label",
        "UniqueName",
        "Type",
        "Length",
        "AnalysisSect",
        "DesignSect",
        "AxisAngle",
        "MaxStaSpcg",
        "MinNumSta",
        "Releases",
        "UserOffsets",
    )
    BEAM_OVERWRITE_FIELDS = (
        "Story",
        "Label",
        "UniqueName",
        "Type",
        "DesignSect",
        "FrameType",
        "LLRF",
        "LMajor",
        "LMinor",
        "Torsion",
        "IgnorePu",
    )
    BEAM_REINFORCING_FIELDS = (
        "Name",
        "RebarMatL",
        "RebarMatC",
        "TopCover",
        "BotCover",
        "TopIEndArea",
        "TopJEndArea",
        "BotIEndArea",
        "BotJEndArea",
    )
    CONCRETE_MATERIAL_FIELDS = (
        "Material",
        "Fc",
        "LtWtConc",
        "IsUserFr",
        "SSCurveOpt",
        "SSHysType",
        "SFc",
        "SCap",
        "FinalSlope",
        "FAngle",
        "DAngle",
    )
    REBAR_MATERIAL_FIELDS = (
        "Material",
        "Fy",
        "Fu",
        "Fye",
        "Fue",
        "SSCurveOpt",
        "SSHysType",
        "SHard",
        "SCap",
        "FinalSlope",
    )

    def __init__(self, reader: SnapshotReader) -> None:
        self.reader = reader

    def extract_beams(self) -> list[BeamGeometry]:
        metadata = self.reader.metadata()
        if metadata.get("units") != "N_mm_C":
            raise SnapshotSchemaError(f"UNSUPPORTED_SNAPSHOT_UNITS: expected N_mm_C, got {metadata.get('units')!r}")
        if self.reader.has_table(PROJECT_BEAM_GEOMETRY_TABLE_KEY):
            return self._extract_project_beams()
        return self.extract_raw_table_beams()

    def extract_raw_table_beams(self) -> list[BeamGeometry]:
        """Rebuild display-table geometry for export-time provenance checks."""

        metadata = self.reader.metadata()
        if metadata.get("units") != "N_mm_C":
            raise SnapshotSchemaError(f"UNSUPPORTED_SNAPSHOT_UNITS: expected N_mm_C, got {metadata.get('units')!r}")
        points = _indexed_rows(
            self.reader.read_table(
                "Point Object Connectivity",
                expected_fields=self.POINT_FIELDS,
            ),
            "UniqueName",
        )
        assignments = _indexed_rows(
            self.reader.read_table(
                "Frame Assignments - Section Properties",
                expected_fields=self.ASSIGNMENT_FIELDS,
            ),
            "UniqueName",
        )
        sections = _indexed_rows(
            self.reader.read_table(
                "Frame Section Property Definitions - Concrete Rectangular",
                expected_fields=self.RECTANGULAR_SECTION_FIELDS,
            ),
            "Name",
        )
        beams = self.reader.read_table(
            "Beam Object Connectivity",
            expected_fields=self.BEAM_FIELDS,
        )
        design_names = self._project_design_names()

        output: list[BeamGeometry] = []
        for row in _row_mappings(beams):
            name = row["UniqueName"]
            assignment = assignments.get(name)
            if assignment is None:
                raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: frame assignment for {name}")
            shape = assignment["Shape"]
            if shape != "Concrete Rectangular":
                if "concrete" in shape.casefold() or name in design_names:
                    raise SnapshotTableError(f"UNSUPPORTED_CONCRETE_SECTION_SHAPE: {name} uses {shape}")
                continue
            section_name = assignment["SectProp"]
            section = sections.get(section_name)
            if section is None:
                raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: rectangular section {section_name} for {name}")
            point_i_name = row["UniquePtI"]
            point_j_name = row["UniquePtJ"]
            point_i = points.get(point_i_name)
            point_j = points.get(point_j_name)
            if point_i is None or point_j is None:
                missing = point_i_name if point_i is None else point_j_name
                raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: point {missing} for {name}")
            if row["Story"] != assignment["Story"]:
                raise SnapshotTableError(
                    f"SNAPSHOT_STORY_MISMATCH: {name}; "
                    f"connectivity={row['Story']}, assignment={assignment['Story']}"
                )
            try:
                start = (
                    float(point_i["X"]),
                    float(point_i["Y"]),
                    float(point_i["Z"]),
                )
                end = (
                    float(point_j["X"]),
                    float(point_j["Y"]),
                    float(point_j["Z"]),
                )
                depth = float(section["t3"])
            except ValueError as exc:
                raise SnapshotTableError(f"SNAPSHOT_NUMERIC_VALUE_INVALID: {name}") from exc
            output.append(
                BeamGeometry(
                    name=name,
                    label=assignment["Label"],
                    story=row["Story"],
                    point_i=point_i_name,
                    point_j=point_j_name,
                    start=start,
                    end=end,
                    section=section_name,
                    depth=depth,
                )
            )
        if not output:
            raise SnapshotTableError("SNAPSHOT_NO_CONCRETE_BEAMS")
        return output

    def _extract_project_beams(self) -> list[BeamGeometry]:
        """Replay full-precision geometry captured from public GetAllFrames."""

        table = self.reader.read_table(
            PROJECT_BEAM_GEOMETRY_TABLE_KEY,
            expected_fields=PROJECT_BEAM_GEOMETRY_FIELDS,
        )
        output: list[BeamGeometry] = []
        seen: set[str] = set()
        for row in _row_mappings(table):
            name = row["Name"]
            if not name or name in seen:
                raise SnapshotTableError(f"SNAPSHOT_DUPLICATE_KEY: {PROJECT_BEAM_GEOMETRY_TABLE_KEY}.Name={name}")
            seen.add(name)
            try:
                output.append(
                    BeamGeometry(
                        name=name,
                        label=row["Label"],
                        story=row["Story"],
                        point_i=row["PointI"],
                        point_j=row["PointJ"],
                        start=(
                            float(row["StartX"]),
                            float(row["StartY"]),
                            float(row["StartZ"]),
                        ),
                        end=(
                            float(row["EndX"]),
                            float(row["EndY"]),
                            float(row["EndZ"]),
                        ),
                        section=row["Section"],
                        depth=_optional_float(row["Depth"]),
                    )
                )
            except ValueError as exc:
                raise SnapshotTableError(f"SNAPSHOT_NUMERIC_VALUE_INVALID: project geometry {name}") from exc
        if not output:
            raise SnapshotTableError("SNAPSHOT_NO_CONCRETE_BEAMS")
        return output

    def column_supports(self) -> dict[str, Point3D]:
        """Rebuild real and rigid-transferred column support coordinates."""

        metadata = self.reader.metadata()
        if metadata.get("units") != "N_mm_C":
            raise SnapshotSchemaError(f"UNSUPPORTED_SNAPSHOT_UNITS: expected N_mm_C, got {metadata.get('units')!r}")
        if self.reader.has_table(PROJECT_COLUMN_SUPPORT_TABLE_KEY):
            return self._extract_project_supports()
        return self.raw_table_column_supports()

    def raw_table_column_supports(self) -> dict[str, Point3D]:
        """Rebuild display-table supports for export-time provenance checks."""

        metadata = self.reader.metadata()
        if metadata.get("units") != "N_mm_C":
            raise SnapshotSchemaError(f"UNSUPPORTED_SNAPSHOT_UNITS: expected N_mm_C, got {metadata.get('units')!r}")
        points = _indexed_rows(
            self.reader.read_table(
                "Point Object Connectivity",
                expected_fields=self.POINT_FIELDS,
            ),
            "UniqueName",
        )
        assignments = _indexed_rows(
            self.reader.read_table(
                "Frame Assignments - Section Properties",
                expected_fields=self.ASSIGNMENT_FIELDS,
            ),
            "UniqueName",
        )
        columns = self.reader.read_table(
            "Column Object Connectivity",
            expected_fields=self.COLUMN_FIELDS,
        )
        connectivity_tables = [
            self.reader.read_table(
                "Beam Object Connectivity",
                expected_fields=self.BEAM_FIELDS,
            ),
            columns,
        ]
        if self.reader.has_table("Brace Object Connectivity"):
            connectivity_tables.append(
                self.reader.read_table(
                    "Brace Object Connectivity",
                    expected_fields=self.BRACE_FIELDS,
                )
            )

        column_points: set[str] = set()
        for row in _row_mappings(columns):
            column_points.update((row["UniquePtI"], row["UniquePtJ"]))
        rigid_links: list[tuple[str, str]] = []
        for table in connectivity_tables:
            for row in _row_mappings(table):
                assignment = assignments.get(row["UniqueName"])
                if assignment is None:
                    raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: frame assignment for {row['UniqueName']}")
                if "rigid" in assignment["SectProp"].casefold():
                    rigid_links.append((row["UniquePtI"], row["UniquePtJ"]))
        support_names = expand_rigid_support_points(column_points, rigid_links)
        missing = sorted(name for name in support_names if name not in points)
        if missing:
            raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: support point {missing[0]}")
        result: dict[str, Point3D] = {}
        for name in sorted(support_names):
            point = points[name]
            try:
                result[name] = (
                    float(point["X"]),
                    float(point["Y"]),
                    float(point["Z"]),
                )
            except ValueError as exc:
                raise SnapshotTableError(f"SNAPSHOT_NUMERIC_VALUE_INVALID: support point {name}") from exc
        return result

    def _extract_project_supports(self) -> dict[str, Point3D]:
        """Replay full-precision support coordinates captured from GetAllFrames."""

        table = self.reader.read_table(
            PROJECT_COLUMN_SUPPORT_TABLE_KEY,
            expected_fields=PROJECT_COLUMN_SUPPORT_FIELDS,
        )
        result: dict[str, Point3D] = {}
        for row in _row_mappings(table):
            name = row["PointName"]
            if not name or name in result:
                raise SnapshotTableError(f"SNAPSHOT_DUPLICATE_KEY: {PROJECT_COLUMN_SUPPORT_TABLE_KEY}.PointName={name}")
            try:
                result[name] = (
                    float(row["X"]),
                    float(row["Y"]),
                    float(row["Z"]),
                )
            except ValueError as exc:
                raise SnapshotTableError(f"SNAPSHOT_NUMERIC_VALUE_INVALID: project support {name}") from exc
        if not result:
            raise SnapshotTableError("SNAPSHOT_NO_COLUMN_SUPPORTS")
        return result

    def frame_types(self) -> dict[str, str]:
        """Return exact concrete-beam FrameType assignments by unique name."""

        table = self.reader.read_table(
            "Concrete Beam Overwrites - ACI 318-14",
            expected_fields=self.BEAM_OVERWRITE_FIELDS,
        )
        return {name: row["FrameType"] for name, row in _indexed_rows(table, "UniqueName").items()}

    def frame_states(self, beams: Sequence[BeamGeometry]) -> dict[str, Any]:
        """Return normalized GUID/connectivity state for offline planning."""

        from src.core.model_writeback import FrameState

        connectivity = _indexed_rows(
            self.reader.read_table(
                "Beam Object Connectivity",
                expected_fields=self.BEAM_FIELDS,
            ),
            "UniqueName",
        )
        assignments = _indexed_rows(
            self.reader.read_table(
                "Frame Assignments - Section Properties",
                expected_fields=self.ASSIGNMENT_FIELDS,
            ),
            "UniqueName",
        )
        result: dict[str, FrameState] = {}
        for beam in beams:
            row = connectivity.get(beam.name)
            assignment = assignments.get(beam.name)
            if row is None or assignment is None:
                raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: frame state for {beam.name}")
            if (
                row["UniquePtI"] != beam.point_i
                or row["UniquePtJ"] != beam.point_j
                or assignment["Label"] != beam.label
                or assignment["Story"] != beam.story
                or assignment["SectProp"] != beam.section
            ):
                raise SnapshotTableError(f"SNAPSHOT_FRAME_STATE_MISMATCH: {beam.name}")
            guid = row["GUID"].strip()
            if not guid:
                raise SnapshotTableError(f"SNAPSHOT_FRAME_GUID_MISSING: {beam.name}")
            result[beam.name] = FrameState(
                beam.name,
                guid,
                beam.point_i,
                beam.point_j,
                beam.label,
                beam.story,
                beam.section,
            )
        return result

    def section_info(self, section_name: str) -> dict[str, Any]:
        """Rebuild the workbook's rectangular concrete/rebar section contract."""

        sections = _indexed_rows(
            self.reader.read_table(
                "Frame Section Property Definitions - Concrete Rectangular",
                expected_fields=self.RECTANGULAR_SECTION_FIELDS,
            ),
            "Name",
        )
        reinforcing = _indexed_rows(
            self.reader.read_table(
                "Frame Section Property Definitions - Concrete Beam Reinforcing",
                expected_fields=self.BEAM_REINFORCING_FIELDS,
            ),
            "Name",
        )
        concrete_materials = _indexed_rows(
            self.reader.read_table(
                "Material Properties - Concrete Data",
                expected_fields=self.CONCRETE_MATERIAL_FIELDS,
            ),
            "Material",
        )
        rebar_materials = _indexed_rows(
            self.reader.read_table(
                "Material Properties - Rebar Data",
                expected_fields=self.REBAR_MATERIAL_FIELDS,
            ),
            "Material",
        )
        section = sections.get(str(section_name))
        rebar = reinforcing.get(str(section_name))
        if section is None or rebar is None:
            raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: section/reinforcing {section_name}")
        concrete = concrete_materials.get(section["Material"])
        longitudinal = rebar_materials.get(rebar["RebarMatL"])
        transverse = rebar_materials.get(rebar["RebarMatC"])
        if concrete is None or longitudinal is None or transverse is None:
            raise SnapshotTableError(f"SNAPSHOT_JOIN_MISSING: section material for {section_name}")
        try:
            return {
                "section": str(section_name),
                "width_mm": float(section["t2"]),
                "depth_mm": float(section["t3"]),
                "etabs_top_longitudinal_cover_mm": float(rebar["TopCover"]),
                "etabs_bottom_longitudinal_cover_mm": float(rebar["BotCover"]),
                "etabs_longitudinal_cover_mm": max(
                    float(rebar["TopCover"]),
                    float(rebar["BotCover"]),
                ),
                "etabs_longitudinal_cover_source": (
                    f"ETABS PropFrame.GetRebarBeam({section_name}) CoverTop/CoverBot; "
                    "concrete face to longitudinal-reinforcement centroid; "
                    "governing maximum used"
                ),
                "fc_mpa": float(concrete["Fc"]),
                "fy_mpa": float(longitudinal["Fy"]),
                "fyt_mpa": float(transverse["Fy"]),
            }
        except ValueError as exc:
            raise SnapshotTableError(f"SNAPSHOT_NUMERIC_VALUE_INVALID: section {section_name}") from exc

    def _project_design_names(self) -> set[str]:
        if not self.reader.has_table(PROJECT_BEAM_DESIGN_TABLE_KEY):
            return set()
        table = self.reader.read_table(
            PROJECT_BEAM_DESIGN_TABLE_KEY,
            expected_fields=PROJECT_BEAM_DESIGN_FIELDS,
        )
        return {row["BeamName"] for row in _row_mappings(table)}


class SqliteDesignProvider:
    """Replay exact project-normalized beam design results from a snapshot."""

    def __init__(self, reader: SnapshotReader) -> None:
        self.reader = reader
        self._results: dict[str, BeamDesignResult] | None = None

    def get_beam_design(self, beam_name: str) -> BeamDesignResult:
        results = self._load_results()
        result = results.get(str(beam_name))
        if result is not None:
            return result
        metadata = self.reader.metadata()
        return BeamDesignResult(
            str(beam_name),
            status=DesignResultStatus.NO_RESULTS,
            status_message="SQLite 快照没有该梁的归一化设计结果。",
            units=str(metadata.get("units", "N_mm_C")),
            result_source=PROJECT_BEAM_DESIGN_TABLE_KEY,
        )

    def beam_names(self) -> tuple[str, ...]:
        """Return every beam represented by the normalized design table."""

        return tuple(sorted(self._load_results()))

    def _load_results(self) -> dict[str, BeamDesignResult]:
        if self._results is not None:
            return self._results
        metadata = self.reader.metadata()
        mapping = str(metadata.get("domain_mapping_version", ""))
        if mapping != PROJECT_BEAM_DESIGN_MAPPING_VERSION:
            raise SnapshotSchemaError(
                "UNSUPPORTED_DOMAIN_MAPPING: "
                f"expected {PROJECT_BEAM_DESIGN_MAPPING_VERSION}, got {mapping or 'missing'}"
            )
        table = self.reader.read_table(
            PROJECT_BEAM_DESIGN_TABLE_KEY,
            expected_fields=PROJECT_BEAM_DESIGN_FIELDS,
        )
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in _row_mappings(table):
            grouped.setdefault(row["BeamName"], []).append(row)
        results: dict[str, BeamDesignResult] = {}
        for beam_name, rows in grouped.items():
            results[beam_name] = _design_result_from_rows(beam_name, rows)
        if not results:
            raise SnapshotTableError("SNAPSHOT_NO_BEAM_DESIGN_RESULTS")
        self._results = results
        return results


def build_project_beam_geometry_table(
    beams: Sequence[BeamGeometry],
) -> OapiTable:
    """Serialize public-OAPI beam geometry with binary-float round-trip precision."""

    if not beams:
        raise SnapshotTableError("归一化梁几何不能为空。")
    names = [beam.name for beam in beams]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise SnapshotTableError("归一化梁几何名称必须非空且唯一。")
    rows = tuple(
        (
            beam.name,
            beam.label,
            beam.story,
            beam.point_i,
            beam.point_j,
            *(_float_text(value) for value in beam.start),
            *(_float_text(value) for value in beam.end),
            beam.section,
            _optional_float_text(beam.depth),
        )
        # Preserve the public bulk OAPI sequence.  Topology IDs are
        # deterministic, but artifact row order is also part of parity with a
        # same-model live run.
        for beam in beams
    )
    return OapiTable(
        PROJECT_BEAM_GEOMETRY_TABLE_KEY,
        "Project Normalized Beam Geometry",
        -1,
        1,
        PROJECT_BEAM_GEOMETRY_FIELDS,
        rows,
    )


def build_project_column_support_table(
    supports: Mapping[str, Point3D],
) -> OapiTable:
    """Serialize exact column and rigid-transferred support coordinates."""

    if not supports or any(not str(name) for name in supports):
        raise SnapshotTableError("归一化柱支座必须非空且名称有效。")
    rows = tuple(
        (
            str(name),
            *(_float_text(value) for value in supports[name]),
        )
        for name in sorted(supports)
    )
    return OapiTable(
        PROJECT_COLUMN_SUPPORT_TABLE_KEY,
        "Project Normalized Column Supports",
        -1,
        1,
        PROJECT_COLUMN_SUPPORT_FIELDS,
        rows,
    )


def build_project_design_table(
    designs: Mapping[str, BeamDesignResult],
) -> OapiTable:
    """Serialize normalized public-OAPI design results without precision loss."""

    if not designs:
        raise SnapshotTableError("归一化设计结果不能为空。")
    rows: list[tuple[str, ...]] = []
    for beam_name in sorted(designs):
        result = designs[beam_name]
        if result.beam_name != beam_name:
            raise SnapshotTableError(f"DESIGN_RESULT_NAME_MISMATCH: key={beam_name}, result={result.beam_name}")
        common = (
            beam_name,
            result.status.name,
            result.status_message,
            json.dumps(result.source_objects, ensure_ascii=False, separators=(",", ":")),
            result.units,
            result.result_source,
        )
        if not result.stations:
            rows.append((*common, "-1", *([""] * (len(PROJECT_BEAM_DESIGN_FIELDS) - len(common) - 1))))
            continue
        for index, station in enumerate(result.stations):
            rows.append(
                (
                    *common,
                    str(index),
                    _float_text(station.location),
                    station.top_flexural_combo,
                    _float_text(station.top_flexural_area),
                    station.bottom_flexural_combo,
                    _float_text(station.bottom_flexural_area),
                    station.shear_combo,
                    _float_text(station.shear_area),
                    station.torsion_longitudinal_combo,
                    _float_text(station.torsion_longitudinal_area),
                    station.torsion_trans_combo,
                    _float_text(station.torsion_trans_area),
                    station.error,
                    station.warning,
                    _optional_float_text(station.top_total_area),
                    _optional_float_text(station.bottom_total_area),
                    _optional_float_text(station.top_min_area),
                    _optional_float_text(station.bottom_min_area),
                    _optional_float_text(station.top_provided_area),
                    _optional_float_text(station.bottom_provided_area),
                    _optional_float_text(station.raw_top_provided_area),
                    _optional_float_text(station.raw_bottom_provided_area),
                    station.source_object,
                    station.units,
                    station.result_source,
                )
            )
    return OapiTable(
        PROJECT_BEAM_DESIGN_TABLE_KEY,
        "Project Normalized Beam Design Results",
        -1,
        1,
        PROJECT_BEAM_DESIGN_FIELDS,
        tuple(rows),
    )


def _design_result_from_rows(
    beam_name: str,
    rows: Sequence[Mapping[str, str]],
) -> BeamDesignResult:
    first = rows[0]
    common_fields = (
        "ResultStatus",
        "StatusMessage",
        "SourceObjectsJson",
        "ResultUnits",
        "ResultSource",
    )
    for row in rows[1:]:
        if any(row[field] != first[field] for field in common_fields):
            raise SnapshotTableError(f"SNAPSHOT_DESIGN_HEADER_MISMATCH: {beam_name}")
    try:
        status = DesignResultStatus[first["ResultStatus"]]
    except KeyError as exc:
        raise SnapshotTableError(f"SNAPSHOT_DESIGN_STATUS_INVALID: {beam_name}={first['ResultStatus']}") from exc
    try:
        raw_source_objects = json.loads(first["SourceObjectsJson"])
    except json.JSONDecodeError as exc:
        raise SnapshotTableError(f"SNAPSHOT_DESIGN_SOURCE_OBJECTS_INVALID: {beam_name}") from exc
    if not isinstance(raw_source_objects, list) or not all(isinstance(item, str) for item in raw_source_objects):
        raise SnapshotTableError(f"SNAPSHOT_DESIGN_SOURCE_OBJECTS_INVALID: {beam_name}")
    indexed: list[tuple[int, Mapping[str, str]]] = []
    for row in rows:
        try:
            index = int(row["StationIndex"])
        except ValueError as exc:
            raise SnapshotTableError(f"SNAPSHOT_DESIGN_STATION_INDEX_INVALID: {beam_name}") from exc
        indexed.append((index, row))
    if len(indexed) == 1 and indexed[0][0] == -1:
        stations: tuple[DesignStation, ...] = ()
    else:
        indexed.sort(key=lambda item: item[0])
        expected_indices = list(range(len(indexed)))
        actual_indices = [item[0] for item in indexed]
        if actual_indices != expected_indices:
            raise SnapshotTableError(f"SNAPSHOT_DESIGN_STATION_SEQUENCE_INVALID: {beam_name}; got {actual_indices}")
        stations = tuple(_design_station_from_row(beam_name, row) for _, row in indexed)
    return BeamDesignResult(
        beam_name,
        stations,
        status,
        first["StatusMessage"],
        tuple(raw_source_objects),
        first["ResultUnits"],
        first["ResultSource"],
    )


def _design_station_from_row(
    beam_name: str,
    row: Mapping[str, str],
) -> DesignStation:
    try:
        return DesignStation(
            location=float(row["Location"]),
            top_flexural_combo=row["TopFlexuralCombo"],
            top_flexural_area=float(row["TopFlexuralArea"]),
            bottom_flexural_combo=row["BottomFlexuralCombo"],
            bottom_flexural_area=float(row["BottomFlexuralArea"]),
            shear_combo=row["ShearCombo"],
            shear_area=float(row["ShearArea"]),
            torsion_longitudinal_combo=row["TorsionLongitudinalCombo"],
            torsion_longitudinal_area=float(row["TorsionLongitudinalArea"]),
            torsion_trans_combo=row["TorsionTransCombo"],
            torsion_trans_area=float(row["TorsionTransArea"]),
            error=row["Error"],
            warning=row["Warning"],
            top_total_area=_optional_float(row["TopTotalArea"]),
            bottom_total_area=_optional_float(row["BottomTotalArea"]),
            top_min_area=_optional_float(row["TopMinArea"]),
            bottom_min_area=_optional_float(row["BottomMinArea"]),
            top_provided_area=_optional_float(row["TopProvidedArea"]),
            bottom_provided_area=_optional_float(row["BottomProvidedArea"]),
            raw_top_provided_area=_optional_float(row["RawTopProvidedArea"]),
            raw_bottom_provided_area=_optional_float(row["RawBottomProvidedArea"]),
            source_object=row["SourceObject"],
            units=row["StationUnits"],
            result_source=row["StationResultSource"],
        )
    except ValueError as exc:
        raise SnapshotTableError(
            f"SNAPSHOT_DESIGN_NUMERIC_VALUE_INVALID: {beam_name} station {row['StationIndex']}"
        ) from exc


def _float_text(value: float) -> str:
    return format(float(value), ".17g")


def _optional_float_text(value: float | None) -> str:
    return "" if value is None else _float_text(value)


def _optional_float(value: str) -> float | None:
    return None if value == "" else float(value)


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_mappings(table: OapiTable) -> tuple[dict[str, str], ...]:
    return tuple(dict(zip(table.fields, row)) for row in table.rows)


def _indexed_rows(
    table: OapiTable,
    key_field: str,
) -> dict[str, dict[str, str]]:
    rows = _row_mappings(table)
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row[key_field]
        if key in result:
            raise SnapshotTableError(f"SNAPSHOT_DUPLICATE_KEY: {table.key}.{key_field}={key}")
        result[key] = row
    return result


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE snapshot_metadata (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );
        CREATE TABLE snapshot_tables (
            table_key TEXT PRIMARY KEY,
            table_ordinal INTEGER NOT NULL UNIQUE,
            table_name TEXT NOT NULL,
            import_type INTEGER NOT NULL,
            table_version INTEGER NOT NULL,
            field_count INTEGER NOT NULL CHECK (field_count > 0),
            row_count INTEGER NOT NULL CHECK (row_count >= 0)
        );
        CREATE TABLE snapshot_fields (
            table_key TEXT NOT NULL,
            field_ordinal INTEGER NOT NULL CHECK (field_ordinal >= 0),
            field_key TEXT NOT NULL,
            PRIMARY KEY (table_key, field_ordinal),
            UNIQUE (table_key, field_key),
            FOREIGN KEY (table_key) REFERENCES snapshot_tables(table_key)
                ON DELETE CASCADE
        );
        CREATE TABLE snapshot_cells (
            table_key TEXT NOT NULL,
            row_index INTEGER NOT NULL CHECK (row_index >= 0),
            field_ordinal INTEGER NOT NULL CHECK (field_ordinal >= 0),
            value TEXT NOT NULL,
            PRIMARY KEY (table_key, row_index, field_ordinal),
            FOREIGN KEY (table_key, field_ordinal)
                REFERENCES snapshot_fields(table_key, field_ordinal)
                ON DELETE CASCADE
        );
        """)


def _insert_metadata(connection: sqlite3.Connection, metadata: SnapshotMetadata) -> None:
    values: Mapping[str, Any] = asdict(metadata)
    connection.executemany(
        "INSERT INTO snapshot_metadata(key, value_json) VALUES (?, ?)",
        (
            (
                key,
                json.dumps(value, ensure_ascii=False, sort_keys=True),
            )
            for key, value in sorted(values.items())
        ),
    )


def _insert_tables(connection: sqlite3.Connection, tables: Sequence[OapiTable]) -> None:
    for table_ordinal, table in enumerate(tables):
        connection.execute(
            """
            INSERT INTO snapshot_tables(
                table_key, table_ordinal, table_name, import_type,
                table_version, field_count, row_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                table.key,
                table_ordinal,
                table.name,
                table.import_type,
                table.version,
                len(table.fields),
                len(table.rows),
            ),
        )
        connection.executemany(
            """
            INSERT INTO snapshot_fields(table_key, field_ordinal, field_key)
            VALUES (?, ?, ?)
            """,
            ((table.key, field_ordinal, field_key) for field_ordinal, field_key in enumerate(table.fields)),
        )
        connection.executemany(
            """
            INSERT INTO snapshot_cells(table_key, row_index, field_ordinal, value)
            VALUES (?, ?, ?, ?)
            """,
            (
                (table.key, row_index, field_ordinal, value)
                for row_index, row in enumerate(table.rows)
                for field_ordinal, value in enumerate(row)
            ),
        )
