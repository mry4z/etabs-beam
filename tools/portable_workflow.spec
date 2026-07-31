# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


workspace_root = Path(SPECPATH).resolve().parent
pythonnet_datas, pythonnet_binaries, pythonnet_hidden = collect_all("pythonnet")
clr_loader_datas, clr_loader_binaries, clr_loader_hidden = collect_all("clr_loader")

a = Analysis(
    [str(workspace_root / "tools" / "portable_workflow_entry.py")],
    pathex=[str(workspace_root)],
    binaries=pythonnet_binaries + clr_loader_binaries,
    datas=pythonnet_datas + clr_loader_datas,
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
