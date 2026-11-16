import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import cli
from src.cli import (
    _beam_progress,
    _display_summary,
    _ensure_etabs_dll,
    _etabs_version,
    _load_confirmed_multi_section_spans,
    _parser,
    _same_model_path,
    _save_writeback_or_rollback,
    run,
)
from src.config import load_config
from src.core.beam_line_span_analyzer import BeamLineSpanResult
from src.core.geometry_extractor import RigidLinkGeometry
from src.core.model_writeback import VerificationResult, WritebackExecutionError


def test_cli_parser_exposes_safe_workflow_flags() -> None:
    args = _parser().parse_args(
        [
            "--pid",
            "123",
            "--expected-model",
            "model.EDB",
            "--etabs-api",
            "ETABSv1.dll",
            "--read-only",
            "--export-excel",
            "--output-dir",
            "out",
            "--rebar-workers",
            "2",
        ]
    )
    assert args.pid == 123
    assert args.etabs_api == Path("ETABSv1.dll")
    assert args.read_only is True
    assert args.writeback is False
    assert args.export_excel is True
    assert args.output_dir == Path("out")
    assert args.rebar_workers == 2
    assert args.source_model is None
    assert args.manifest_registry is None


@pytest.mark.parametrize("value, expected", [("auto", "auto"), ("4", 4)])
def test_rebar_workers_cli_accepts_auto_and_preserves_integer_type(value, expected):
    assert _parser().parse_args(["--rebar-workers", value]).rebar_workers == expected
    from src.snapshot_cli import _parser as snapshot_parser
    args = snapshot_parser().parse_args(["--pid", "1", "--expected-model", "test.EDB",
        "--output", "new.sqlite", "--rebar-workers", value])
    assert args.rebar_workers == expected


def test_cli_parser_accepts_stable_detailing_policy_ids() -> None:
    args = _parser().parse_args(["--pid", "1", "--detailing-policy", "drawing_20260924_v1"])
    assert args.detailing_policy == "drawing_20260924_v1"


def test_config_keeps_older_files_legacy_and_validates_policy_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy-config.yaml"
    config_path.write_text("etabs:\n  dll_path: ETABSv1.dll\n", encoding="utf-8")

    old_config = load_config(config_path)
    assert old_config.design.detailing_policy == "legacy"
    assert old_config.design.candidate_lookup == "off"
    assert load_config("config.example.yaml").design.detailing_policy == "drawing_20260924_v1"
    assert load_config("config.example.yaml").design.candidate_lookup == "auto"
    assert (
        load_config("plugins/etabs/portable/config.yaml").design.detailing_policy
        == "drawing_20260924_v1"
    )
    assert load_config("plugins/etabs/portable/config.yaml").design.candidate_lookup == "auto"
    assert (
        load_config(config_path, detailing_policy_id="drawing_20260924_v1").design.detailing_policy
        == "drawing_20260924_v1"
    )

    invalid_config = tmp_path / "invalid-config.yaml"
    invalid_config.write_text(
        "etabs:\n  dll_path: ETABSv1.dll\ndesign:\n  detailing_policy: future-policy\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="design.detailing_policy"):
        load_config(invalid_config)


def test_fresh_drawing_policy_engine_selects_r7_and_380_eligible_rules() -> None:
    from src.design.detailing import make_fresh_detailing_engine
    from src.design.offline_style import DRAWING_POLICY_REVISION_R7_ID

    engine = make_fresh_detailing_engine("drawing_20260924_v1")
    legacy_engine = make_fresh_detailing_engine("legacy")

    assert engine.detailing_policy_revision_id == DRAWING_POLICY_REVISION_R7_ID
    assert engine.offline_style_library.effective_rule_summary(engine)["eligible_rule_count"] == 380
    assert engine.project.max_main_bar_layers == 3
    assert engine.project.smf_max_longitudinal_ratio == 0.02
    assert legacy_engine.detailing_policy_id == "legacy"


def test_runtime_self_test_reports_fresh_r7_policy_metadata(tmp_path, monkeypatch) -> None:
    from src.design.offline_style import DRAWING_POLICY_REVISION_R7_ID

    api = tmp_path / "ETABSv1.dll"
    api.write_bytes(b"test")
    config = SimpleNamespace(
        etabs=SimpleNamespace(dll_path=api),
        design=SimpleNamespace(detailing_policy="drawing_20260924_v1", layout_dictionary_path=None),
    )
    monkeypatch.setattr(cli, "load_config", lambda *_args, **_kwargs: config)

    class FakeHelper:
        pass

    class FakePluginContract:
        pass

    monkeypatch.setitem(sys.modules, "clr", SimpleNamespace(AddReference=lambda _path: None))
    monkeypatch.setitem(
        sys.modules,
        "ETABSv1",
        SimpleNamespace(cHelper=FakeHelper, cPluginContract=FakePluginContract),
    )
    result = cli._runtime_self_test(
        SimpleNamespace(etabs_api=str(api), config=None, detailing_policy=None)
    )

    assert result["detailing_policy_revision_id"] == DRAWING_POLICY_REVISION_R7_ID
    assert result["detailing_policy_resource"]["policy_revision_id"] == DRAWING_POLICY_REVISION_R7_ID
    assert result["detailing_policy_resource"]["eligible_rule_count"] == 380


def test_frozen_policy_resource_is_found_beside_packaged_module(tmp_path: Path, monkeypatch) -> None:
    import shutil

    from src.design import offline_style

    packaged_design_dir = tmp_path / "moved-package" / "src" / "design"
    policy_dir = packaged_design_dir / "policies"
    policy_dir.mkdir(parents=True)
    packaged_module = packaged_design_dir / "offline_style.py"
    packaged_module.write_text("# simulated frozen module location\n", encoding="utf-8")
    packaged_resource = policy_dir / "drawing_20260924_v1.json"
    shutil.copyfile(offline_style.default_runtime_rules_path(), packaged_resource)
    monkeypatch.setattr(offline_style, "__file__", str(packaged_module))

    library = offline_style.OfflineStyleLibrary()

    assert library.path == packaged_resource
    assert library.payload["policy_id"] == "drawing_20260924_v1"


def test_cli_beam_progress_is_monotonic_and_throttled(capsys) -> None:
    _beam_progress._state = {"percent": -1}
    for percent in (0, 25, 25, 20, 100):
        _beam_progress(percent, "阶段")

    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[beam]")]
    values = [int(line.split("]", 2)[2].split("%", 1)[0]) for line in lines]
    assert values == [0, 25, 100]
    assert all(0 <= value <= 100 for value in values)
    assert lines[-1].endswith("100% 阶段")


def test_cli_display_summary_keeps_ordinary_stdout_compact() -> None:
    displayed = _display_summary(
        {
            "status": "OK",
            "mode": "read-only",
            "beams": 3,
            "spans": 2,
            "blocking_topology_diagnostics": 1,
            "beam_result_dxf_issues": [{"span_id": "F1-PB1-1"}],
            "beam_result_dxf_summary": [{"span_id": "F1-PB1-1", "status": "AVAILABLE"}],
            "outputs": {"beam_result_dxf": ["wpj-F1.dxf"]},
        }
    )

    assert displayed == {
        "status": "OK",
        "mode": "read-only",
        "beams": 3,
        "spans": 2,
        "errors": 2,
        "beam_result_dxf_errors": 1,
        "outputs": {"beam_result_dxf": ["wpj-F1.dxf"]},
    }


@pytest.mark.parametrize(
    ("policy_id", "expected_sb_override"),
    [("drawing_20260924_v1", "Non Sway"), ("drawing_v3", "Non Sway"), ("legacy", None)],
)
def test_read_only_exports_reuse_topology_and_pass_policy_scoped_sb_override(
    monkeypatch,
    tmp_path: Path,
    policy_id,
    expected_sb_override,
) -> None:
    """Read-only exports preserve topology and pass the explicit override only to drawing strategies."""

    model = tmp_path / "model.EDB"
    model.write_bytes(b"offline model")
    beams = [SimpleNamespace(name="B1")]
    rigid_link = RigidLinkGeometry("RIGID", "F1", "P1", "P2", (0, 0, 0), (100, 0, 0), "Rigid")
    structural = BeamLineSpanResult((), (), (), (), (rigid_link,))
    extraction_calls = []
    schedule_calls = []
    dxf_calls = []
    span_note_calls = []
    payload_calls = []

    class Source:
        def extract_beams(self, selection="all", **kwargs):
            extraction_calls.append((selection, kwargs))
            return beams

        def column_supports(self):
            return {}

        def column_outlines(self):
            return ()

        def frame_states(self, _beams):
            return {}

        def designs(self, source_beams, **kwargs):
            return {beam.name: object() for beam in source_beams}

        def design_object_exclusions(self):
            return ()

        def section_info(self, _section):
            return {}

        def frame_types(self):
            return {"B1": "Sway Special"}

        def design_code(self):
            return "ACI 318-14"

        def identity(self):
            return {"kind": "fake-etabs"}

    source = Source()
    sap = SimpleNamespace(
        GetModelIsLocked=lambda: False,
        GetPresentUnits=lambda: "original",
        SetPresentUnits=lambda _units: 0,
        DesignConcrete=SimpleNamespace(GetResultsAvailable=lambda: True),
    )

    class Connector:
        def __init__(self, _dll_path):
            self.sap_model = None

        @contextmanager
        def temporary_present_units(self, _units):
            yield

    def fake_schedule(result, source_beams, _designs, _tolerance, **kwargs):
        schedule = SimpleNamespace(records=(), detailing_policy_id=kwargs.get("detailing_policy_id"))
        schedule_calls.append((result, source_beams, schedule))
        return schedule

    def fake_dxf(schedule, source_beams, _section_info, _output_dir, **_kwargs):
        dxf_calls.append((schedule, source_beams))
        return SimpleNamespace(result_paths=(), section_paths=(), issues=(), summaries=())

    def fake_payload_builder(*_args, **kwargs):
        payload_calls.append(kwargs)
        return {"metadata": {"rebar_parallelism": {"workers_effective": 1}}, "raw_rows": []}

    monkeypatch.setattr(cli, "_attach", lambda *_args: (sap, model))
    monkeypatch.setattr(cli, "_etabs_version", lambda _sap: {})
    monkeypatch.setattr(cli, "_api_environment", lambda _path: {})
    monkeypatch.setattr(cli, "EtabsConnector", Connector)
    monkeypatch.setattr(cli, "EtabsOapiReadSource", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(cli, "run_tower_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "analyze_beam_lines", lambda *_args, **_kwargs: structural)
    monkeypatch.setattr(
        cli,
        "build_writeback_plan",
        lambda *_args, **_kwargs: SimpleNamespace(orientations=(), renames=()),
    )
    monkeypatch.setattr(cli, "build_requires_split_records", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(cli, "build_multi_section_span_records", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(cli, "export_structural_spans_csv", lambda _spans, path, **_kwargs: path)
    def fake_span_note(_spans, path, **kwargs):
        span_note_calls.append(kwargs)
        return path

    monkeypatch.setattr(cli, "export_structural_spans_dxf", fake_span_note)
    monkeypatch.setattr(cli, "export_requires_split_dxf", lambda _records, path, **_kwargs: path)
    monkeypatch.setattr(
        cli,
        "write_requires_split_audit",
        lambda _records, data_dir, _stem: (data_dir / "requires.json", data_dir / "requires.csv"),
    )
    monkeypatch.setattr(
        cli,
        "write_multi_section_span_audit",
        lambda _records, data_dir: (data_dir / "sections.json", data_dir / "sections.csv"),
    )
    monkeypatch.setattr(cli, "build_span_schedule", fake_schedule)
    monkeypatch.setattr(cli, "export_beam_result_and_section_dxfs", fake_dxf)
    monkeypatch.setattr(cli, "build_rebar_workbook_payload_from_domain", fake_payload_builder)
    monkeypatch.setattr(cli, "write_rebar_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "build_secondary_schedule_payload", lambda _payload: {})
    monkeypatch.setattr(cli, "run_workbook_builder", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "run_secondary_workbook_builder", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eUnits=SimpleNamespace(N_mm_C="N_mm_C")))

    args = cli._parser().parse_args(
        [
            "--pid",
            "123",
            "--expected-model",
            str(model),
            "--read-only",
            "--export-beam-result-dxf",
            "--export-rebar-workbook",
            "--detailing-policy",
            policy_id,
        ]
    )
    result = cli._run_impl(
        args,
        load_config("config.example.yaml", candidate_lookup="off"),
        tmp_path / "output",
    )

    assert result["status"] == "OK"
    assert extraction_calls == [("all", {})]
    schedule = schedule_calls[0][2]
    assert schedule_calls[0][:2] == (structural, beams)
    assert dxf_calls == [(schedule, beams)]
    assert span_note_calls[0]["rigid_links"] == (rigid_link,)
    assert schedule.detailing_policy_id == policy_id
    assert len(payload_calls) == 1
    assert payload_calls[0]["detailing_policy_id"] == policy_id
    assert payload_calls[0]["sb_frame_type_override"] == expected_sb_override
    assert payload_calls[0]["frame_types"] == {"B1": "Sway Special"}


def test_cli_parser_exposes_formal_rebar_workbook_flags() -> None:
    args = _parser().parse_args(
        [
            "--pid",
            "123",
            "--read-only",
            "--export-rebar-workbook",
            "--artifact-node",
            "C:/runtime/node.exe",
            "--artifact-node-modules",
            "C:/runtime/node_modules",
        ]
    )

    assert args.export_rebar_workbook is True
    assert args.artifact_node == "C:/runtime/node.exe"
    assert args.artifact_node_modules == Path("C:/runtime/node_modules")
    assert args.artifact_node_max_old_space_mb == 8192
    assert args.workbook_builder == "python"
    assert args.candidate_lookup is None
    explicit_lookup = _parser().parse_args(["--pid", "123", "--candidate-lookup", "off"])
    assert explicit_lookup.candidate_lookup == "off"


def test_cli_ensure_etabs_dll_short_circuits_on_explicit(tmp_path: Path) -> None:
    config = load_config("config.example.yaml", etabs_dll_path=tmp_path / "missing.dll")
    assert _ensure_etabs_dll(config, explicit=tmp_path / "api.dll") is config


def test_cli_ensure_etabs_dll_prefers_env(tmp_path: Path, monkeypatch) -> None:
    dll = tmp_path / "env.dll"
    dll.write_bytes(b"x")
    monkeypatch.setenv("ETABS_API_PATH", str(dll))
    config = load_config("config.example.yaml", etabs_dll_path=tmp_path / "missing.dll")
    resolved = _ensure_etabs_dll(config, explicit=None)
    assert resolved.etabs.dll_path == dll.resolve()


def test_cli_ensure_etabs_dll_falls_back_to_search(tmp_path: Path, monkeypatch) -> None:
    dll = tmp_path / "found.dll"
    dll.write_bytes(b"x")
    config = load_config("config.example.yaml", etabs_dll_path=tmp_path / "missing.dll")
    monkeypatch.setattr("src.cli.search_etabs_dll_path", lambda: dll)
    resolved = _ensure_etabs_dll(config, explicit=None)
    assert resolved.etabs.dll_path == dll


def test_cli_ensure_etabs_dll_raises_when_not_found(tmp_path: Path, monkeypatch) -> None:
    config = load_config("config.example.yaml", etabs_dll_path=tmp_path / "missing.dll")
    monkeypatch.setattr("src.cli.search_etabs_dll_path", lambda: None)
    with pytest.raises(FileNotFoundError, match="ETABS API DLL"):
        _ensure_etabs_dll(config, explicit=None)


def test_cli_parser_accepts_snapshot_as_read_only_source() -> None:
    args = _parser().parse_args(
        [
            "--snapshot-input",
            "domain.sqlite",
            "--expected-model",
            "working.EDB",
            "--read-only",
        ]
    )

    assert args.pid is None
    assert args.snapshot_input == Path("domain.sqlite")
    assert args.expected_model == Path("working.EDB")


def test_cli_payload_replay_preserves_recorded_strategy_and_revision(tmp_path: Path) -> None:
    from src.design.detailing import make_detailing_engine
    from src.design.region_results import DRAWING_DETAILING_POLICY_ID
    from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION

    source = tmp_path / "old-drawing-payload.json"
    recorded_engine = make_detailing_engine(DRAWING_DETAILING_POLICY_ID)
    source.write_text(
        json.dumps(
            {
                "metadata": {
                    "schema_version": PAYLOAD_SCHEMA_VERSION,
                    "detailing_policy_id": DRAWING_DETAILING_POLICY_ID,
                    "detailing_policy_revision_id": recorded_engine.detailing_policy_revision_id,
                    "detailing_policy_parameters": recorded_engine.detailing_policy_parameters,
                    "ranking_mode": recorded_engine.ranking_mode,
                    "detailing_policy_rules_file": str(recorded_engine.offline_style_library.path),
                },
                "raw_rows": [],
            }
        ),
        encoding="utf-8",
    )
    args = _parser().parse_args(["--replay-payload", str(source), "--output-dir", str(tmp_path / "out")])

    summary = cli._replay_payload(args)

    assert summary["detailing_policy_id"] == DRAWING_DETAILING_POLICY_ID
    assert summary["detailing_policy_revision_id"] == recorded_engine.detailing_policy_revision_id
    assert not json.loads(Path(summary["output_payload"]).read_text(encoding="utf-8"))["metadata"].get(
        "detailing_policy_replay_history"
    )


def test_cli_payload_replay_uses_legacy_when_recorded_policy_is_absent(tmp_path: Path) -> None:
    from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION

    source = tmp_path / "old-payload.json"
    source.write_text(
        json.dumps({"metadata": {"schema_version": PAYLOAD_SCHEMA_VERSION}, "raw_rows": []}),
        encoding="utf-8",
    )
    args = _parser().parse_args(["--replay-payload", str(source), "--output-dir", str(tmp_path / "out")])

    summary = cli._replay_payload(args)

    assert summary["detailing_policy_id"] == "legacy"
    assert summary["detailing_policy_revision_id"] == "legacy"


@pytest.mark.parametrize("allow_revision_change", [False, True])
def test_cli_payload_replay_rejects_implicit_cross_policy_conversion(tmp_path: Path, allow_revision_change) -> None:
    from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION

    source = tmp_path / "legacy-payload.json"
    source.write_text(
        json.dumps({"metadata": {"schema_version": PAYLOAD_SCHEMA_VERSION}, "raw_rows": []}),
        encoding="utf-8",
    )
    args = _parser().parse_args(
        [
            "--replay-payload",
            str(source),
            "--output-dir",
            str(tmp_path / "out"),
            "--detailing-policy",
            "drawing_20260924_v1",
        ] + (["--allow-policy-revision-change"] if allow_revision_change else [])
    )

    with pytest.raises(ValueError, match="CROSS_STRATEGY_REQUIRES_RAW_SNAPSHOT"):
        cli._replay_payload(args)


@pytest.mark.parametrize(
    "source_revision",
    ["drawing_20261025_uniform_hoop_no_4_3_v1", "drawing_20261028_bounded_coordination_v1"],
)
@pytest.mark.parametrize(
    "opt_in_arguments",
    [(), ("--allow-policy-revision-change",), ("--candidate-lookup", "auto")],
    ids=["default-auto-is-not-opt-in", "explicit-revision-opt-in", "explicit-lookup-opt-in"],
)
def test_cli_payload_replay_revision_change_requires_explicit_opt_in(
    tmp_path: Path, source_revision: str, opt_in_arguments: tuple[str, ...]
) -> None:
    from src.design.detailing import make_detailing_engine
    from src.design.offline_style import DRAWING_POLICY_REVISION_R9_ID
    from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION

    recorded = make_detailing_engine("drawing_20260924_v1", detailing_policy_revision_id=source_revision)
    source = tmp_path / "recorded-payload.json"
    source.write_text(json.dumps({"metadata": {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "detailing_policy_id": "drawing_20260924_v1",
        "detailing_policy_revision_id": recorded.detailing_policy_revision_id,
        "detailing_policy_parameters": recorded.detailing_policy_parameters,
        "ranking_mode": recorded.ranking_mode,
    }, "raw_rows": []}), encoding="utf-8")
    arguments = ["--replay-payload", str(source), "--output-dir", str(tmp_path / "out"),
                 "--detailing-policy", "drawing_20260924_v1", *opt_in_arguments]
    args = _parser().parse_args(arguments)
    if not opt_in_arguments:
        with pytest.raises(ValueError, match="DETAILING_POLICY_REVISION_MISMATCH"):
            cli._replay_payload(args)
        return
    summary = cli._replay_payload(args)
    generated = json.loads(Path(summary["output_payload"]).read_text(encoding="utf-8"))
    assert summary["detailing_policy_revision_id"] == DRAWING_POLICY_REVISION_R9_ID
    history = generated["metadata"]["detailing_policy_replay_history"][-1]
    assert history["source_revision_id"] == source_revision
    assert history["target_revision_id"] == DRAWING_POLICY_REVISION_R9_ID
    assert history["revision_change_explicitly_authorized"] is True
    assert summary["rebar_generation_seconds"] >= 0
    assert summary["rebar_input_load_seconds"] >= 0
    assert summary["rebar_serialization_seconds"] >= 0


@pytest.mark.parametrize("arguments", [
    ["--allow-policy-revision-change", "--detailing-policy", "drawing_20260924_v1"],
    ["--allow-policy-revision-change", "--replay-payload", "absent.json"],
])
def test_cli_revision_opt_in_rejects_missing_replay_or_policy(arguments, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "run", lambda _args: pytest.fail("normal model workflow must not run"))
    assert cli.main(arguments) == 2
    error = json.loads(capsys.readouterr().out)
    assert "requires --replay-payload and explicit --detailing-policy" in error["error"]


def test_snapshot_cli_uses_configured_candidate_lookup(tmp_path: Path, monkeypatch) -> None:
    from src import snapshot_cli

    config = SimpleNamespace(
        design=SimpleNamespace(
            detailing_policy="drawing_20260924_v1",
            layout_dictionary_path=None,
            candidate_lookup="auto",
        )
    )
    engine_calls = []
    monkeypatch.setattr(snapshot_cli, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        snapshot_cli,
        "make_fresh_detailing_engine",
        lambda policy, **kwargs: engine_calls.append((policy, kwargs)) or object(),
    )

    with pytest.raises(FileNotFoundError, match="expected durable EDB not found"):
        snapshot_cli.run(
            SimpleNamespace(
                config=None,
                detailing_policy=None,
                layout_dictionary=None,
                rebar_workers=None,
                expected_model=tmp_path / "missing.EDB",
            )
        )

    assert engine_calls == [(
        "drawing_20260924_v1",
        {"layout_dictionary_path": None, "candidate_lookup": "auto"},
    )]


def test_cli_accepts_etabs_sidecar_for_same_model() -> None:
    edb = Path(r"F:\model\sample.EDB")
    sidecar = Path(r"F:\model\sample.$et")
    other = Path(r"F:\model\other.$et")
    assert _same_model_path(sidecar, edb)
    assert not _same_model_path(other, edb)


def test_etabs_version_supports_ref_parameter_signature() -> None:
    class Sap:
        def GetVersion(self, *args):
            if not args:
                raise TypeError("ref parameters required")
            return 0, "22.7.0", 0.0

    assert _etabs_version(Sap()) == {
        "available": True,
        "supported": True,
        "version": "22.7.0",
        "major": 22,
        "raw": [0, "22.7.0", 0.0],
    }


def test_cli_loads_confirmed_multi_section_spans(tmp_path: Path) -> None:
    path = tmp_path / "confirmed.json"
    path.write_text(
        json.dumps({"confirmed_span_ids": ["F1-PB1-1", "F1-PB1-1"]}),
        encoding="utf-8",
    )

    assert _load_confirmed_multi_section_spans(path) == {"F1-PB1-1"}


def test_cli_rejects_invalid_multi_section_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "confirmed.json"
    path.write_text('{"confirmed_span_ids": "F1-PB1-1"}', encoding="utf-8")

    with pytest.raises(ValueError):
        _load_confirmed_multi_section_spans(path)


def test_cli_run_writes_success_manifest(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    registry = tmp_path / "registry"
    args = _parser().parse_args(
        [
            "--pid",
            "1",
            "--output-dir",
            str(output),
            "--manifest-registry",
            str(registry),
            "--config",
            "config.example.yaml",
        ]
    )
    def fake_run(_args, _config, _output_dir, *, progress=None):
        assert progress is _beam_progress
        progress(0, "附加 ETABS")
        progress(40, "拓扑划分")
        return {
            "status": "OK",
            "mode": "read-only",
            "model": str(tmp_path / "missing.EDB"),
            "beams": 1,
            "spans": 1,
            "outputs": {},
        }

    monkeypatch.setattr("src.cli._run_impl", fake_run)

    summary = run(args)

    assert summary["run_id"]
    assert Path(summary["outputs"]["manifest"]).is_file()
    assert (output / "model_beam_output" / "_data" / "topology_summary.json").is_file()
    manifest = json.loads(Path(summary["outputs"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["configuration"]["design"]["detailing_policy"] == "drawing_20260924_v1"


def test_cli_run_writes_failure_manifest(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    registry = tmp_path / "registry"
    args = _parser().parse_args(
        [
            "--pid",
            "1",
            "--output-dir",
            str(output),
            "--manifest-registry",
            str(registry),
        ]
    )

    def fail(*args, **kwargs):
        raise RuntimeError("expected failure")

    monkeypatch.setattr("src.cli._run_impl", fail)
    with pytest.raises(RuntimeError, match="expected failure"):
        run(args)

    manifests = list((output / "model_beam_output" / "_data").glob("run_manifest_*.json"))
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert payload["status"] == "FAILED"
    assert payload["validity"]["state"] == "INVALID"


def test_save_failure_rolls_back_and_persists_restored_state() -> None:
    class File:
        calls = 0

        def Save(self):
            self.calls += 1
            return 1 if self.calls == 1 else 0

    file_api = File()

    class Sap:
        def GetModelIsLocked(self):
            return False

    sap = Sap()
    sap.File = file_api

    class Writeback:
        calls = 0

        def rollback_completed(self, *args, **kwargs):
            self.calls += 1
            return VerificationResult(True, 1)

    writeback = Writeback()
    with pytest.raises(WritebackExecutionError, match="已回滚并保存"):
        _save_writeback_or_rollback(sap, writeback, (), ())
    assert writeback.calls == 1
    assert file_api.calls == 2


@pytest.mark.parametrize("revision", ["drawing_20261025_etabs_only_independent_skin_v1", "drawing_20261028_bounded_coordination_v1"])
def test_cli_replay_recorded_bounded_revision(tmp_path, revision):
    from src.design.detailing import make_detailing_engine
    from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION
    engine = make_detailing_engine("drawing_20260924_v1", detailing_policy_revision_id=revision)
    source = tmp_path / "payload.json"
    source.write_text(json.dumps({"metadata": {"schema_version": PAYLOAD_SCHEMA_VERSION,
        "detailing_policy_id": "drawing_20260924_v1", "detailing_policy_revision_id": revision,
        "detailing_policy_parameters": engine.detailing_policy_parameters, "ranking_mode": engine.ranking_mode},
        "raw_rows": []}), encoding="utf-8")
    args = _parser().parse_args(["--replay-payload", str(source), "--output-dir", str(tmp_path / "out")])
    summary = cli._replay_payload(args)
    assert summary["detailing_policy_revision_id"] == revision
    metadata = json.loads(Path(summary["output_payload"]).read_text(encoding="utf-8"))["metadata"]
    if revision.endswith("bounded_coordination_v1"):
        assert metadata["coordination_solver_revision_id"] == "coordination_20261028_support_pool_v1"
        assert metadata["detailing_policy_parameters"]["coordination_solver_revision_id"] == metadata["coordination_solver_revision_id"]


def test_cli_payload_replay_preserves_recorded_r9_revision(tmp_path: Path) -> None:
    from src.design.detailing import make_detailing_engine
    from src.design.offline_style import DRAWING_POLICY_REVISION_R9_ID
    from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION

    engine = make_detailing_engine(
        "drawing_20260924_v1", detailing_policy_revision_id=DRAWING_POLICY_REVISION_R9_ID
    )
    source = tmp_path / "r9-payload.json"
    source.write_text(json.dumps({"metadata": {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "detailing_policy_id": "drawing_20260924_v1",
        "detailing_policy_revision_id": DRAWING_POLICY_REVISION_R9_ID,
        "detailing_policy_parameters": engine.detailing_policy_parameters,
        "ranking_mode": engine.ranking_mode,
    }, "raw_rows": []}), encoding="utf-8")
    args = _parser().parse_args(["--replay-payload", str(source), "--output-dir", str(tmp_path / "out")])

    summary = cli._replay_payload(args)

    assert summary["detailing_policy_revision_id"] == DRAWING_POLICY_REVISION_R9_ID
    metadata = json.loads(Path(summary["output_payload"]).read_text(encoding="utf-8"))["metadata"]
    assert metadata["coordination_solver_revision_id"] == "coordination_20261031_indexed_candidates_v1"
    assert metadata["detailing_policy_parameters"]["coordination_solver_revision_id"] == metadata["coordination_solver_revision_id"]
