"""Exact profile and loaded-unit identities for the daemon lifecycle (ADR 0020)."""

import hashlib
import json
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Never

from heinzel import Config, HeinzelError, Settings
from heinzel.kernel import user_config_dir
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._units import PATH, TICK

_SPACE = 32
_DEL = 127
_MAX_MANAGER_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class ManagerState:
    """An owned registration, its activation state and any currently executing tick PID."""

    registered: bool
    active: bool
    enabled: bool
    pid: int | None = None


def profile_environment(settings: Settings, daemon: DaemonSettings, config: Config) -> dict[str, str]:
    """Persist only selected configuration routing; credentials and arbitrary environment values stay out.

    Raises:
        HeinzelError: If routing is relative or cannot be represented without control characters.
    """
    user = next((file.path.parent for file in config.files if file.scope == "user"), user_config_dir())
    paths = {
        "HOME": Path.home(),
        "HZ_CONFIG_HOME": user,
        "HZ_DATA_DIR": settings.data_dir,
        "HZ_CACHE_DIR": settings.cache_dir,
        "HZ_STATE_DIR": settings.state_dir,
        "HZ_LOG_DIR": settings.log_dir,
        "HZ_SYSTEM_DAEMON__UNIT_DIR": daemon.unit_dir,
    }
    explicit = [file.path for file in config.files if file.scope == "explicit"]
    if len(explicit) > 1:
        msg = "The daemon cannot preserve ambiguous explicit configuration routing; nothing was installed."
        raise HeinzelError(msg)
    if explicit:
        paths["HZ_CONFIG"] = explicit[0]
    result: dict[str, str] = {}
    for key, path in paths.items():
        text = str(path)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or any(ord(char) < _SPACE or ord(char) == _DEL for char in text)
        ):
            msg = "The daemon requires absolute profile paths without control characters; nothing was installed."
            raise HeinzelError(msg)
        result[key] = text
    return result


def profile_digest(environment: Mapping[str, str]) -> str:
    """Bind the install receipt to the selected profile routing, without copying credentials."""
    return hashlib.sha256(json.dumps(dict(environment), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tick_environment(executable: Path, profile: Mapping[str, str]) -> dict[str, str]:
    """The explicit profile and executable search path of the generated tick."""
    return {"PATH": f"{executable.parent}:{PATH}", **profile}


def _environment_matches(actual: Mapping[str, str], expected: Mapping[str, str]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items()) and not any(
        key.startswith("HZ_") and key not in expected for key in actual
    )


def launchd_owned(output: str, unit: Path, executable: Path, environment: Mapping[str, str]) -> ManagerState:
    """Require one loaded path, program, argv and explicit profile; unfamiliar manager output refuses.

    Raises:
        HeinzelError: If launchd's registration differs or cannot be inspected unambiguously.
    """
    if len(output.encode()) > _MAX_MANAGER_BYTES:
        _refuse()
    paths = re.findall(r"(?m)^\s*path = (.+)$", output)
    programs = re.findall(r"(?m)^\s*program = (.+)$", output)
    arguments = re.findall(r"(?ms)^\s*arguments = \{\n(.*?)^\s*\}", output)
    environments = re.findall(r"(?ms)^\s*environment = \{\n(.*?)^\s*\}", output)
    directories = re.findall(r"(?m)^\s*working directory = (.+)$", output)
    argv = [line.strip() for line in arguments[0].splitlines()] if len(arguments) == 1 else []
    values: dict[str, str] = {}
    if len(environments) == 1:
        for line in environments[0].splitlines():
            key, separator, value = line.strip().partition(" => ")
            if not separator or key in values:
                _refuse()
            values[key] = value
    if (
        paths != [str(unit)]
        or programs != [str(executable)]
        or argv != [str(executable), *TICK]
        or directories != [environment["HOME"]]
        or not _environment_matches(values, environment)
    ):
        _refuse()
    pids = re.findall(r"(?m)^\s*pid = (.*)$", output)
    if len(pids) > 1 or (pids and not re.fullmatch(r"0|[1-9][0-9]{0,9}", pids[0])):
        _refuse()
    return ManagerState(registered=True, active=True, enabled=True, pid=(int(pids[0]) or None) if pids else None)


def systemd_properties(output: str) -> dict[str, str]:
    """Parse unique properties; malformed and duplicate keys are not evidence of ownership.

    Raises:
        HeinzelError: If a property record is malformed.
    """
    values: dict[str, str] = {}
    if len(output.encode()) > _MAX_MANAGER_BYTES:
        _refuse()
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            _refuse()
        values[key] = value
    return values


def systemd_owned(
    service: str, timer: str, units: tuple[Path, Path], executable: Path, environment: Mapping[str, str]
) -> ManagerState:
    """Bind both systemd units, their activation, argv and environment to this exact profile.

    Raises:
        HeinzelError: If a fragment, drop-in, command, profile or activation state is unknown or foreign.
    """
    svc, tmr = systemd_properties(service), systemd_properties(timer)
    if svc.get("LoadState") == tmr.get("LoadState") == "not-found":
        return ManagerState(registered=False, active=False, enabled=False)
    for value, path in zip((svc, tmr), units, strict=True):
        if (
            value.get("Id") != path.name
            or value.get("LoadState") != "loaded"
            or value.get("FragmentPath") != str(path)
            or value.get("DropInPaths") != ""
        ):
            _refuse()
    expected = f"{{ path={executable} ; argv[]={executable} {' '.join(TICK)} ; ignore_errors=no ;"
    command = svc.get("ExecStart", "")
    if not command.startswith(expected) or command.count("argv[]=") != 1 or command.count("{ path=") != 1:
        _refuse()
    if tmr.get("Triggers") != "hz-daemon.service" or svc.get("WorkingDirectory") != environment["HOME"]:
        _refuse()
    if not _environment_matches(_systemd_environment(svc.get("Environment", "")), environment):
        _refuse()
    active, enabled = tmr.get("ActiveState"), tmr.get("UnitFileState")
    if active not in {"active", "inactive"} or enabled not in {"enabled", "disabled"}:
        _refuse()
    if svc.get("Job") != "" or svc.get("ControlPID") != "0":
        _refuse()
    raw_pid = svc.get("MainPID", "")
    if not re.fullmatch(r"0|[1-9][0-9]{0,9}", raw_pid):
        _refuse()
    return ManagerState(
        registered=True, active=active == "active", enabled=enabled == "enabled", pid=int(raw_pid) or None
    )


def _systemd_environment(raw: str) -> dict[str, str]:
    try:
        entries = shlex.split(raw)
    except ValueError as exc:
        msg = "The loaded daemon environment is malformed; nothing was changed."
        raise HeinzelError(msg) from exc
    values: dict[str, str] = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        if not separator or key in values:
            _refuse()
        values[key] = value
    return values


def _refuse() -> Never:
    msg = "The daemon manager identity or selected profile cannot be verified; nothing was changed."
    raise HeinzelError(msg)
