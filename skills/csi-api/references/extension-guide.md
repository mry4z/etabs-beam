# SAP2000 And SAFE Extension Guide

## Capability Model

Track support by product and major version for: active/process attachment, create/open ownership, model identity, units, bulk frame geometry, design summaries, selection/assignments, connectivity/name mutation, and save/reopen audit. Mark each capability `supported`, `unsupported`, or `unverified`; fail before a workflow whose required capability is unavailable.

## Adapter Shape

Keep a small descriptor/session contract and product-specific implementations:

```python
@dataclass(frozen=True)
class CsiProductDescriptor:
    product: str
    assembly_module: str
    prog_id: str
    supported_majors: frozenset[int]

class CsiSession(Protocol):
    @property
    def model(self) -> Any: ...
    def version_info(self) -> VersionInfo: ...
    def model_filename(self) -> Path: ...
    def temporary_present_units(self, units: Any) -> ContextManager[Any]: ...
```

Keep geometry, design, and mutation in separate capability protocols until their semantics are proven common.

## Add SAP2000 Or SAFE

1. Inspect the installed product's public documentation and matching assembly; do not guess namespace, ProgID, helper, enums, or signatures from ETABS.
2. Add a product adapter for identifiers and overload fallbacks.
3. Verify x64 loading, lifecycle ownership, version/model identity, and unit restoration.
4. Normalize only capabilities needed by a real workflow.
5. Add product-specific fake tests and opted-in real-host tests using disposable models.
6. Establish product-specific working-copy, save/reopen, stable-identity, and audit procedures before enabling mutation.
7. Update the capability matrix and references only after evidence exists.

Reuse checked result handling only when tuple conventions match. Reuse lifecycle, restoration, audit, and fail-closed planning concepts. Never reuse ETABS enums, design-array positions, object-type integers, schemas, or mutation equivalence rules without evidence.

## Evolve Current Code

Keep the accurate name `EtabsConnector` until a second adapter exists. Then extract only proven-neutral exceptions/result parsing, introduce a small protocol, retain `EtabsConnector` as implementation or compatibility facade, move imports behind adapters, and keep all ETABS 22/23 tests passing before adding the new product suite.
