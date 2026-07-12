import pytest

from src.core.etabs_connector import EtabsConnector


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
