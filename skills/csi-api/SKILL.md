---
name: csi-api
description: Implement, review, debug, or extend Python integrations with the Computers and Structures, Inc. (CSI) Open API in this repository. Use for ETABS 22/23 attachment, pythonnet out/ref handling, version and unit compatibility, geometry or design-result extraction, guarded model writeback, CSI API tests, and architecture work that must preserve adapters for future SAP2000 and SAFE support.
---

# CSI API

Use the repository's tested CSI OAPI boundary instead of scattering product-specific COM/.NET calls through domain code. Treat ETABS 22 and 23 as supported now; treat SAP2000 and SAFE as extension targets until their adapters and real-host tests exist.

## Start Here

1. Read `references/core-contract.md` for every CSI API task.
2. Read `references/etabs-22-23.md` when connecting to ETABS or changing an ETABS call.
3. Read `references/extension-guide.md` when adding shared abstractions, SAP2000, SAFE, or another CSI version.
4. Inspect the implementation and tests named in those references before editing. Repository code and accepted ADRs override examples here if they diverge.

## Workflow

1. Classify the work as connection, read, analysis/design, mutation, or product extension.
2. Keep raw OAPI objects inside a product adapter. Return normalized project values to domain code.
3. Load the exact product assembly from an absolute configured path. Match its major version to the running host.
4. Verify product, major version, model identity, process architecture, and units before extracting or mutating data.
5. Route status-code-bearing calls through the checked result helper. Document and test genuine direct-return exceptions.
6. Prefer documented bulk public OAPI calls. Add a tested object fallback only where signatures require it.
7. Preserve design errors and warnings as engineering data even when the OAPI return code is zero.
8. Default to read-only. For mutation, require a non-overwriting working copy, reviewable plan, explicit opt-in, post-write and reopen verification, and audit evidence.
9. Cover signature and failure differences with fakes. Run real-host tests only against an explicitly selected disposable model.

## Boundaries

- Use `SapModel` only as CSI's conventional model-root name; do not assume ETABS, SAP2000, and SAFE interfaces are identical.
- Keep product identifiers, assembly/module names, helper types, version policies, and capabilities in product adapters or descriptors.
- Share only proven invariants: checked returns, session lifecycle, unit restoration, model identity, mutation gates, and normalized domain ports.
- Do not use undocumented internal assemblies or private database formats as production contracts.
- Do not claim SAP2000 or SAFE support until product-specific connection, version, units, feature, and mutation tests exist.

## Completion Checks

- Keep ETABS 22 ref-argument and ETABS 23 no-argument version signatures covered.
- Verify nonzero API codes fail with call context.
- Verify temporary units restore after success and failure.
- Keep product-specific imports out of domain modules.
- Keep mutations explicitly gated and fail closed.
- Update references and tests when adding a supported product, major version, or capability.
