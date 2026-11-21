"""Persist only profile routing; behavior remains in the selected configuration files."""

import os
import shlex
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from heinzel import Config, HeinzelError, Settings

_FIELDS = frozenset(
    {
        "HOME",
        "HZ_CONFIG_HOME",
        "HZ_STATE_DIR",
        "HZ_DATA_DIR",
        "HZ_CACHE_DIR",
        "HZ_LOG_DIR",
        "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR",
        "XDG_CONFIG_DIRS",
    }
)
_OPTIONAL = frozenset({"HZ_CONFIG", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"})
_ROUTING = frozenset(name for name in _FIELDS if name.startswith("HZ_")) | {"HZ_CONFIG"}
_SESSION = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "SSH_AUTH_SOCK",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "SECURITYSESSIONID",
    "__CF_USER_TEXT_ENCODING",
)


def default_profile(home: Path, state: Path, prefix: Path, executable: Path) -> dict[str, str]:
    """Provide explicit owned fixture routing without reading ambient user configuration."""
    return {
        "HOME": str(home),
        "HZ_CONFIG_HOME": str(home / ".config/hz"),
        "HZ_STATE_DIR": str(state),
        "HZ_DATA_DIR": str(home / "data"),
        "HZ_CACHE_DIR": str(home / "cache"),
        "HZ_LOG_DIR": str(home / "log"),
        "UV_TOOL_DIR": str(prefix.parent),
        "UV_TOOL_BIN_DIR": str(executable.parent),
        "XDG_CONFIG_DIRS": "/etc/xdg",
    }


def user_profile(config: Config, settings: Settings, home: Path, prefix: Path, executable: Path) -> dict[str, str]:
    """Capture selected user-scope file selectors and effective roots, never credentials or behavior values."""
    profile = default_profile(home, settings.state_dir, prefix, executable)
    for name in ("state", "data", "cache", "log"):
        profile[f"HZ_{name.upper()}_DIR"] = str(getattr(settings, f"{name}_dir").expanduser())
    for file in config.files:
        if file.scope == "user":
            profile["HZ_CONFIG_HOME"] = str(file.path.parent.expanduser().absolute())
        elif file.scope == "explicit":
            profile["HZ_CONFIG"] = str(file.path.expanduser().absolute())
    profile["XDG_CONFIG_DIRS"] = os.environ.get("XDG_CONFIG_DIRS", "/etc/xdg")
    for name in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"):
        if value := os.environ.get(name):
            profile[name] = str(Path(value).expanduser().absolute())
    return profile


def installation_overrides(config: Config) -> tuple[str, ...]:
    """Name unsupported process-only behavior instead of silently losing it at the service boundary."""
    unsupported = [
        name if name.isascii() and name.isidentifier() else "unsupported HZ variable"
        for name in os.environ
        if name.upper().startswith("HZ_") and name.upper() not in _ROUTING
    ]
    if any(file.scope == "project" and file.exists for file in config.files):
        unsupported.append("home/ancestor hz.toml")
    return tuple(sorted(unsupported))


def validated_profile(value: object, home: Path, state: Path, prefix: Path, executable: Path) -> dict[str, str] | None:
    """Accept only the complete bounded routing schema and exact installation anchors."""
    try:
        profile = TypeAdapter(dict[str, str]).validate_python(value, strict=True)
    except ValidationError:
        return None
    if not _FIELDS.issubset(profile) or not frozenset(profile).issubset(_FIELDS | _OPTIONAL):
        return None
    if any(not item or "\x00" in item for item in profile.values()):
        return None
    if any(not Path(item).is_absolute() for name, item in profile.items() if name != "XDG_CONFIG_DIRS"):
        return None
    if any(not Path(item).is_absolute() for item in profile["XDG_CONFIG_DIRS"].split(":")):
        return None
    anchors = {
        "HOME": str(home),
        "HZ_STATE_DIR": str(state),
        "UV_TOOL_DIR": str(prefix.parent),
        "UV_TOOL_BIN_DIR": str(executable.parent),
    }
    return profile if all(profile[name] == item for name, item in anchors.items()) else None


def environment_command(profile: dict[str, str]) -> str:
    """Clear manager credentials and configuration while forwarding only OS-session capabilities at runtime."""
    preserve = "".join(f'if [ "${{{name}+x}}" = x ]; then set -- "$@" "{name}=${name}"; fi\n' for name in _SESSION)
    values = " ".join(shlex.quote(f"{name}={value}") for name, value in sorted(profile.items()))
    return f'set -- /usr/bin/env -i\n{preserve}set -- "$@" {values}\n'


def require_installation_profile(profile: dict[str, str] | None, unsupported: tuple[str, ...]) -> None:
    """Refuse ambiguous routing and process-only settings before any OS integration is changed.

    Raises:
        HeinzelError: If the profile cannot be reproduced from the service manager.
    """
    if unsupported:
        msg = (
            "Move process-only settings to the selected user configuration before installing web availability: "
            + ", ".join(unsupported)
        )
        raise HeinzelError(msg)
    if profile is None:
        msg = "Web availability requires complete absolute profile paths; no integration was changed."
        raise HeinzelError(msg)
