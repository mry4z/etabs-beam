"""Stage the complete lookup asset tree with JSONL indices recompressed as XZ."""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import lzma
import shutil
from pathlib import Path


def _convert(source: Path, destination: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    raw_bytes = 0
    with gzip.open(source, "rb") as reader, lzma.open(
        destination, "wb", preset=6
    ) as writer:
        while block := reader.read(1024 * 1024):
            writer.write(block)
            digest.update(block)
            raw_bytes += len(block)

    expected = digest.hexdigest()
    check = hashlib.sha256()
    checked_bytes = 0
    with lzma.open(destination, "rb") as reader:
        while block := reader.read(1024 * 1024):
            check.update(block)
            checked_bytes += len(block)
    if checked_bytes != raw_bytes or check.hexdigest() != expected:
        raise ValueError(f"XZ content verification failed: {source}")
    return {
        "bytes": destination.stat().st_size,
        "uncompressed_bytes": raw_bytes,
        "sha256_uncompressed": expected,
    }


def _manifest_paths(root: Path) -> list[tuple[Path, Path]]:
    root = root.resolve()
    sources: dict[Path, Path] = {}
    standard = root / "standard_indices" / "manifest.json"
    if standard.is_file():
        manifest = json.loads(standard.read_text(encoding="utf-8"))
        manifest_roots = [(root / "standard_indices", manifest)]
    else:
        manifest_roots = []
    contexts = root / "context_indices_manifest.json"
    if contexts.is_file():
        manifest = json.loads(contexts.read_text(encoding="utf-8"))
        manifest_roots.append((root, manifest))
    if len(manifest_roots) != 2:
        raise FileNotFoundError(f"Both lookup index manifests are required in {root}")

    for manifest_root, manifest in manifest_roots:
        for item in manifest.get("files", []):
            relative = Path(item["path"])
            if relative.is_absolute():
                raise ValueError(f"Manifest index path must be relative: {item['path']!r}")
            source = (manifest_root / relative).resolve(strict=True)
            try:
                source.relative_to(root)
            except ValueError as error:
                raise ValueError(f"Manifest index path escapes asset directory: {item['path']!r}") from error
            if not source.is_file() or not source.name.endswith(".jsonl.gz"):
                raise ValueError(f"Manifest index must name a JSONL gzip shard: {source}")
            if source in sources:
                raise ValueError(f"Duplicate manifest index path: {source}")
            sources[source] = source.relative_to(root).with_suffix(".xz")

    actual = {path.resolve() for path in root.rglob("*.jsonl.gz") if path.is_file()}
    if set(sources) != actual:
        missing = sorted(str(path.relative_to(root)) for path in actual - set(sources))
        unlisted = sorted(str(path.relative_to(root)) for path in set(sources) - actual)
        raise ValueError(f"Manifest index coverage mismatch: unlisted={missing}, missing={unlisted}")
    return [(source, relative) for source, relative in sorted(sources.items())]


def _update_manifest(
    path: Path, root: Path, conversions: dict[str, dict[str, int | str]]
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["compression"] = "XZ (LZMA preset 6); JSONL index shards only"
    for item in data["files"]:
        old_path = item["path"]
        if old_path.endswith(".jsonl.gz"):
            new_path = old_path[:-3] + ".xz"
            staged_path = (path.parent / new_path).relative_to(root).as_posix()
            metadata = conversions[staged_path]
            item["path"] = new_path
            item.update(metadata)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stage(source_dir: Path, output_dir: Path, workers: int) -> dict[str, object]:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Lookup asset source does not exist: {source_dir}")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to replace staged lookup assets: {output_dir}")
    pairs = _manifest_paths(source_dir)
    if not pairs:
        raise ValueError(f"No manifest-listed JSONL index shards in {source_dir}")
    indexed_sources = {source for source, _ in pairs}

    def ignore_index_shards(directory: str, names: list[str]) -> list[str]:
        current = Path(directory).resolve()
        return [name for name in names if (current / name).resolve() in indexed_sources]

    shutil.copytree(source_dir, output_dir, ignore=ignore_index_shards)
    destination_pairs = [(source, output_dir / relative) for source, relative in pairs]
    original_total = sum(source.stat().st_size for source, _ in destination_pairs)
    conversions: dict[str, dict[str, int | str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_convert, source, destination): (source, destination)
            for source, destination in destination_pairs
        }
        for future in concurrent.futures.as_completed(futures):
            _source, destination = futures[future]
            conversions[destination.relative_to(output_dir).as_posix()] = future.result()

    _update_manifest(output_dir / "standard_indices" / "manifest.json", output_dir, conversions)
    _update_manifest(output_dir / "context_indices_manifest.json", output_dir, conversions)
    compressed_total = sum((output_dir / name).stat().st_size for name in conversions)
    return {
        "source": str(source_dir),
        "staged": str(output_dir),
        "index_shards": len(conversions),
        "gzip_bytes": original_total,
        "xz_bytes": compressed_total,
        "reduction_percent": round((1 - compressed_total / original_total) * 100, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Original lookup_assets directory")
    parser.add_argument("output", type=Path, help="New staging directory")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    print(json.dumps(stage(args.source, args.output, args.workers), indent=2))


if __name__ == "__main__":
    main()
