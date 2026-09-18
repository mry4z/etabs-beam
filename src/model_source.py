"""Normalized read-model sources for live ETABS OAPI and SQLite replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from src.core.etabs_connector import api_values, model_paths_match
from src.core.etabs_tower import EtabsTowerReader
from src.core.tower_assignment import TowerAssignmentReport
from src.core.geometry_extractor import BeamGeometry, GeometryExtractor, SelectionError
from src.core.model_writeback import EtabsWriteback, FrameState
from src.design.etabs_result import EtabsResultProvider
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.rebar_workbook import _frame_types, _section_info
from src.run_manifest import sha256_file
from src.snapshot.sqlite_snapshot import (
    SnapshotReader,
    SnapshotSchemaError,
    SnapshotTableError,
    SqliteDesignProvider,
    SqliteSnapshotSource,
)
from src.utils.geometry import Point3D


def _emit_progress(
    progress: Callable[[int, str], None] | None,
    percent: int,
    message: str,
) -> None:
    if progress is not None:
        progress(max(0, min(100, int(percent))), message)


class ReadModelSource(Protocol):
    """The normalized read contract consumed by topology and workbook flows."""

    kind: str
    model_path: Path

    def assert_current(self) -> str: ...

    def extract_beams(
        self,
        selection: str = "all",
        *,
        include_design_ineligible: bool = False,
        progress: Callable[[int, str], None] | None = None,
    ) -> list[BeamGeometry]: ...

    def column_supports(self) -> dict[str, Point3D]: ...

    def frame_states(self, beams: Sequence[BeamGeometry]) -> dict[str, FrameState]: ...

    def designs(
        self,
        beams: Iterable[BeamGeometry],
        *,
        progress: Callable[[int, str], None] | None = None,
    ) -> dict[str, BeamDesignResult]: ...

    def frame_types(self) -> dict[str, str]: ...

    def section_info(self, section_name: str) -> dict[str, Any]: ...

    def design_code(self) -> str: ...

    def identity(self) -> Mapping[str, Any]: ...

    def design_object_exclusions(self) -> tuple[Mapping[str, Any], ...]: ...


class EtabsOapiReadSource:
    """Normalized, read-only facade over an attached ETABS SapModel."""

    kind = "etabs-oapi"

    def __init__(
        self,
        sap_model: Any,
        model_path: str | Path,
        *,
        durable_model_path: str | Path | None = None,
        expected_model_sha256: str | None = None,
    ) -> None:
        self.sap = sap_model
        self.model_path = Path(model_path).resolve()
        self.durable_model_path = (
            Path(durable_model_path).resolve() if durable_model_path is not None else self.model_path
        )
        self.expected_model_sha256 = expected_model_sha256
        self._geometry = GeometryExtractor(sap_model)
        self._design_provider = EtabsResultProvider(sap_model)
        self._design_cache: dict[str, BeamDesignResult] = {}
        self._design_exclusions: list[dict[str, Any]] = []

    def assert_current(self) -> str:
        if not self.durable_model_path.is_file():
            raise FileNotFoundError(self.durable_model_path)
        actual = sha256_file(self.durable_model_path)
        if self.expected_model_sha256 is not None and actual.casefold() != self.expected_model_sha256.casefold():
            raise RuntimeError("OAPI_SOURCE_MODEL_CHANGED: " f"expected {self.expected_model_sha256}, got {actual}")
        return actual

    def extract_beams(
        self,
        selection: str = "all",
        *,
        include_design_ineligible: bool = False,
        progress: Callable[[int, str], None] | None = None,
    ) -> list[BeamGeometry]:
        beams = self._geometry.extract(selection)
        results_available = getattr(self.sap.DesignConcrete, "GetResultsAvailable", None)
        if results_available is None or not bool(results_available()):
            # Without a model-level design result there is no authoritative
            # object membership set yet.  The workflow will fail closed before
            # exporting design-dependent outputs; do not silently discard all
            # geometry during a topology-only diagnostic run.
            _emit_progress(progress, 100, "读取梁几何完成（模型暂无设计结果）")
            return beams
        self._design_exclusions = []
        eligible: list[BeamGeometry] = []
        _emit_progress(progress, 0, "读取梁设计资格")
        total = len(beams)
        last_percent = -1
        for index, beam in enumerate(beams, 1):
            result = self._design_result(beam.name)
            if result.status in {DesignResultStatus.AVAILABLE, DesignResultStatus.PARTIAL}:
                eligible.append(beam)
            else:
                self._design_exclusions.append(
                    {
                        "object_name": beam.name,
                        "label": beam.label,
                        "story": beam.story,
                        "section": beam.section,
                        "status": result.status.value,
                        "reason": result.status_message or "ETABS 未返回当前对象的混凝土设计结果。",
                    }
                )
            percent = round(index * 100 / max(total, 1))
            if percent != last_percent:
                _emit_progress(progress, percent, f"读取梁设计资格 {index}/{total}")
                last_percent = percent
        if include_design_ineligible:
            _emit_progress(progress, 100, f"梁设计资格读取完成（{len(beams)} 梁）")
            return beams
        if selection == "selected" and not eligible:
            raise SelectionError("当前选择中没有参与混凝土设计的梁对象。")
        _emit_progress(progress, 100, f"梁设计资格读取完成（{len(eligible)} 梁）")
        return eligible

    def tower_assignment_report(
        self,
        *,
        selection: str = "all",
        selected_beams: Iterable[str] | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> TowerAssignmentReport:
        return EtabsTowerReader(self.sap).read_report(
            selection=selection,
            selected_beams=selected_beams,
            model_identity=self.identity(),
            progress=progress,
        )

    def column_supports(self) -> dict[str, Point3D]:
        return self._geometry.column_supports()

    def frame_states(self, beams: Sequence[BeamGeometry]) -> dict[str, FrameState]:
        return EtabsWriteback(self.sap).snapshot_from_geometry(beams)

    def designs(
        self,
        beams: Iterable[BeamGeometry],
        *,
        progress: Callable[[int, str], None] | None = None,
    ) -> dict[str, BeamDesignResult]:
        beam_list = tuple(beams)
        designs: dict[str, BeamDesignResult] = {}
        _emit_progress(progress, 0, "读取梁设计结果")
        last_percent = -1
        total = len(beam_list)
        for index, beam in enumerate(beam_list, 1):
            designs[beam.name] = self._design_result(beam.name)
            percent = round(index * 100 / max(total, 1))
            if percent != last_percent:
                _emit_progress(progress, percent, f"读取梁设计结果 {index}/{total}")
                last_percent = percent
        _emit_progress(progress, 100, f"梁设计结果读取完成（{total} 梁）")
        return designs

    def _design_result(self, name: str) -> BeamDesignResult:
        cached = self._design_cache.get(name)
        if cached is not None:
            return cached
        try:
            result = self._design_provider.get_beam_design_total(name)
        except Exception as exc:
            result = BeamDesignResult(
                name,
                status=DesignResultStatus.API_ERROR,
                status_message=str(exc),
                source_objects=(name,),
                result_source="GetSummaryResultsBeam_2",
            )
        self._design_cache[name] = result
        return result

    def design_object_exclusions(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._design_exclusions)

    def frame_types(self) -> dict[str, str]:
        return _frame_types(self.sap)

    def section_info(self, section_name: str) -> dict[str, Any]:
        return _section_info(self.sap, section_name)

    def design_code(self) -> str:
        return str(
            api_values(
                self.sap.DesignConcrete.GetCode(None),
                "DesignConcrete.GetCode",
            )[0]
        )

    def identity(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "model_path": str(self.model_path),
            "durable_model_path": str(self.durable_model_path),
            "source_model_sha256": self.assert_current(),
        }


class SqliteReadSource:
    """Normalized, fail-closed facade over one project SQLite snapshot."""

    kind = "sqlite-snapshot"

    def __init__(
        self,
        snapshot_path: str | Path,
        source_model_path: str | Path,
    ) -> None:
        self.snapshot_path = Path(snapshot_path).resolve()
        self.model_path = Path(source_model_path).resolve()
        self.reader = SnapshotReader(self.snapshot_path)
        metadata = self.reader.metadata()
        metadata_model = Path(str(metadata.get("source_model_path", ""))).resolve()
        if not model_paths_match(metadata_model, self.model_path):
            raise SnapshotSchemaError(f"SNAPSHOT_SOURCE_PATH_MISMATCH: {metadata_model} != {self.model_path}")
        self._metadata = metadata
        self._source = SqliteSnapshotSource(self.reader)
        self._design = SqliteDesignProvider(self.reader)
        self._design_exclusions: list[dict[str, Any]] = []

    def assert_current(self) -> str:
        return self.reader.assert_source_current(self.model_path)

    def extract_beams(
        self,
        selection: str = "all",
        *,
        include_design_ineligible: bool = False,
        progress: Callable[[int, str], None] | None = None,
    ) -> list[BeamGeometry]:
        if selection != "all":
            raise SnapshotTableError("SNAPSHOT_SELECTION_UNSUPPORTED: only all is available")
        _emit_progress(progress, 0, "读取快照梁几何")
        beams = self._source.extract_beams()
        design_names = set(self._design.beam_names())
        eligible = [beam for beam in beams if beam.name in design_names]
        self._design_exclusions = [
            {
                "object_name": beam.name,
                "label": beam.label,
                "story": beam.story,
                "section": beam.section,
                "status": DesignResultStatus.NO_RESULTS.value,
                "reason": "快照设计结果中不存在当前对象。",
            }
            for beam in beams
            if beam.name not in design_names
        ]
        result = beams if include_design_ineligible else eligible
        _emit_progress(progress, 100, f"快照梁几何读取完成（{len(result)} 梁）")
        return result

    def column_supports(self) -> dict[str, Point3D]:
        return self._source.column_supports()

    def frame_states(self, beams: Sequence[BeamGeometry]) -> dict[str, FrameState]:
        return self._source.frame_states(beams)

    def designs(
        self,
        beams: Iterable[BeamGeometry],
        *,
        progress: Callable[[int, str], None] | None = None,
    ) -> dict[str, BeamDesignResult]:
        beam_names = tuple(beam.name for beam in beams)
        snapshot_names = self._design.beam_names()
        if set(beam_names) != set(snapshot_names):
            raise SnapshotTableError("SNAPSHOT_DESIGN_BEAM_SET_MISMATCH")
        designs: dict[str, BeamDesignResult] = {}
        _emit_progress(progress, 0, "读取梁设计结果")
        last_percent = -1
        total = len(beam_names)
        for index, name in enumerate(beam_names, 1):
            designs[name] = self._design.get_beam_design(name)
            percent = round(index * 100 / max(total, 1))
            if percent != last_percent:
                _emit_progress(progress, percent, f"读取梁设计结果 {index}/{total}")
                last_percent = percent
        _emit_progress(progress, 100, f"梁设计结果读取完成（{total} 梁）")
        return designs

    def frame_types(self) -> dict[str, str]:
        return self._source.frame_types()

    def section_info(self, section_name: str) -> dict[str, Any]:
        return self._source.section_info(section_name)

    def design_code(self) -> str:
        return str(self._metadata.get("design_code", ""))

    def identity(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "snapshot_path": str(self.snapshot_path),
            "snapshot_sha256": sha256_file(self.snapshot_path),
            "model_path": str(self.model_path),
            "source_model_sha256": self.assert_current(),
            "snapshot_schema_version": self._metadata.get("snapshot_schema_version"),
            "field_mapping_version": self._metadata.get("field_mapping_version"),
            "domain_mapping_version": self._metadata.get("domain_mapping_version"),
            "export_profile": self._metadata.get("export_profile"),
            "export_profile_version": self._metadata.get("export_profile_version"),
            "etabs_version": self._metadata.get("etabs_version"),
            "api_version": self._metadata.get("api_version"),
        }

    def design_object_exclusions(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._design_exclusions)


def _read_designs(
    provider: EtabsResultProvider,
    beams: Iterable[BeamGeometry],
) -> dict[str, BeamDesignResult]:
    designs: dict[str, BeamDesignResult] = {}
    for beam in beams:
        if beam.depth is None or str(beam.section).strip().casefold() in {"", "none", "null"}:
            designs[beam.name] = BeamDesignResult(
                beam.name,
                status=DesignResultStatus.SKIPPED,
                status_message="非混凝土或无有效截面。",
            )
            continue
        try:
            designs[beam.name] = provider.get_beam_design_total(beam.name)
        except Exception as exc:
            designs[beam.name] = BeamDesignResult(
                beam.name,
                status=DesignResultStatus.API_ERROR,
                status_message=str(exc),
            )
    return designs
