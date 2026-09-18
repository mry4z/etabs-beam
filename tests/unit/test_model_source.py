from types import SimpleNamespace

from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.model_source import EtabsOapiReadSource


def _beam(name: str, section: str = "B300X600") -> BeamGeometry:
    return BeamGeometry(
        name, name, "L1", f"{name}-I", f"{name}-J",
        (0.0, 0.0, 0.0), (6000.0, 0.0, 0.0), section, 600.0,
    )


def test_oapi_source_excludes_objects_without_design_results(monkeypatch, tmp_path):
    beams = (_beam("DESIGN-1"), _beam("RIB-6303", "RIB 150X370"))
    results = {
        "DESIGN-1": BeamDesignResult("DESIGN-1"),
        "RIB-6303": BeamDesignResult(
            "RIB-6303", status=DesignResultStatus.NO_RESULTS,
            status_message="ETABS 未返回混凝土梁设计站点。",
        ),
    }

    class FakeGeometry:
        def __init__(self, _sap):
            pass

        def extract(self, _selection):
            return list(beams)

    class FakeProvider:
        def __init__(self, _sap):
            self.calls = []

        def get_beam_design_total(self, name):
            self.calls.append(name)
            return results[name]

    monkeypatch.setattr("src.model_source.GeometryExtractor", FakeGeometry)
    monkeypatch.setattr("src.model_source.EtabsResultProvider", FakeProvider)
    model = tmp_path / "model.EDB"
    model.write_bytes(b"model")
    sap = SimpleNamespace(
        DesignConcrete=SimpleNamespace(GetResultsAvailable=lambda: True),
    )

    source = EtabsOapiReadSource(sap, model)
    extraction_events = []
    eligible = source.extract_beams(progress=lambda percent, message: extraction_events.append((percent, message)))

    assert [beam.name for beam in eligible] == ["DESIGN-1"]
    assert source.designs(eligible)["DESIGN-1"].status is DesignResultStatus.AVAILABLE
    assert source.design_object_exclusions() == (
        {
            "object_name": "RIB-6303",
            "label": "RIB-6303",
            "story": "L1",
            "section": "RIB 150X370",
            "status": "无设计结果",
            "reason": "ETABS 未返回混凝土梁设计站点。",
        },
    )
    assert extraction_events[0][0] == 0
    assert extraction_events[-1][0] == 100
    assert [percent for percent, _ in extraction_events] == sorted(percent for percent, _ in extraction_events)
    design_events = []
    source.designs(eligible, progress=lambda percent, message: design_events.append((percent, message)))
    assert design_events[0][0] == 0
    assert design_events[-1][0] == 100


def test_oapi_source_keeps_partial_design_results_for_audited_review(monkeypatch, tmp_path):
    beam = _beam("PARTIAL-1")

    class FakeGeometry:
        def __init__(self, _sap):
            pass

        def extract(self, _selection):
            return [beam]

    class FakeProvider:
        def __init__(self, _sap):
            pass

        def get_beam_design_total(self, name):
            return BeamDesignResult(name, status=DesignResultStatus.PARTIAL)

    monkeypatch.setattr("src.model_source.GeometryExtractor", FakeGeometry)
    monkeypatch.setattr("src.model_source.EtabsResultProvider", FakeProvider)
    model = tmp_path / "model.EDB"
    model.write_bytes(b"model")
    source = EtabsOapiReadSource(
        SimpleNamespace(DesignConcrete=SimpleNamespace(GetResultsAvailable=lambda: True)),
        model,
    )

    assert [item.name for item in source.extract_beams()] == ["PARTIAL-1"]
    assert source.design_object_exclusions() == ()
