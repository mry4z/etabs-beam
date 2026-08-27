"""Versioned run manifests and deterministic artifact invalidation."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping
from uuid import uuid4

from src.config import PROJECT_ROOT

MANIFEST_SCHEMA_VERSION = "1.0"
ALGORITHM_VERSIONS = {
    "topology": "2026.07.22-p01",
    "numbering": "2026.07.22-no-span-s",
    "writeback": "1.0",
    "detailing": "aci318-14-si-errata+nasiriyah-smf-sb-2026-08-24-r1",
    "rebar_workbook_payload": "2.1",
}


def create_run_id(now: datetime | None = None) -> str:
    """Return a sortable run identifier with collision-resistant suffix."""

    moment = now or datetime.now(timezone.utc)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_model_file(path: str | Path | None) -> Path | None:
    """Resolve ETABS' transient ``.$et`` filename to its durable EDB if present."""

    if path is None:
        return None
    candidate = Path(path).resolve()
    if candidate.suffix.casefold() == ".$et":
        edb = candidate.with_suffix(".EDB")
        if edb.is_file():
            return edb
    return candidate if candidate.is_file() else None


def artifact_record(path: str | Path) -> dict[str, Any]:
    """Describe and hash one output file or directory tree."""

    target = Path(path).resolve()
    if not target.exists():
        return {"path": str(target), "exists": False}
    if target.is_file():
        return {
            "path": str(target),
            "exists": True,
            "kind": "file",
            "size_bytes": target.stat().st_size,
            "sha256": sha256_file(target),
        }
    files = sorted(item for item in target.rglob("*") if item.is_file())
    digest = hashlib.sha256()
    total = 0
    for item in files:
        relative = item.relative_to(target).as_posix()
        file_hash = sha256_file(item)
        size = item.stat().st_size
        digest.update(f"{relative}\0{size}\0{file_hash}\n".encode("utf-8"))
        total += size
    return {
        "path": str(target),
        "exists": True,
        "kind": "directory",
        "file_count": len(files),
        "size_bytes": total,
        "sha256": digest.hexdigest(),
    }


def git_state(root: str | Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return commit and dirty-content identity without requiring a clean tree."""

    directory = Path(root)

    def command(*args: str) -> bytes:
        try:
            return subprocess.check_output(["git", *args], cwd=directory, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError):
            return b""

    commit = command("rev-parse", "HEAD").decode("utf-8", "replace").strip() or None
    status = command("status", "--porcelain=v1")
    diff = command("diff", "--binary", "HEAD")
    untracked_names = tuple(
        os.fsdecode(raw_name)
        for raw_name in command("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if raw_name
    )
    content_digest = hashlib.sha256()
    content_digest.update(b"TRACKED-DIFF\0")
    content_digest.update(diff)
    untracked_files: list[dict[str, Any]] = []
    for relative_name in sorted(untracked_names):
        target = directory / relative_name
        if not target.is_file():
            continue
        size = target.stat().st_size
        file_hash = sha256_file(target)
        normalized_name = Path(relative_name).as_posix()
        content_digest.update(
            f"\0UNTRACKED\0{normalized_name}\0{size}\0{file_hash}".encode(
                "utf-8",
                "surrogateescape",
            )
        )
        untracked_files.append(
            {
                "path": normalized_name,
                "size_bytes": size,
                "sha256": file_hash,
            }
        )
    return {
        "commit": commit,
        "dirty": bool(status.strip()),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "content_sha256": content_digest.hexdigest(),
        "untracked_files": untracked_files,
    }


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value.resolve())
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RunManifest:
    """Collect one CLI run and maintain the cross-run validity registry."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        registry_dir: str | Path,
        config: Any,
        arguments: Mapping[str, Any],
        run_id: str | None = None,
    ) -> None:
        self.run_id = run_id or create_run_id()
        self.output_dir = Path(output_dir).resolve()
        self.registry_dir = Path(registry_dir).resolve()
        self.config = jsonable(config)
        self.arguments = jsonable(arguments)
        self.git = git_state()
        self.started_at = datetime.now(timezone.utc).isoformat()

    def finalize(
        self,
        *,
        status: str,
        summary: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> Path:
        summary_data = jsonable(summary or {})
        source_path = self.arguments.get("source_model") or self.arguments.get("expected_model")
        actual_path = summary_data.get("model")
        source_file = resolve_model_file(source_path)
        actual_file = resolve_model_file(actual_path)
        input_sha = summary_data.get("model_input_sha256")
        if input_sha is None and source_file:
            input_sha = sha256_file(source_file)

        environment = summary_data.get("environment", {})
        identity = {
            "model_input_sha256": input_sha,
            "snapshot_input_sha256": _optional_file_hash(self.arguments.get("snapshot_input")),
            "selection": self.arguments.get("selection"),
            "confirmed_multi_section_spans_sha256": _optional_file_hash(
                self.arguments.get("confirmed_multi_section_spans")
            ),
            "excluded_writeback_objects_sha256": _optional_file_hash(self.arguments.get("exclude_writeback_objects")),
            "configuration": self.config,
            "git": self.git,
            "algorithms": ALGORITHM_VERSIONS,
            "etabs_version": environment.get("etabs_version"),
            "api": environment.get("api"),
            "design_standard": summary_data.get("design_standard"),
            "export_units": summary_data.get("export_units"),
        }
        input_fingerprint = fingerprint(identity)
        outputs = {name: artifact_record(path) for name, path in (summary_data.get("outputs") or {}).items() if path}
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": status,
            "started_at_utc": self.started_at,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "validity": {
                "state": "CURRENT" if status == "SUCCESS" else "INVALID",
                "input_fingerprint": input_fingerprint,
                "superseded_by_run_id": None,
                "reason": None if status == "SUCCESS" else "RUN_FAILED",
            },
            "models": {
                "source": _model_record(source_file),
                "actual": _model_record(actual_file),
                "input_sha256": input_sha,
            },
            "environment": environment,
            "data_source": summary_data.get("data_source"),
            "git": self.git,
            "algorithms": ALGORITHM_VERSIONS,
            "configuration": self.config,
            "arguments": self.arguments,
            "counts": _counts(summary_data),
            "return_codes": _return_codes(summary_data),
            "outputs": outputs,
            "error": error,
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        local_path = self.output_dir / f"run_manifest_{self.run_id}.json"
        registry_path = self.registry_dir / f"run_manifest_{self.run_id}.json"
        manifest["manifest_paths"] = {
            "local": str(local_path),
            "registry": str(registry_path),
        }
        if status == "SUCCESS":
            _supersede_incompatible(self.registry_dir, manifest)
        _write_json(local_path, manifest)
        _write_json(registry_path, manifest)
        return local_path


def _optional_file_hash(path: Any) -> str | None:
    if not path:
        return None
    target = Path(path)
    return sha256_file(target) if target.is_file() else None


def _model_record(path: Path | None) -> dict[str, Any] | None:
    return artifact_record(path) if path else None


def _counts(summary: Mapping[str, Any]) -> dict[str, Any]:
    names = (
        "beams",
        "beam_lines",
        "spans",
        "primary_spans",
        "secondary_spans",
        "requires_split_objects",
        "requires_split_actions",
        "multi_section_spans",
        "unconfirmed_multi_section_spans",
        "section_review_actions",
        "blocking_topology_diagnostics",
        "record_count",
    )
    return {name: summary.get(name) for name in names if name in summary}


def _return_codes(summary: Mapping[str, Any]) -> dict[str, Any]:
    writeback = summary.get("writeback") or {}
    review = summary.get("section_review_groups") or {}
    return {
        key: value
        for key, value in {
            "analysis": writeback.get("analysis_status"),
            "design": writeback.get("design_status"),
            "save": writeback.get("save_status", review.get("save_status")),
        }.items()
        if value is not None
    }


def _supersede_incompatible(registry_dir: Path, current: Mapping[str, Any]) -> None:
    current_id = current["run_id"]
    current_fingerprint = current["validity"]["input_fingerprint"]
    for path in registry_dir.glob("run_manifest_*.json"):
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        validity = previous.get("validity") or {}
        if previous.get("run_id") == current_id or validity.get("state") != "CURRENT":
            continue
        if validity.get("input_fingerprint") == current_fingerprint:
            continue
        validity.update(
            {
                "state": "SUPERSEDED",
                "superseded_by_run_id": current_id,
                "reason": "INPUT_OR_ALGORITHM_FINGERPRINT_CHANGED",
            }
        )
        previous["validity"] = validity
        _write_json(path, previous)
        local = (previous.get("manifest_paths") or {}).get("local")
        if local and Path(local).is_file():
            _write_json(Path(local), previous)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
