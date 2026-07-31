"""Locate an installed ETABS API DLL on machines where the path is unknown.

The plugin always passes the host assembly path explicitly, so this module
serves standalone CLI / portable-workflow runs that only have the config
placeholder ``dll_path: "ETABSv1.dll"``.  Search is limited to a fixed set of
well-known directories (no recursive tree walk) so it is safe on any disk.
"""

from __future__ import annotations

from pathlib import Path

# Newer major version first so a machine with both prefers ETABS 23.
ETABS_VERSION_DIRECTORIES = ("ETABS 23", "ETABS 22")
SEARCH_DRIVE_LETTERS = "CDEFGH"


def candidate_directories() -> list[Path]:
    """Return existing ``<drive>:/Program Files*/Computers and Structures`` dirs."""
    directories: list[Path] = []
    for letter in SEARCH_DRIVE_LETTERS:
        root = Path(f"{letter}:\\")
        if not root.is_dir():
            continue
        for base in (root / "Program Files", root / "Program Files (x86)"):
            cs_directory = base / "Computers and Structures"
            if cs_directory.is_dir():
                directories.append(cs_directory)
    return directories


def search_etabs_dll_path() -> Path | None:
    """Find the first existing ``ETABSv1.dll`` under known installation roots."""
    for version in ETABS_VERSION_DIRECTORIES:
        for directory in candidate_directories():
            candidate = directory / version / "ETABSv1.dll"
            if candidate.is_file():
                return candidate
    return None
