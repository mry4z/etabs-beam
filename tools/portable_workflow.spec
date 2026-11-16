# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_all


workspace_root = Path(SPECPATH).resolve().parent
lookup_assets = Path(os.environ.get(
    "BEAM_LOOKUP_ASSETS_BUILD_DIR",
    str(workspace_root / "src" / "design" / "lookup_assets"),
)).resolve()
if not lookup_assets.is_dir():
    raise FileNotFoundError(f"Lookup asset directory not found: {lookup_assets}")
pythonnet_datas, pythonnet_binaries, pythonnet_hidden = collect_all("pythonnet")
clr_loader_datas, clr_loader_binaries, clr_loader_hidden = collect_all("clr_loader")

a = Analysis(
    [str(workspace_root / "tools" / "portable_workflow_entry.py")],
    pathex=[str(workspace_root)],
    binaries=pythonnet_binaries + clr_loader_binaries,
    datas=pythonnet_datas + clr_loader_datas + [
        (
            str(workspace_root / "src" / "design" / "policies" / "drawing_20260924_v1.json"),
            "src/design/policies",
        ),
        (str(lookup_assets), "src/design/lookup_assets"),
    ],
    hiddenimports=[
        "clr",
        "pythonnet",
        "clr_loader",
        *pythonnet_hidden,
        *clr_loader_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "black",
        "flake8",
        "IPython",
        "jupyter",
        "mypy",
        "pytest",
        "pytest_cov",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BeamGenerator.Workflow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BeamGenerator.Workflow",
)
