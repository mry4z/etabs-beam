"""Safe pythonnet attachment to supported ETABS 22/23 instances."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SUPPORTED_ETABS_MAJOR_VERSIONS = frozenset({22, 23})


class EtabsConnectionError(RuntimeError):
    """Raised when ETABS cannot be loaded or attached."""


class EtabsApiError(RuntimeError):
    """Raised when an ETABS OAPI method reports a non-zero return code."""


def api_values(result: Any, context: str) -> tuple[Any, ...]:
    """Unpack pythonnet OAPI output and raise a helpful error on API failure.

    ETABS methods return an integer status followed by their ``out`` values when
    invoked through pythonnet.
    """
    values = tuple(result) if isinstance(result, tuple) else (result,)
    if not values:
        raise EtabsApiError(f"{context}: API 未返回结果。")
    if not isinstance(values[0], int):
        raise EtabsApiError(f"{context}: 无法识别 API 返回值 {values!r}。")
    if values[0] != 0:
        raise EtabsApiError(f"{context}: ETABS API 返回错误码 {values[0]}。")
    return values[1:]


def etabs_version_info(sap_model: Any) -> dict[str, Any]:
    """Read and classify the ETABS host version across 22/23 signatures.

    ETABS 22 exposes ``GetVersion(ref string, ref double)`` to pythonnet and
    therefore needs initial ref values.  Some ETABS 23 builds also accept a
    no-argument call.  The compatibility boundary tries both without making
    the rest of the workflow branch on the host version.
    """

    try:
        try:
            result = sap_model.GetVersion()
        except TypeError:
            result = sap_model.GetVersion("", 0.0)
    except Exception as exc:
        return {
            "available": False,
            "supported": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    raw = list(result) if isinstance(result, tuple) else [result]
    values = [value if isinstance(value, (str, int, float, bool)) or value is None else str(value) for value in raw]
    version = next((str(value) for value in values if isinstance(value, str)), None)
    major = _major_version(version)
    return {
        "available": True,
        "supported": major in SUPPORTED_ETABS_MAJOR_VERSIONS,
        "version": version,
        "major": major,
        "raw": values,
    }


def _major_version(version: str | None) -> int | None:
    if not version:
        return None
    try:
        return int(version.split(".", 1)[0])
    except ValueError:
        return None


def model_paths_match(actual: str | Path, expected: str | Path) -> bool:
    """Compare one ETABS-reported path with a durable EDB path.

    ETABS may report the transient ``.$et`` sidecar while a model is open.
    Only the same resolved parent and filename stem are accepted.
    """

    actual_path = Path(actual).resolve()
    expected_path = Path(expected).resolve()
    return actual_path.parent == expected_path.parent and actual_path.stem.casefold() == expected_path.stem.casefold()


class EtabsConnector:
    """Attach to ETABS through CSI's documented ``cHelper.GetObject`` API."""

    PROG_ID = "CSI.ETABS.API.ETABSObject"

    def __init__(self, dll_path: str | Path) -> None:
        self.dll_path = Path(dll_path)
        self.app: Any | None = None
        self.sap_model: Any | None = None

    def attach(self) -> Any:
        """Attach to the registered active ETABS instance."""

        return self._attach(lambda helper: helper.GetObject(self.PROG_ID))

    def attach_process(self, pid: int) -> Any:
        """Attach to one explicit ETABS process and retain its application."""

        if isinstance(pid, bool) or int(pid) <= 0:
            raise EtabsConnectionError("ETABS PID 必须是正整数。")
        return self._attach(
            lambda helper: helper.GetObjectProcess(self.PROG_ID, int(pid)),
        )

    def _attach(self, resolver: Any) -> Any:
        if not self.dll_path.is_file():
            raise EtabsConnectionError(f"找不到 ETABS API DLL：{self.dll_path}")
        try:
            import clr

            clr.AddReference(str(self.dll_path))
            from ETABSv1 import Helper, cHelper

            helper = cHelper(Helper())
            app = resolver(helper)
            if app is None:
                raise EtabsConnectionError("未找到已运行的 ETABS 实例。")
            sap_model = app.SapModel
            if sap_model is None:
                raise EtabsConnectionError("已连接 ETABS，但未取得 SapModel。")
        except EtabsConnectionError:
            raise
        except Exception as exc:  # pythonnet translates COM/.NET exceptions
            raise EtabsConnectionError(
                "无法连接到 ETABS。请确认受支持的 ETABS 22/23 已打开、模型已加载，"
                "Python/ETABS 均为 x64，且 DLL 主版本与宿主一致。"
            ) from exc

        self.app = app
        self.sap_model = sap_model
        return sap_model

    def model_filename(self) -> str:
        if self.sap_model is None:
            raise EtabsConnectionError("尚未连接 ETABS。")
        # Unlike most OAPI calls, GetModelFilename returns a string directly.
        return str(self.sap_model.GetModelFilename(True))

    def present_units(self) -> Any:
        """Return the model's current ETABS present/display unit enum."""
        if self.sap_model is None:
            raise EtabsConnectionError("尚未连接 ETABS。")
        return self.sap_model.GetPresentUnits()

    def set_present_units(self, units: Any) -> None:
        """Set ETABS present/display units and check the OAPI return code."""
        if self.sap_model is None:
            raise EtabsConnectionError("尚未连接 ETABS。")
        api_values(self.sap_model.SetPresentUnits(units), "SetPresentUnits")

    @contextmanager
    def temporary_present_units(self, target_units: Any) -> Iterator[Any]:
        """Temporarily set present units and always restore the original value.

        ETABS exposes values through its current present-unit context.  This
        context manager changes only that context for the duration of an export
        and restores it even when extraction or Excel writing fails.
        """
        original_units = self.present_units()
        changed = original_units != target_units
        if changed:
            self.set_present_units(target_units)
            if self.present_units() != target_units:
                raise EtabsApiError("SetPresentUnits: ETABS 未切换到请求的单位。")
        try:
            yield original_units
        finally:
            if changed:
                self.set_present_units(original_units)
