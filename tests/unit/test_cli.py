import json
from pathlib import Path

import pytest

from src.cli import (
    _etabs_version,
    _load_confirmed_multi_section_spans,
    _parser,
    _same_model_path,
    _save_writeback_or_rollback,
    run,
)
from src.core.model_writeback import VerificationResult, WritebackExecutionError


def test_cli_parser_exposes_safe_workflow_flags() -> None:
    args = _parser().parse_args(
        [
            "--pid",
            "123",
            "--expected-model",
            "model.EDB",
            "--read-only",
            "--export-excel",
            "--output-dir",
            "out",
        ]
    )
    assert args.pid == 123
    assert args.read_only is True
    assert args.writeback is False
    assert args.export_excel is True
    assert args.output_dir == Path("out")
    assert args.source_model is None
    assert args.manifest_registry is None


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
        ]
    )
    monkeypatch.setattr(
        "src.cli._run_impl",
        lambda args, config, output_dir: {
            "status": "OK",
            "model": str(tmp_path / "missing.EDB"),
            "outputs": {},
        },
    )

    summary = run(args)

    assert summary["run_id"]
    assert Path(summary["outputs"]["manifest"]).is_file()
    assert (output / "topology_summary.json").is_file()


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

    manifests = list(output.glob("run_manifest_*.json"))
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
