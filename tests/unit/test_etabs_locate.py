"""Unit tests for ETABS API DLL auto-discovery."""

from __future__ import annotations

from pathlib import Path

import src.etabs_locate as locate


def test_search_prefers_etabs_23(tmp_path: Path, monkeypatch) -> None:
    cs = tmp_path / "Program Files" / "Computers and Structures"
    dll23 = cs / "ETABS 23" / "ETABSv1.dll"
    dll22 = cs / "ETABS 22" / "ETABSv1.dll"
    dll23.parent.mkdir(parents=True)
    dll23.write_bytes(b"x")
    dll22.parent.mkdir(parents=True)
    dll22.write_bytes(b"x")
    monkeypatch.setattr(locate, "candidate_directories", lambda: [cs])
    assert locate.search_etabs_dll_path() == dll23


def test_search_returns_none_when_missing(tmp_path: Path, monkeypatch) -> None:
    cs = tmp_path / "Program Files" / "Computers and Structures"
    cs.mkdir(parents=True)
    monkeypatch.setattr(locate, "candidate_directories", lambda: [cs])
    assert locate.search_etabs_dll_path() is None


def test_candidate_directories_skips_missing_drives(tmp_path: Path, monkeypatch) -> None:
    cs_dirs = [tmp_path / "PF", tmp_path / "PF64"]
    monkeypatch.setattr(locate, "candidate_directories", lambda: cs_dirs)
    # Only ETABS 22 present; search should still find it.
    dll = cs_dirs[0] / "ETABS 22" / "ETABSv1.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"x")
    assert locate.search_etabs_dll_path() == dll
