"""Which Python a uv tool update installs onto, when a release needs a newer one than the tool runs on.

An update reinstalls the tool on the interpreter it runs on, so a release whose `Requires-Python` excludes that
interpreter could never be reached: uv would keep the old release or fail to resolve. `newer_python` reads the target
release's `Requires-Python` and names the lowest newer minor version that satisfies it; `managed_python` asks uv for a
uv-managed interpreter of that version (installing it if needed) and verifies it like the tool's own, without running
it. Nothing here touches the tool environment: if no interpreter can be had, the update stops before uv installs.
"""

import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from heinzel.kernel import _installation_continuity
from heinzel.kernel._errors import HeinzelError

type Runner = Callable[..., object]

HIGHEST_MINOR = 40
"""The highest Python 3 minor version looked at for a newer interpreter."""
PATCHES = 30
"""Patch levels tried per minor version, so `>=3.15.2` still selects 3.15."""


def _allows(spec: SpecifierSet, major: int, minor: int) -> bool:
    return any(spec.contains(f"{major}.{minor}.{patch}", prereleases=False) for patch in range(PATCHES))


def newer_python(requires_python: str | None, running: tuple[int, int] | None = None) -> str | None:
    """The `X.Y` a release needs instead of `running` (default: this interpreter), or None if `running` fits.

    None also when the release names no `Requires-Python`: nothing says the running interpreter won't do.

    Raises:
        HeinzelError: If `Requires-Python` is malformed, or no newer Python 3 satisfies it.
    """
    if not requires_python or not requires_python.strip():
        return None
    major, minor = running or (sys.version_info.major, sys.version_info.minor)
    try:
        spec = SpecifierSet(requires_python)
    except InvalidSpecifier as exc:
        msg = f"The release's Python requirement {requires_python!r} can't be read; the update was not started."
        raise HeinzelError(msg) from exc
    if _allows(spec, major, minor):
        return None
    for candidate in range(minor + 1, HIGHEST_MINOR + 1):
        if _allows(spec, major, candidate):
            return f"{major}.{candidate}"
    msg = (
        f"The release needs Python {requires_python}, which no newer Python {major} satisfies; "
        f"hz stays on Python {major}.{minor} and keeps working."
    )
    raise HeinzelError(msg)


def _found(runner: Runner, uv: str, version: str, environment: Mapping[str, str] | None) -> str | None:
    command = [uv, "python", "find", "--managed-python", "--no-python-downloads", version]
    result = runner(command, environment) if environment else runner(command)
    if getattr(result, "returncode", 1) != 0:
        return None
    lines = [line.strip() for line in str(getattr(result, "stdout", "")).splitlines() if line.strip()]
    return lines[-1] if lines else None


def managed_python(
    runner: Runner, uv: str, version: str, environment: Mapping[str, str] | None = None
) -> tuple[str, str, tuple[int, ...]]:
    """A verified uv-managed interpreter for `version` (`X.Y`), installed by uv first if it has none.

    The interpreter is checked as the tool's own is (a physical file, owned safely, its bytes hashed) and must be a
    `pythonX.Y`; it is never run here.

    Raises:
        HeinzelError: If uv can't provide one, or the one it names can't be verified; nothing was installed into the
            tool then.
    """
    found = _found(runner, uv, version, environment)
    detail = ""
    if found is None:
        command: Sequence[str] = [uv, "python", "install", "--managed-python", "--no-bin", version]
        result = runner(list(command), environment) if environment else runner(list(command))
        if getattr(result, "returncode", 1) == 0:
            found = _found(runner, uv, version, environment)
        else:
            detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "")).strip()
    if found is None:
        msg = (
            f"The release needs Python {version}, and uv could not provide a uv-managed Python {version}"
            f"{f': {detail}' if detail else '.'}\nNothing was changed; the installed hz keeps working. "
            f"Install it with `uv python install {version}`, then update again."
        )
        raise HeinzelError(msg)
    unverified = (
        f"The uv-managed Python {version} uv named ({found}) can't be verified; nothing was changed and the installed "
        "hz keeps working."
    )
    try:
        path = Path(found).resolve(strict=True)
    except OSError as exc:
        raise HeinzelError(unverified) from exc
    if not path.name.startswith(f"python{version}"):
        raise HeinzelError(unverified)
    try:
        return _installation_continuity.external_python(path)
    except HeinzelError as exc:
        raise HeinzelError(unverified) from exc
