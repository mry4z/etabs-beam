from types import SimpleNamespace

from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.model_source import EtabsOapiReadSource, SqliteReadSource


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


def test_sqlite_source_keeps_explicit_no_design_outside_the_eligible_set():
    beams = (_beam("DESIGN-1"), _beam("RIB-1", "RIB 150X370"))
    results = {
        "DESIGN-1": BeamDesignResult("DESIGN-1"),
        "RIB-1": BeamDesignResult(
            "RIB-1",
            status=DesignResultStatus.SKIPPED,
            status_message="EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
        ),
    }
    source = object.__new__(SqliteReadSource)
    source._source = SimpleNamespace(extract_beams=lambda: list(beams))
    source._design = SimpleNamespace(
        beam_names=lambda: tuple(results),
        get_beam_design=lambda name: results[name],
    )
    source._design_exclusions = []

    eligible = source.extract_beams()
    assert [beam.name for beam in eligible] == ["DESIGN-1"]
    all_geometry = source.extract_beams(include_design_ineligible=True)
    assert [beam.name for beam in all_geometry] == ["DESIGN-1", "RIB-1"]
    assert source.design_object_exclusions() == ({
        "object_name": "RIB-1",
        "label": "RIB-1",
        "story": "L1",
        "section": "RIB 150X370",
        "status": "不纳入混凝土设计",
        "reason": "EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
    },)
