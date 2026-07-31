# ETABS 22/23 Adapter

## Current Boundary

The repository supports running ETABS 22 and 23 x64 instances through pythonnet and the matching `ETABSv1.dll`. The implementation is `src/core/etabs_connector.py`; the authoritative reference is `docs/reference/ETABS_OAPI.md`.

```python
assembly_module = "ETABSv1"
prog_id = "CSI.ETABS.API.ETABSObject"
supported_major_versions = {22, 23}
```

Construct `cHelper(Helper())`. Use `GetObjectProcess(prog_id, pid)` for deterministic CLI attachment and `GetObject(prog_id)` only when the active instance is already unique. Require non-null application and `SapModel`, then compare `GetModelFilename(True)` with the expected model.

## Version Compatibility

Try `sap.GetVersion()` first. ETABS 22 may require `sap.GetVersion("", 0.0)`; fall back only on `TypeError`. Parse and enforce the major version while retaining raw values. Keep both signatures covered in `tests/unit/test_connector.py`.

## Project Conventions

- Use `api_values` for `(ret, out...)` methods and import ETABS enums only in adapters/providers.
- Run geometry, design extraction, and writeback calculations under `eUnits.N_mm_C` via `temporary_present_units`.
- Use `GeometryExtractor`; it prefers validated `FrameObj.GetAllFrames` results and has a per-object fallback.
- Use `EtabsResultProvider` for `GetSummaryResultsBeam` and `_2`; do not duplicate positional decoding.
- Preserve design warnings/errors separately from the OAPI status.

Key paths are `src/core/etabs_connector.py`, `src/core/geometry_extractor.py`, `src/design/etabs_result.py`, `src/core/model_writeback.py`, `src/cli.py`, and `tests/unit/test_connector.py`.

## Mutation

Use `EditFrame.ChangeConnectivity` for I/J exchange; `SetLocalAxes` is not equivalent. Use GUID-derived temporary unique names before final `FrameObj.ChangeName`. Follow `docs/decisions/ADR-0002_SAFE_WORKING_COPY_WRITEBACK.md`. Follow ADR-0003 for public-OAPI-to-project-SQLite snapshots; internal ETABS SQLite classes are not supported contracts.

## Another ETABS Major

Do not only add its number. Validate its DLL/module, helper attachment, version overload, model filename, units, required bulk/result signatures, analysis/design state, and writeback audit. Add fake regressions and recorded real-host validation before advertising support.
