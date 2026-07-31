import sys
from types import SimpleNamespace

import pytest

from src.core.etabs_connector import (
    EtabsConnectionError,
    EtabsConnector,
    etabs_version_info,
    model_paths_match,
)
from src.core.geometry_extractor import GeometryExtractor, expand_rigid_support_points


class FakeSapModel:
    def __init__(self, units: int) -> None:
        self.units = units
        self.set_calls: list[int] = []

    def GetPresentUnits(self) -> int:
        return self.units

    def SetPresentUnits(self, units: int) -> int:
        self.units = units
        self.set_calls.append(units)
        return 0


def _connector(model: FakeSapModel) -> EtabsConnector:
    connector = EtabsConnector("unused.dll")
    connector.sap_model = model
    return connector


def test_etabs_22_ref_version_signature_is_supported() -> None:
    class Sap:
        def GetVersion(self, *args):
            if not args:
                raise TypeError("ref arguments required")
            return 0, "22.7.0", 0.0

    info = etabs_version_info(Sap())

    assert info["major"] == 22
    assert info["supported"] is True


def test_etabs_23_no_argument_version_signature_is_supported() -> None:
    class Sap:
        def GetVersion(self):
            return 0, "23.3.1", 0.0

    info = etabs_version_info(Sap())

    assert info["major"] == 23
    assert info["supported"] is True


def test_temporary_units_restores_original_value() -> None:
    model = FakeSapModel(6)
    with _connector(model).temporary_present_units(9) as original:
        assert original == 6
        assert model.units == 9
    assert model.units == 6
    assert model.set_calls == [9, 6]


def test_temporary_units_restores_after_failure() -> None:
    model = FakeSapModel(6)
    with pytest.raises(RuntimeError):
        with _connector(model).temporary_present_units(9):
            raise RuntimeError("export failed")
    assert model.units == 6


def test_process_attachment_uses_explicit_pid(monkeypatch, tmp_path) -> None:
    dll = tmp_path / "ETABSv1.dll"
    dll.write_bytes(b"fake")
    sap = object()
    calls: list[tuple[str, int]] = []

    class HelperApi:
        def GetObjectProcess(self, prog_id, pid):
            calls.append((prog_id, pid))
            return SimpleNamespace(SapModel=sap)

    monkeypatch.setitem(
        sys.modules,
        "clr",
        SimpleNamespace(AddReference=lambda path: path),
    )
    monkeypatch.setitem(
        sys.modules,
        "ETABSv1",
        SimpleNamespace(
            Helper=lambda: object(),
            cHelper=lambda _helper: HelperApi(),
        ),
    )

    connector = EtabsConnector(dll)

    assert connector.attach_process(1234) is sap
    assert connector.app is not None
    assert calls == [(connector.PROG_ID, 1234)]


def test_process_attachment_rejects_invalid_pid() -> None:
    connector = EtabsConnector("unused.dll")

    with pytest.raises(EtabsConnectionError, match="正整数"):
        connector.attach_process(0)
    with pytest.raises(EtabsConnectionError, match="正整数"):
        connector.attach_process(True)


def test_model_paths_match_accepts_only_same_edb_sidecar(tmp_path) -> None:
    expected = tmp_path / "model.EDB"

    assert model_paths_match(tmp_path / "model.$et", expected)
    assert model_paths_match(expected, expected)
    assert not model_paths_match(tmp_path / "other.$et", expected)
    assert not model_paths_match(tmp_path / "nested" / "model.$et", expected)


def test_rigid_support_propagates_only_from_real_column_points() -> None:
    assert expand_rigid_support_points(
        {"COLUMN"},
        [("COLUMN", "RIGID_BEAM"), ("RIGID_BEAM", "BEAM_ENDPOINT"), ("FREE", "UNANCHORED")],
    ) == {"COLUMN", "RIGID_BEAM", "BEAM_ENDPOINT"}


def test_column_supports_reads_coordinates_not_limited_to_beam_endpoints(monkeypatch) -> None:
    class Frames:
        points = {
            "COL": ("C0", "C1"),
            "RIGID1": ("C1", "R1"),
            "RIGID2": ("R1", "R2"),
            "BEAM": ("B0", "B1"),
        }

        def GetNameList(self, *_):
            return 0, len(self.points), list(self.points)

        def GetAllFrames(self, *_):
            names = list(self.points)
            sections = ["rigid link" if name.startswith("RIGID") else "B" for name in names]
            point_i = [self.points[name][0] for name in names]
            point_j = [self.points[name][1] for name in names]
            coordinates = Points.coordinates | {
                "B0": (0.0, 100.0, 0.0),
                "B1": (200.0, 100.0, 0.0),
            }
            starts = [coordinates[name] for name in point_i]
            ends = [coordinates[name] for name in point_j]
            arrays = [
                names,
                sections,
                ["F2"] * len(names),
                point_i,
                point_j,
                [point[0] for point in starts],
                [point[1] for point in starts],
                [point[2] for point in starts],
                [point[0] for point in ends],
                [point[1] for point in ends],
                [point[2] for point in ends],
            ]
            arrays.extend([[0] * len(names) for _ in range(8)])
            return (0, len(names), *arrays)

        def GetPoints(self, name, *_):
            return 0, *self.points[name]

        def GetSection(self, name, *_):
            return 0, "rigid link" if name.startswith("RIGID") else "B", ""

        def GetDesignOrientation(self, name, *_):
            return 0, 1 if name == "COL" else 2

    class Points:
        calls = 0
        coordinates = {
            "C0": (0.0, 0.0, -3000.0),
            "C1": (0.0, 0.0, 0.0),
            "R1": (100.0, 0.0, 0.0),
            "R2": (200.0, 0.0, 0.0),
        }

        def GetCoordCartesian(self, name, *_):
            self.calls += 1
            return 0, *self.coordinates[name]

    sap = SimpleNamespace(FrameObj=Frames(), PointObj=Points())
    monkeypatch.setitem(
        sys.modules,
        "ETABSv1",
        SimpleNamespace(eFrameDesignOrientation=SimpleNamespace(Column=1)),
    )

    supports = GeometryExtractor(sap).column_supports()

    assert supports == Points.coordinates
    assert sap.PointObj.calls == 0
