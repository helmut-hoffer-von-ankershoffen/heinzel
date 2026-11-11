"""Shared CLI/web ownership of one transient, authenticated MCP server per profile."""

import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from heinzel import HeinzelError, Kernel
from heinzel.kernel import (
    HostIntegrationRemoval,
    HostServiceManager,
    Installation,
    MaintenanceLocks,
    Progress,
    capture_owned_integration_file,
    feature_enabled,
    host_maintenance_lock,
    installation_mutation_pending,
    launchd_labels,
    private_directory,
    private_read,
    private_write,
    remove_owned_integration_file,
)
from heinzel.kernel._logging import get_logger, step
from heinzel.modules.system.mcp._records import (
    Generation,
    profile_lock,
    read_generation,
    record_path,
    refuse,
    unit_path,
)

FLAG = "managed_mcp"
START_WAIT = 5.0
CHECK_WAIT = 10.0
RESULT_LIMIT = 8192
MAX_TOOL_COUNT = 999_999_999
TOKEN = re.compile(r"hzmcp_[A-Za-z0-9_-]{43,4090}\n\Z")
STARTED = re.compile(r"(?m)^INFO:\s+Started server process \[([1-9][0-9]{0,9})\]$")
log = get_logger("mcp.managed")


class McpStatus(BaseModel):
    """Only observed service facts. No bearer, profile path, PID or raw diagnostics are returned."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["managed-http"] = "managed-http"
    state: Literal["unavailable", "not-created", "ready", "stopped", "unverified"]
    generation: str | None = None
    endpoint: str | None = None
    tools: int | None = None
    ownership: Literal["verified", "unverified"] = "unverified"
    readiness: Literal["authenticated-initialized", "unverified"] = "unverified"
    owned_server: Literal["closed", "unverified"] = "unverified"
    termination: Literal["unverified"] = "unverified"
    descendants: Literal["unverified"] = "unverified"
    can_start: bool = False
    can_stop: bool = False


def installation() -> Installation:
    """Use the existing standard installed tool authority, never an ambient PATH executable."""
    return Installation(Progress())


def platform() -> str:
    """The first qualified service definition is macOS only."""
    return sys.platform


def available_port() -> int:
    """Select an OS loopback port. Auth and exact manager checks reject a later occupation race."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def port_closed(port: int) -> bool:
    """Only literal loopback ECONNREFUSED proves closure; timeout and other failures do not."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return False
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def exclusive(path: Path, value: str) -> None:
    """Publish one new private file without replacing any pre-existing entry.

    Raises:
        OSError: If the exact target is not exclusively creatable.
    """
    private_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def verify_configuration(directory: Path) -> None:
    """Refuse changed generation configuration before any installed CLI invocation.

    Raises:
        HeinzelError: If the isolated configuration boundary has changed.
    """
    if private_read(directory / "hz.toml") != "":
        refuse()
    for scope, relative in (("config", "config.toml"), ("site", "hz/config.toml")):
        folder = directory / scope
        if os.path.lexists(folder):
            private_directory(folder, create=False)
            if os.path.lexists(folder / relative):
                refuse()


@dataclass(frozen=True, slots=True)
class Authority:
    """Exact installation and selected profile, captured before a manager action."""

    installed: Installation
    prefix: Path
    executable: Path
    profile: str

    @property
    def locks(self) -> tuple[Path, Path]:
        """The same physical lock union as provider-free maintenance."""
        return host_maintenance_lock(self.prefix), profile_lock(self.prefix, self.profile)

    @property
    def record(self) -> Path:
        """The only current generation for this selected profile."""
        return record_path(self.prefix, self.profile)

    def existing(self) -> Generation | None:
        """Read an existing receipt without creating any file or adopting a manager job."""
        try:
            return read_generation(self.record)
        except FileNotFoundError:
            return None


class Service:
    """Kernel service used unchanged by both heads. Construction has no local effects."""

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel

    @property
    def enabled(self) -> bool:
        """This flag only authorizes creating a new generation."""
        return platform() == "darwin" and feature_enabled(self.kernel.settings, FLAG)

    def authority(self) -> Authority:
        """Derive an identity from trusted selected configuration/storage and installed tool location."""
        installed = installation()
        _, binary = installed.tool_destination()
        prefix = installed.prefix
        config = [str(item.path.resolve()) for item in self.kernel.config.files if item.scope in {"user", "explicit"}]
        settings = self.kernel.settings
        storage = [
            str(path.resolve())
            for path in (settings.state_dir, settings.data_dir, settings.cache_dir, settings.log_dir)
        ]
        identity = hashlib.sha256(json.dumps([config, storage], separators=(",", ":")).encode()).hexdigest()[:24]
        return Authority(installed, prefix, binary / "hz", identity)

    def manager(self) -> HostServiceManager:
        """Resolve only the registered built-in manager for this verified user session."""
        return self.kernel.resolve_builtin_service(HostServiceManager, "system.daemon")

    def _verified(self, authority: Authority) -> str:
        if (
            not authority.installed.startup_state_matches()
            or installation_mutation_pending(
                authority.prefix,
                self.kernel.settings.state_dir,
            )
            or authority.executable not in authority.installed.tool_entrypoints()
        ):
            refuse()
        return authority.installed.startup_fingerprint()

    def _call(
        self, authority: Authority, generation: Generation, args: tuple[str, ...], input_: bytes = b""
    ) -> dict[str, object]:
        """Call only the fixed trusted Core CLI and read its bounded private JSON, never diagnostics."""
        directory = generation.directory(authority.prefix)
        try:
            verify_configuration(directory)
            with tempfile.TemporaryFile(dir=directory) as output:
                result = subprocess.run(  # noqa: S603 - installed verified executable and fixed operation arguments
                    [str(authority.executable), "system", "serve", *args],
                    cwd=directory,
                    env=generation.environment(authority.prefix),
                    input=input_,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=CHECK_WAIT,
                    check=False,
                )
                output.seek(0)
                content = output.read(RESULT_LIMIT + 1)
            if result.returncode != 0 or len(content) > RESULT_LIMIT:
                refuse()
            decoded: object = json.loads(content)
            if not isinstance(decoded, dict):
                refuse()
            return cast("dict[str, object]", decoded)  # JSON object keys are strings.
        except (OSError, ValueError, subprocess.SubprocessError):
            refuse()

    def _guard(self, authority: Authority, generation: Generation, locks: MaintenanceLocks) -> HostIntegrationRemoval:
        item = generation.integration(authority.prefix, authority.executable)
        guard = HostIntegrationRemoval(item, self.manager(), locks)
        if guard.registered and not any(file.path == item.unit for file in item.files):
            refuse()
        return guard

    def _ready(self, authority: Authority, generation: Generation, guard: HostIntegrationRemoval) -> McpStatus:
        if not self._listener_observed(authority, generation, guard) or generation.installation != self._verified(
            authority
        ):
            refuse()
        token = private_read(generation.directory(authority.prefix) / "probe-token")
        if not TOKEN.fullmatch(token) or hashlib.sha256(token.encode()).hexdigest() != generation.probe_sha256:
            refuse()
        reply = self._call(
            authority, generation, ("mcp-check", "--url", generation.endpoint, "--token-stdin"), token.encode()
        )
        if (
            reply.get("mcp_readiness") != "initialized"
            or reply.get("worker_ownership") != "unverified"
            or type(tools := reply.get("tools")) is not int
            or not 0 <= tools <= MAX_TOOL_COUNT
        ):
            refuse()
        guard.verify_current()
        if generation.installation != self._verified(authority):
            refuse()
        return McpStatus(
            state="ready",
            generation=generation.generation,
            endpoint=generation.endpoint,
            tools=tools,
            ownership="verified",
            readiness="authenticated-initialized",
            can_stop=True,
        )

    def _listener_observed(self, authority: Authority, generation: Generation, guard: HostIntegrationRemoval) -> bool:
        if not guard.running:
            return False
        # A foreign listener can occupy the reserved port while the job is starting. Do not send it the probe
        # credential: first bind the actual retained job's private startup output to the manager's live leader.
        stderr = private_read(generation.directory(authority.prefix) / "server.stderr")
        return STARTED.findall(stderr) == [str(guard.observed_pid)] and (
            f"Uvicorn running on http://127.0.0.1:{generation.port} (" in stderr
        )

    def _stopped(self, generation: Generation) -> McpStatus:
        if not port_closed(generation.port):
            refuse()
        return McpStatus(
            state="stopped",
            generation=generation.generation,
            ownership="verified",
            owned_server="closed",
            can_start=self.enabled,
            can_stop=True,
        )

    def check(self) -> McpStatus:
        """Observe the exact current generation; never create credentials or a server.

        Raises:
            HeinzelError: If the installation or receipt cannot be verified.
        """
        with step(log, "mcp.managed.check"):
            if platform() != "darwin":
                return McpStatus(state="unavailable")
            authority = self.authority()
            generation = authority.existing()
            if generation is None:
                return McpStatus(state="not-created" if self.enabled else "unavailable", can_start=self.enabled)
            return self._check_existing(authority, generation)

    def _check_existing(self, authority: Authority, generation: Generation) -> McpStatus:
        try:
            with MaintenanceLocks(authority.locks) as locks:
                if authority.existing() != generation:
                    refuse()
                guard = self._guard(authority, generation, locks)
                if generation.phase == "stopped":
                    guard.quiescent()
                    return self._stopped(generation)
                return self._ready(authority, generation, guard)
        except (HeinzelError, OSError, ValueError, subprocess.SubprocessError):
            # A retained receipt permits a recovery attempt; Stop still has to verify it independently.
            return McpStatus(state="unverified", generation=generation.generation, can_stop=True)

    def start(self) -> McpStatus:
        """Start one fixed transient job, or reauthenticate the already-owned generation.

        Raises:
            HeinzelError: If new start is disabled or exact ownership/readiness cannot be established.
        """
        with step(log, "mcp.managed.start"):
            if not self.enabled:
                return McpStatus(state="unavailable")
            authority = self.authority()
            with MaintenanceLocks(authority.locks) as locks:
                fingerprint = self._verified(authority)
                existing = authority.existing()
                if existing is not None:
                    guard = self._guard(authority, existing, locks)
                    if existing.phase == "prepared":
                        return self._ready(authority, existing, guard)
                    guard.quiescent()
                    self._stopped(existing)
                    for file in guard.integration.files:
                        if file.path == guard.integration.unit:
                            remove_owned_integration_file(file)
                return self._start_new(authority, fingerprint, locks)

    def _start_new(self, authority: Authority, fingerprint: str, locks: MaintenanceLocks) -> McpStatus:
        manager = self.manager()
        if os.path.lexists(unit_path(authority.prefix, authority.profile)):
            refuse()
        generation = Generation(
            profile=authority.profile, generation=secrets.token_hex(16), installation=fingerprint, port=available_port()
        )
        if generation.label(authority.prefix) in launchd_labels(manager):
            refuse()
        directory = generation.directory(authority.prefix)
        private_directory(directory.parent)
        directory.mkdir(mode=0o700)
        exclusive(directory / "hz.toml", "")
        reply = self._call(
            authority,
            generation,
            ("mcp-token", "create", "--actor", "managed-local", "--scope", "read", "--expires", "1h", "--json"),
        )
        if reply.get("scope") != "read" or not isinstance(token := reply.get("token"), str):
            refuse()
        token += "\n"
        if not TOKEN.fullmatch(token):
            refuse()
        generation = generation.model_copy(update={"probe_sha256": hashlib.sha256(token.encode()).hexdigest()})
        # The recovery record must precede publication and loading of any job.
        private_write(authority.record, generation.text())
        exclusive(directory / "probe-token", token)
        exclusive(directory / "server.stderr", "")
        unit = unit_path(authority.prefix, authority.profile)
        exclusive(unit, generation.definition(authority.prefix, authority.executable))
        self._guard(authority, generation, locks).quiescent()
        verify_configuration(directory)
        if (
            fingerprint != self._verified(authority)
            or manager.command(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(unit)]).returncode
        ):
            refuse()
        deadline = time.monotonic() + START_WAIT
        while time.monotonic() < deadline:
            guard = self._guard(authority, generation, locks)
            if self._listener_observed(authority, generation, guard):
                return self._ready(authority, generation, guard)
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return refuse()

    def stop(self) -> McpStatus:
        """Stop only the existing captured job, even after the preview is disabled.

        Raises:
            HeinzelError: If the registered owner or its endpoint closure cannot be verified.
        """
        with step(log, "mcp.managed.stop"):
            if platform() != "darwin":
                return McpStatus(state="unavailable")
            authority = self.authority()
            existing = authority.existing()
            if existing is None:
                return McpStatus(state="not-created" if self.enabled else "unavailable", can_start=self.enabled)
            with MaintenanceLocks(authority.locks) as locks:
                if authority.existing() != existing:
                    refuse()
                self._guard(authority, existing, locks).stop()
                result = self._stopped(existing)
                secret = existing.directory(authority.prefix) / "probe-token"
                if os.path.lexists(secret):
                    captured = capture_owned_integration_file(secret)
                    if hashlib.sha256(captured.content).hexdigest() != existing.probe_sha256:
                        refuse()
                    remove_owned_integration_file(captured)
                if existing.phase != "stopped":
                    private_write(authority.record, existing.model_copy(update={"phase": "stopped"}).text())
                return result
