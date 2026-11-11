"""The exact private generation and transient job shared by control and maintenance."""

import hashlib
import os
import plistlib
from itertools import islice
from pathlib import Path
from typing import Annotated, Literal, Never

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError
from heinzel.kernel import (
    HostIntegration,
    HostIntegrationScope,
    HostMaintenanceIntegration,
    HostServiceManager,
    OwnedIntegrationFile,
    capture_owned_integration_file,
    host_maintenance_directory,
    host_maintenance_lock,
    launchd_labels,
    private_directory,
    private_read,
)

FILE_PREFIX = "mcp-"
ENV = Path("/usr/bin/env")
MAX_DEFINITIONS = 128
MAX_ENTRIES = 512


def refuse() -> Never:
    """Report unverified ownership without exposing private paths or diagnostics.

    Raises:
        HeinzelError: Always; no ownership may be inferred from incomplete evidence.
    """
    msg = "The managed MCP server could not be verified. Its recovery state was retained."
    raise HeinzelError(msg)


def root(prefix: Path) -> Path:
    """Use the existing physical SDK maintenance namespace, outside the runtime being removed."""
    return host_maintenance_directory(prefix) / "mcp"


def manager_prefix(prefix: Path) -> str:
    """Keep this installed SDK's user-session jobs distinct from every other installation."""
    return f"dev.heinzel.mcp.{hashlib.sha256(str(prefix).encode()).hexdigest()[:12]}."


class Generation(BaseModel):
    """A durable prepared/stopped observation, never a cached readiness or PID."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    profile: Annotated[str, Field(pattern=r"^[0-9a-f]{24}$")]
    generation: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    installation: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    probe_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    port: Annotated[int, Field(ge=1, le=65535)]
    phase: Literal["prepared", "stopped"] = "prepared"

    def directory(self, prefix: Path) -> Path:
        """Derive the private working directory; no path in the receipt is executable authority."""
        return root(prefix) / "profiles" / self.profile / self.generation

    def environment(self, prefix: Path) -> dict[str, str]:
        """Bind site, user and project lookup to this generation; inherit no credentials."""
        home = self.directory(prefix)
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "XDG_CONFIG_DIRS": str(home / "site"),
            "HZ_CONFIG_HOME": str(home / "config"),
            **{f"HZ_{field.upper()}_DIR": str(home / field) for field in ("state", "data", "cache", "log")},
            "HZ_PLUGINS_ENABLED": "false",
            "HZ_UPDATE_CHECK": "false",
            "HZ_AUTO_UPDATE": "false",
        }

    @property
    def endpoint(self) -> str:
        """Never accept a caller-provided endpoint."""
        return f"http://127.0.0.1:{self.port}/mcp"

    def label(self, prefix: Path) -> str:
        """One stable manager identity per selected profile."""
        return manager_prefix(prefix) + self.profile

    def arguments(self, prefix: Path, executable: Path) -> tuple[str, ...]:
        """Explicit env -i clears the GUI session environment before the fixed executable runs."""
        return (
            str(ENV),
            "-i",
            *(f"{key}={value}" for key, value in self.environment(prefix).items()),
            str(executable),
            "system",
            "serve",
            "mcp",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        )

    def definition(self, prefix: Path, executable: Path) -> str:
        """Render the exact transient job. Its definition is never installed in LaunchAgents."""
        return plistlib.dumps(
            {
                "Label": self.label(prefix),
                "ProgramArguments": list(self.arguments(prefix, executable)),
                "WorkingDirectory": str(self.directory(prefix)),
                "RunAtLoad": True,
                "KeepAlive": False,
                "StandardOutPath": "/dev/null",
                "StandardErrorPath": str(self.directory(prefix) / "server.stderr"),
            }
        ).decode()

    def text(self) -> str:
        """One canonical receipt, so alternate JSON encodings are not silently adopted."""
        return self.model_dump_json() + "\n"

    def integration(self, prefix: Path, executable: Path) -> HostIntegration:
        """Capture only exact, non-secret definitions for existing SDK removal and manager checks.

        Raises:
            HeinzelError: If a receipt or definition changed.
        """
        receipt = capture_owned_integration_file(record_path(prefix, self.profile))
        unit = unit_path(prefix, self.profile)
        if receipt.content != self.text().encode():
            refuse()
        files: tuple[OwnedIntegrationFile, ...] = (receipt,)
        if os.path.lexists(unit):
            captured = capture_owned_integration_file(unit)
            if captured.content != self.definition(prefix, executable).encode():
                refuse()
            files = (*files, captured)
        return HostIntegration(
            name=self.label(prefix),
            prefix=prefix,
            platform="darwin",
            files=files,
            locks=(host_maintenance_lock(prefix), profile_lock(prefix, self.profile)),
            unit=unit,
            program=ENV,
            arguments=self.arguments(prefix, executable),
            environment={},
            directory=self.directory(prefix),
        )


def record_path(prefix: Path, profile: str) -> Path:
    """The single current receipt in this SDK's bounded inventory."""
    return root(prefix) / f"{FILE_PREFIX}{profile}.json"


def unit_path(prefix: Path, profile: str) -> Path:
    """A private, non-login plist for this profile only."""
    return root(prefix) / f"{FILE_PREFIX}{profile}.plist"


def profile_lock(prefix: Path, profile: str) -> Path:
    """Use the same physical profile lock for operations and SDK maintenance."""
    return root(prefix) / "locks" / f"{profile}.lock"


def read_generation(path: Path) -> Generation:
    """Refuse malformed, noncanonical or incorrectly named owned receipts.

    Raises:
        HeinzelError: If the recorded generation is not recognized.
    """
    text = private_read(path)
    try:
        value = Generation.model_validate_json(text)
    except ValidationError:
        refuse()
    if value.probe_sha256 is None or value.text() != text or path.name != f"{FILE_PREFIX}{value.profile}.json":
        refuse()
    return value


class Maintenance(HostMaintenanceIntegration):
    """Provider-free enumeration of every owned managed MCP job for this installed SDK."""

    def __init__(self, prefix: Path, platform: str) -> None:
        self.prefix = prefix
        self.platform = platform

    def discovery_scopes(self) -> tuple[HostIntegrationScope, ...]:
        """Keep an exact discovery namespace even when there are no jobs to remove."""
        return (
            (HostIntegrationScope(root(self.prefix), FILE_PREFIX, manager_prefix(self.prefix)),)
            if self.platform == "darwin"
            else ()
        )

    def inventory(self, prefix: Path, binary: Path, manager: HostServiceManager) -> tuple[HostIntegration, ...]:
        """Capture all definitions; an unknown loaded name or orphan definition fails closed.

        Raises:
            HeinzelError: If the complete SDK/manager inventory cannot be proved.
        """
        if self.platform != "darwin":
            return ()
        if prefix != self.prefix:
            refuse()
        folder = root(prefix)
        entries: set[Path] = set()
        if os.path.lexists(folder):
            private_directory(folder, create=False)
            bounded = tuple(islice(folder.iterdir(), MAX_ENTRIES + 1))
            if len(bounded) > MAX_ENTRIES:
                refuse()
            entries = {path for path in bounded if path.name.startswith(FILE_PREFIX)}
        if len(entries) > MAX_DEFINITIONS:
            refuse()
        generations = tuple(read_generation(path) for path in sorted(entries) if path.suffix == ".json")
        items = tuple(generation.integration(prefix, binary / "hz") for generation in generations)
        if {file.path for item in items for file in item.files} != entries:
            refuse()
        loaded = {label for label in launchd_labels(manager) if label.startswith(manager_prefix(prefix))}
        if not loaded <= {item.name for item in items if any(file.path == item.unit for file in item.files)}:
            refuse()
        # Offline recovery has no MCP client. Do not erase a live or uncertain server's SDK: first use the
        # ordinary Stop, which checks both positive manager absence and this generation's endpoint closure.
        if loaded or any(generation.phase != "stopped" for generation in generations):
            msg = "Stop the owned managed MCP server before changing its installed SDK."
            raise HeinzelError(msg)
        return items
