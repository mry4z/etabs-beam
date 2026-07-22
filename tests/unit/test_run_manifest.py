import json
from datetime import datetime, timezone
from pathlib import Path

from src.run_manifest import RunManifest, artifact_record, create_run_id, fingerprint


def test_create_run_id_is_sortable_and_unique() -> None:
    now = datetime(2026, 7, 22, 1, 2, 3, tzinfo=timezone.utc)
    first = create_run_id(now)
    second = create_run_id(now)
    assert first.startswith("20260722T010203Z-")
    assert first != second


def test_artifact_record_hashes_directory_deterministically(tmp_path: Path) -> None:
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "b.txt").write_text("B", encoding="utf-8")
    (folder / "a.txt").write_text("A", encoding="utf-8")
    first = artifact_record(folder)
    second = artifact_record(folder)
    assert first["file_count"] == 2
    assert first["sha256"] == second["sha256"]


def test_fingerprint_ignores_mapping_order() -> None:
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_new_input_supersedes_previous_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.run_manifest.git_state", lambda root=None: {"commit": "abc", "dirty": False})
    model_a = tmp_path / "a.EDB"
    model_b = tmp_path / "b.EDB"
    model_a.write_bytes(b"model-a")
    model_b.write_bytes(b"model-b")
    registry = tmp_path / "registry"

    first = RunManifest(
        output_dir=tmp_path / "run-a",
        registry_dir=registry,
        config={"standard": "ACI 318-14"},
        arguments={"expected_model": model_a, "selection": "all"},
        run_id="run-a",
    )
    first_path = first.finalize(
        status="SUCCESS",
        summary={"model": str(model_a), "outputs": {}, "design_standard": "ACI 318-14"},
    )
    second = RunManifest(
        output_dir=tmp_path / "run-b",
        registry_dir=registry,
        config={"standard": "ACI 318-14"},
        arguments={"expected_model": model_b, "selection": "all"},
        run_id="run-b",
    )
    second.finalize(
        status="SUCCESS",
        summary={"model": str(model_b), "outputs": {}, "design_standard": "ACI 318-14"},
    )

    prior = json.loads(first_path.read_text(encoding="utf-8"))
    assert prior["validity"] == {
        "state": "SUPERSEDED",
        "input_fingerprint": prior["validity"]["input_fingerprint"],
        "superseded_by_run_id": "run-b",
        "reason": "INPUT_OR_ALGORITHM_FINGERPRINT_CHANGED",
    }


def test_failed_run_is_invalid_and_does_not_supersede(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.run_manifest.git_state", lambda root=None: {"commit": "abc", "dirty": False})
    model = tmp_path / "model.EDB"
    model.write_bytes(b"model")
    registry = tmp_path / "registry"
    successful = RunManifest(
        output_dir=tmp_path / "ok",
        registry_dir=registry,
        config={},
        arguments={"expected_model": model},
        run_id="ok",
    )
    success_path = successful.finalize(status="SUCCESS", summary={"model": str(model), "outputs": {}})
    failed = RunManifest(
        output_dir=tmp_path / "bad",
        registry_dir=registry,
        config={},
        arguments={"expected_model": model},
        run_id="bad",
    )
    failed_path = failed.finalize(status="FAILED", error="boom")

    assert json.loads(failed_path.read_text(encoding="utf-8"))["validity"]["state"] == "INVALID"
    assert json.loads(success_path.read_text(encoding="utf-8"))["validity"]["state"] == "CURRENT"


def test_same_fingerprint_keeps_both_manifests_current(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.run_manifest.git_state", lambda root=None: {"commit": "abc", "dirty": False})
    model = tmp_path / "model.EDB"
    model.write_bytes(b"same-model")
    registry = tmp_path / "registry"
    paths = []
    for run_id in ("first", "second"):
        session = RunManifest(
            output_dir=tmp_path / run_id,
            registry_dir=registry,
            config={"standard": "ACI 318-14"},
            arguments={"expected_model": model, "selection": "all"},
            run_id=run_id,
        )
        paths.append(
            session.finalize(
                status="SUCCESS",
                summary={"model": str(model), "outputs": {}, "design_standard": "ACI 318-14"},
            )
        )

    assert all(json.loads(path.read_text(encoding="utf-8"))["validity"]["state"] == "CURRENT" for path in paths)
