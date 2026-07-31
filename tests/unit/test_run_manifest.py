import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION
from src.run_manifest import ALGORITHM_VERSIONS, RunManifest, artifact_record, create_run_id, fingerprint, git_state


def test_manifest_rebar_payload_identity_matches_writer_schema() -> None:
    assert ALGORITHM_VERSIONS["rebar_workbook_payload"] == PAYLOAD_SCHEMA_VERSION


def test_manifest_fingerprints_snapshot_input(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.run_manifest.git_state", lambda root=None: {"commit": "abc", "dirty": False})
    model = tmp_path / "model.EDB"
    snapshot = tmp_path / "domain.sqlite"
    model.write_bytes(b"model")
    snapshot.write_bytes(b"snapshot-v1")
    session = RunManifest(
        output_dir=tmp_path / "run",
        registry_dir=tmp_path / "registry",
        config={},
        arguments={
            "expected_model": model,
            "snapshot_input": snapshot,
            "selection": "all",
        },
        run_id="snapshot-run",
    )
    path = session.finalize(
        status="SUCCESS",
        summary={
            "model": str(model),
            "model_input_sha256": artifact_record(model)["sha256"],
            "data_source": {"kind": "sqlite-snapshot"},
            "outputs": {},
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["data_source"] == {"kind": "sqlite-snapshot"}
    assert payload["validity"]["state"] == "CURRENT"

    snapshot.write_bytes(b"snapshot-v2")
    changed = RunManifest(
        output_dir=tmp_path / "run-changed",
        registry_dir=tmp_path / "registry-changed",
        config={},
        arguments={
            "expected_model": model,
            "snapshot_input": snapshot,
            "selection": "all",
        },
        run_id="snapshot-run-changed",
    ).finalize(
        status="SUCCESS",
        summary={
            "model": str(model),
            "model_input_sha256": artifact_record(model)["sha256"],
            "data_source": {"kind": "sqlite-snapshot"},
            "outputs": {},
        },
    )
    changed_payload = json.loads(changed.read_text(encoding="utf-8"))
    assert changed_payload["validity"]["input_fingerprint"] != payload["validity"]["input_fingerprint"]


def test_git_state_fingerprints_untracked_file_contents(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    untracked = tmp_path / "new.txt"
    untracked.write_text("first\n", encoding="utf-8")

    first = git_state(tmp_path)
    untracked.write_text("second\n", encoding="utf-8")
    second = git_state(tmp_path)

    assert first["status_sha256"] == second["status_sha256"]
    assert first["content_sha256"] != second["content_sha256"]
    assert first["untracked_files"][0]["path"] == "new.txt"
    assert first["untracked_files"][0]["sha256"] != second["untracked_files"][0]["sha256"]


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
