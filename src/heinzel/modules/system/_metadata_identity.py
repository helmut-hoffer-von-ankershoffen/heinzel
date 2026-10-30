"""Narrow owner-installed core metadata read policy, not publisher authentication.

This internal policy assumes the running host and Python process are trusted against concurrent in-process
mutation. It establishes continuity of selected loaded core code and source, not signed or publisher-verified code.
"""

import hashlib
import inspect
from collections import defaultdict
from pathlib import Path
from types import CodeType

import heinzel
from heinzel import HeinzelError, Kernel
from heinzel.kernel import CORE_DISTRIBUTION, CoreMetadataVerifier, Exposed
from heinzel.modules import system
from heinzel.modules.system import _models, _service
from heinzel.modules.system._service import SystemService

_OPERATION = "system:modules"
_MAX_SOURCE = 2_000_000


def _codes(root: CodeType) -> list[CodeType]:
    result = [root]
    for value in root.co_consts:
        if isinstance(value, CodeType):
            result.extend(_codes(value))
    return result


def _source(path: Path, root: Path) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        if resolved.is_relative_to(root) and resolved.stat().st_size <= _MAX_SOURCE:
            return resolved.read_bytes()
    except OSError:
        pass
    msg = "Core metadata source could not be verified"
    raise HeinzelError(msg)


def _loaded_matches(function: object, source: bytes, path: Path) -> bool:
    if not inspect.isfunction(function):
        return False
    try:
        compiled = compile(source, str(path), "exec", dont_inherit=True)
        code = function.__code__
        return any(candidate.co_qualname == code.co_qualname and candidate == code for candidate in _codes(compiled))
    except (SyntaxError, ValueError, TypeError):
        return False


class CoreMetadataIdentity(CoreMetadataVerifier):
    """Pin actual loaded built-in registration, callable, dependency, and owner-installed source snapshot."""

    def __init__(self, kernel: Kernel) -> None:
        self._kernel = kernel
        self._root = Path(heinzel.__file__).resolve(strict=True).parent
        self._function = system.modules
        self._service_method = SystemService.modules
        self._module_info = _models.ModuleInfo
        self._kernel_modules = Kernel.modules
        files = tuple(module.__file__ for module in (system, _service, _models))
        if not all(isinstance(path, str) for path in files):
            msg = "Core metadata source could not be verified"
            raise HeinzelError(msg)
        self._source_paths = tuple(Path(path).resolve(strict=True) for path in files if isinstance(path, str))
        self._initial = tuple(_source(path, self._root) for path in self._source_paths)
        if not _loaded_matches(self._function, self._initial[0], self._source_paths[0]) or not _loaded_matches(
            self._service_method, self._initial[1], self._source_paths[1]
        ):
            msg = "Core metadata source could not be verified"
            raise HeinzelError(msg)
        self._digest = hashlib.sha256(
            b"".join(len(source).to_bytes(8, "big") + source for source in self._initial)
        ).hexdigest()

    def __call__(self, item: Exposed) -> str:
        """Recheck the exact core operation and loaded service immediately before review and invocation.

        Raises:
            HeinzelError: If registration, selected code, source, or dependency changed.
        """
        invalid = "Core metadata registration or source changed"
        if (
            item.record.qualname != "system"
            or item.operation.name != "modules"
            or item.parameters
            or not item.operation.read_only
            or item.operation.private
            or item.record.module is not system.module
            or not item.record.origin.builtin
            or item.record.origin.distribution != CORE_DISTRIBUTION
            or item.operation.func is not self._function
            or system.modules is not self._function
            or SystemService.modules is not self._service_method
            or _service.SystemService is not SystemService
            or system.SystemService is not SystemService
            or getattr(_service, "ModuleInfo", None) is not self._module_info
            or _models.ModuleInfo is not self._module_info
            or getattr(_service, "defaultdict", None) is not defaultdict
            or type(self._kernel) is not Kernel
            or Kernel.modules is not self._kernel_modules
            or self._kernel._factories.get(SystemService) is not SystemService  # noqa: SLF001 - verify injection before resolve
            or tuple(_source(path, self._root) for path in self._source_paths) != self._initial
        ):
            raise HeinzelError(invalid)
        service = self._kernel.resolve(SystemService)
        if type(service) is not SystemService or vars(service) != {"_kernel": self._kernel}:
            raise HeinzelError(invalid)
        if not _loaded_matches(self._function, self._initial[0], self._source_paths[0]) or not _loaded_matches(
            self._service_method, self._initial[1], self._source_paths[1]
        ):
            raise HeinzelError(invalid)
        return self._digest
