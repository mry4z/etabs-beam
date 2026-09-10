"""Checked preservation of the complete ETABS UI selection (not only beams)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.etabs_connector import EtabsApiError, api_values, model_paths_match

# ETABS 22/23 expose these object APIs on cSapModel.  Cable (3) and Solid
# (6) can appear in GetSelected's object-type enum but do not have public
# cSapModel object properties in the supported assemblies; fail closed when
# either is present instead of guessing a recovery entry point.
OBJECT_APIS = {1: "PointObj", 2: "FrameObj", 4: "TendonObj", 5: "AreaObj", 7: "LinkObj"}


class SelectionRestoreModelError(EtabsApiError):
    """The model identity changed or became unreadable during recovery."""


def read_selection(sap: Any) -> tuple[tuple[int, str], ...]:
    values = api_values(sap.SelectObj.GetSelected(0, None, None), "SelectObj.GetSelected")
    if len(values) != 3:
        raise EtabsApiError("SelectObj.GetSelected: unexpected output count")
    count, raw_types, raw_names = values
    types, names = tuple(raw_types or ()), tuple(raw_names or ())
    if not isinstance(count, int) or isinstance(count, bool) or count < 0 or len(types) != count or len(names) != count:
        raise EtabsApiError("SelectObj.GetSelected: inconsistent array lengths")
    if any(not isinstance(kind, int) or isinstance(kind, bool) for kind in types):
        raise EtabsApiError("SelectObj.GetSelected: invalid object type")
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise EtabsApiError("SelectObj.GetSelected: invalid object name")
    objects = tuple(sorted(zip(types, names)))
    if len(set(objects)) != count:
        raise EtabsApiError("SelectObj.GetSelected: duplicate object identities")
    return objects


@dataclass(frozen=True)
class EtabsSelectionSnapshot:
    objects: tuple[tuple[int, str], ...]
    model_path: str | None = None

    @classmethod
    def capture(cls, sap: Any) -> EtabsSelectionSnapshot:
        filename = getattr(sap, "GetModelFilename", None)
        model_path: str | None = None
        if callable(filename):
            try:
                model_path = str(filename(True)).strip()
            except Exception as exc:
                raise EtabsApiError(
                    f"SELECTION_RESTORE_MODEL_IDENTITY_READ_FAILED: {type(exc).__name__}: {exc}"
                ) from exc
            if not model_path:
                raise EtabsApiError("SELECTION_RESTORE_MODEL_IDENTITY_READ_FAILED: empty model path")
        objects = read_selection(sap)
        # Refuse to switch Towers if any selected type cannot be restored.
        if not callable(getattr(sap.SelectObj, "ClearSelection", None)):
            raise EtabsApiError("SelectObj.ClearSelection: unavailable")
        for kind, name in objects:
            api = getattr(sap, OBJECT_APIS.get(kind, ""), None)
            if not callable(getattr(api, "SetSelected", None)):
                raise EtabsApiError(f"Selection restore unsupported: type={kind}, name={name}")
        if model_path is not None:
            try:
                after_path = str(filename(True)).strip()
            except Exception as exc:
                raise EtabsApiError(
                    f"SELECTION_RESTORE_MODEL_IDENTITY_READ_FAILED: {type(exc).__name__}: {exc}"
                ) from exc
            if not after_path:
                raise EtabsApiError("SELECTION_RESTORE_MODEL_IDENTITY_READ_FAILED: empty model path")
            if not model_paths_match(model_path, after_path):
                raise EtabsApiError(
                    "SELECTION_RESTORE_MODEL_CHANGED: model changed while capturing selection"
                )
        return cls(objects, model_path)

    def restore_with_audit(self, sap: Any) -> SelectionRestoreResult:
        """Restore the snapshot and return a checked strategy/audit record.

        ``PreviousSelection`` is a useful ETABS batch operation, but its
        history is not a contract for the selection captured by this object.
        It is therefore accepted only after a complete ``GetSelected`` set
        comparison.  A mismatch, a non-zero return code, or an unavailable
        method falls back to the checked per-object API from the immutable
        snapshot.
        """

        self._verify_model(sap)
        diagnostics: list[str] = []
        initial: tuple[tuple[int, str], ...] | None = None
        try:
            initial = self._guarded_read_selection(sap)
        except SelectionRestoreModelError:
            raise
        except Exception as exc:
            diagnostics.append(f"SELECTION_RESTORE_INITIAL_READ_FAILED: {type(exc).__name__}: {exc}")
        if initial == self.objects:
            return SelectionRestoreResult(
                actual=self.objects,
                strategy="noop",
                verified=True,
                diagnostics=tuple(diagnostics),
            )

        previous_method = getattr(getattr(sap, "SelectObj", None), "PreviousSelection", None)
        previous_code: int | None = None
        previous_verified: bool | None = None
        if callable(previous_method):
            try:
                raw_result = self._guarded_raw_call(sap, previous_method)
                values = tuple(raw_result) if isinstance(raw_result, tuple) else (raw_result,)
                if len(values) != 1 or not isinstance(values[0], int) or isinstance(values[0], bool):
                    raise EtabsApiError(
                        f"SelectObj.PreviousSelection: invalid return value {values!r}"
                    )
                previous_code = int(values[0])
                if previous_code != 0:
                    raise EtabsApiError(
                        f"SelectObj.PreviousSelection: ETABS API 返回错误码 {previous_code}。"
                    )
                previous_actual = self._guarded_read_selection(sap)
                previous_verified = previous_actual == self.objects
                if previous_verified:
                    return SelectionRestoreResult(
                        actual=previous_actual,
                        strategy="previous_selection",
                        previous_selection_return_code=previous_code,
                        previous_selection_verified=True,
                        verified=True,
                        diagnostics=tuple(diagnostics),
                    )
                diagnostics.append(
                    "SELECTION_RESTORE_PREVIOUS_MISMATCH: PreviousSelection 返回成功，"
                    f"但完整集合不符；expected={self.objects!r}, actual={previous_actual!r}。"
                )
            except SelectionRestoreModelError:
                # A model guard failure is not a failed PreviousSelection
                # attempt.  Do not let the broad recovery fallback select old
                # object names in a replacement model.
                raise
            except Exception as exc:
                diagnostics.append(
                    f"SELECTION_RESTORE_PREVIOUS_FAILED: {type(exc).__name__}: {exc}"
                )
        else:
            diagnostics.append("SELECTION_RESTORE_PREVIOUS_UNAVAILABLE: SelectObj.PreviousSelection")

        clear_called = False
        set_selected_calls = 0
        try:
            self._verify_model(sap)
            from ETABSv1 import eItemType

            self._guarded_status_call(
                sap,
                sap.SelectObj.ClearSelection,
                "SelectObj.ClearSelection(restore)",
            )
            clear_called = True
        except SelectionRestoreModelError:
            raise
        except Exception as exc:
            diagnostics.append(f"SELECTION_RESTORE_CLEAR_FAILED: {type(exc).__name__}: {exc}")
            actual = self._read_selection_or_empty(sap, diagnostics)
            result = SelectionRestoreResult(
                actual=actual,
                strategy="failed",
                previous_selection_return_code=previous_code,
                previous_selection_verified=previous_verified,
                clear_selection_called=clear_called,
                verified=False,
                diagnostics=tuple(diagnostics),
            )
            return result

        diagnostics.append(
            "SELECTION_RESTORE_STAGE: object_fallback start; "
            f"objects={len(self.objects)}; previous_selection_verified={previous_verified!r}"
        )
        for kind, name in self.objects:
            set_selected_calls += 1
            try:
                api = getattr(sap, OBJECT_APIS[kind])
                self._guarded_status_call(
                    sap,
                    lambda api=api, name=name: api.SetSelected(name, True, eItemType.Objects),
                    f"{OBJECT_APIS[kind]}.SetSelected({name})",
                )
            except SelectionRestoreModelError:
                raise
            except Exception as exc:
                diagnostics.append(
                    f"SELECTION_RESTORE_OBJECT_FAILED: type={kind}, name={name}: "
                    f"{type(exc).__name__}: {exc}"
                )
        actual = self._read_selection_or_empty(sap, diagnostics)
        verified = not any(
            item.startswith(("SELECTION_RESTORE_OBJECT_FAILED", "SELECTION_RESTORE_VERIFY_READ_FAILED"))
            for item in diagnostics
        )
        if actual != self.objects:
            diagnostics.append(
                "SELECTION_RESTORE_VERIFY_FAILED: 完整选择集不一致；"
                f"expected={self.objects!r}, actual={actual!r}。"
            )
            verified = False
        return SelectionRestoreResult(
            actual=actual,
            strategy="object_fallback" if clear_called else "failed",
            previous_selection_return_code=previous_code,
            previous_selection_verified=previous_verified,
            clear_selection_called=clear_called,
            set_selected_calls=set_selected_calls,
            verified=verified,
            diagnostics=tuple(diagnostics),
        )

    def _guarded_raw_call(self, sap: Any, method: Any) -> Any:
        self._verify_model(sap)
        try:
            result = method()
        except Exception:
            # The call may have changed the active model before raising.
            self._verify_model(sap)
            raise
        self._verify_model(sap)
        return result

    def _guarded_status_call(self, sap: Any, method: Any, context: str) -> tuple[Any, ...]:
        result = self._guarded_raw_call(sap, method)
        return api_values(result, context)

    def _guarded_read_selection(self, sap: Any) -> tuple[tuple[int, str], ...]:
        self._verify_model(sap)
        try:
            result = read_selection(sap)
        except Exception:
            # A failed read is not allowed to hide an identity change and
            # trigger writes into a model that may have replaced the original.
            self._verify_model(sap)
            raise
        self._verify_model(sap)
        return result

    def _read_selection_or_empty(
        self,
        sap: Any,
        diagnostics: list[str],
    ) -> tuple[tuple[int, str], ...]:
        try:
            return self._guarded_read_selection(sap)
        except SelectionRestoreModelError:
            raise
        except Exception as exc:
            diagnostics.append(f"SELECTION_RESTORE_VERIFY_READ_FAILED: {type(exc).__name__}: {exc}")
            return ()

    def restore(self, sap: Any) -> tuple[tuple[int, str], ...]:
        result = self.restore_with_audit(sap)
        if not result.verified:
            raise EtabsApiError("; ".join(result.diagnostics) or "SELECTION_RESTORE_VERIFY_FAILED")
        return result.actual

    def _verify_model(self, sap: Any) -> None:
        if self.model_path is None:
            return
        filename = getattr(sap, "GetModelFilename", None)
        if not callable(filename):
            raise SelectionRestoreModelError("SELECTION_RESTORE_MODEL_IDENTITY_UNAVAILABLE: GetModelFilename")
        try:
            current_path = str(filename(True))
        except Exception as exc:
            raise SelectionRestoreModelError(
                f"SELECTION_RESTORE_MODEL_IDENTITY_READ_FAILED: {type(exc).__name__}: {exc}"
            ) from exc
        if not model_paths_match(self.model_path, current_path):
            raise SelectionRestoreModelError(
                "SELECTION_RESTORE_MODEL_CHANGED: refusing to select objects in another model"
            )


@dataclass(frozen=True)
class SelectionRestoreResult:
    """Auditable outcome of a selection restore attempt."""

    actual: tuple[tuple[int, str], ...]
    strategy: str
    previous_selection_return_code: int | None = None
    previous_selection_verified: bool | None = None
    clear_selection_called: bool = False
    set_selected_calls: int = 0
    verified: bool = False
    diagnostics: tuple[str, ...] = ()

    def summary(self) -> str:
        previous_code = "none" if self.previous_selection_return_code is None else str(self.previous_selection_return_code)
        previous_verified = "none" if self.previous_selection_verified is None else str(int(self.previous_selection_verified))
        return (
            "SELECTION_RESTORE_AUDIT: "
            f"strategy={self.strategy}; verified={int(self.verified)}; "
            f"previous_status={previous_code}; previous_verified={previous_verified}; "
            f"clear_calls={int(self.clear_selection_called)}; "
            f"set_selected_calls={self.set_selected_calls}"
        )
