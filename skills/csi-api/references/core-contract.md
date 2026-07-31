# CSI OAPI Core Contract

## Architecture

Use four layers: a session loads/attaches and restores state; a product adapter translates namespaces, enums, signatures, and capabilities; a domain port emits normalized geometry/design/audit values; workflows consume those values without importing product assemblies.

Do not invent a large common interface. Extract shared protocols only after two product adapters prove identical operations and semantics.

## Call Results

Most pythonnet CSI methods return `(integer_status, out...)`. Current ETABS code must use `src.core.etabs_connector.api_values`:

```python
values = api_values(sap.FrameObj.GetNameList(0, None), "FrameObj.GetNameList")
```

Require an integer status equal to zero and retain method/object context in errors. Never silently discard the status. Document direct-return exceptions; current examples include `GetModelFilename(True)`, `GetPresentUnits()`, and Boolean availability methods.

Pass initialized values of the expected .NET type for `out/ref` parameters. Use product enum objects rather than guessed integers. Isolate overload fallbacks inside adapters and catch only the overload mismatch, normally `TypeError`.

## Session And Units

- Require x64 Python, x64 host, and a matching major-version API assembly.
- Load an absolute configured DLL path with `clr.AddReference`.
- Prefer process-specific attachment when multiple hosts may run, then verify the model filename.
- Keep the application object alive. Do not close a host the adapter did not create.
- Treat present units as session state: capture, set, verify, execute, and restore in `finally`.
- Do not save while temporary display units are active unless that change is intentional.

## Read Paths

- Prefer documented bulk calls and verify all array lengths against the reported count.
- Keep per-object calls as compatibility/diagnostic fallbacks, not duplicate business paths.
- Store stable GUID identity separately from mutable names.
- Treat `ret == 0` as API transport success only; preserve design warnings, errors, program-determined flags, and missing results.
- Include product/API version, model identity, units, design code, and schema version in offline snapshots.

## Mutation Contract

Default to read-only. Create and hash a non-overwriting working copy; snapshot affected identity and attributes; generate a serializable plan; require explicit opt-in; check every mutation result; re-read by stable identity; save/reopen and audit; rerun invalidated analysis/design.

Never mutate an original model, report full success after silently skipping unsafe objects, or automate undocumented internal APIs.

## Testing

Use fakes for overload differences, nonzero codes, malformed lengths, enum/ref placeholders, unit restoration, and missing capabilities. Require explicit product/process/model selection for installed-host integration tests. Record product and assembly versions, model hash, operation, and result as validation evidence.
