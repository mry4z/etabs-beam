from __future__ import annotations

from types import SimpleNamespace

from src.design.etabs_result import EtabsResultProvider


class _DatabaseTables:
    def GetTableForDisplayArray(self, *_args):
        fields = ["Story", "Label", "UniqueName", "DesignSect", "Location", "VCombo", "Shear", "VRebar", "Status"]
        rows = [
            ["S1", "B1", "B1", "B300", "100.0", "EQX", "125.5", "0.42", "OK"],
            ["S1", "B1", "B1", "B300", "200.0", "EQX", "-150.0", "0.55", "OK"],
        ]
        flat = [value for row in rows for value in row]
        return (0, None, 0, fields, len(rows), flat)


class _DesignConcrete:
    def GetSeismicFramingType(self, *_args):
        return (0, 2, ["B1", "B2"], ["Sway Special", "Non Sway"])


def test_reads_resolved_frame_types_and_capacity_shear_envelope() -> None:
    provider = EtabsResultProvider(
        SimpleNamespace(
            DesignConcrete=_DesignConcrete(),
            DatabaseTables=_DatabaseTables(),
        )
    )

    assert provider.get_seismic_framing_types() == {
        "B1": "Sway Special",
        "B2": "Non Sway",
    }
    envelope = provider.get_beam_shear_envelope()
    assert envelope["B1"][0].shear_kN == 125.5
    assert envelope["B1"][1].shear_kN == 150.0
    assert envelope["B1"][1].combination == "EQX"
    assert envelope["B1"][1].source == "Concrete Beam Shear Envelope - ACI 318-14"


def test_missing_resolved_frame_type_capability_is_nonfatal() -> None:
    provider = EtabsResultProvider(SimpleNamespace(DesignConcrete=SimpleNamespace()))
    assert provider.get_seismic_framing_types() == {}
