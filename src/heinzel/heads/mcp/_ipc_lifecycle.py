"""Explicit host lifecycle for a reviewed installed MCP foreground registration."""

import json
import os
import re
import secrets
import socket
import sys
import time
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import TYPE_CHECKING, Literal, Never, cast

from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_child import OwnedMcpChild, close_unrelated_descriptors, require_isolated_fork
from heinzel.heads.mcp._ipc_cli_review import review_mcp_lifecycle_from_terminal
from heinzel.heads.mcp._ipc_descriptors import local_stream_pair, receive_exact
from heinzel.heads.mcp._ipc_native_plan import McpNativePlan, NativeMcpState
from heinzel.heads.mcp._ipc_peer import require_owner_peer
from heinzel.heads.mcp._ipc_runtime_root import InstalledMcpRuntime, installation_directories
from heinzel.heads.mcp._ipc_supervisor import owned_installed_mcp_supervisor
from heinzel.kernel import HeinzelError, installation_mutation_pending
from heinzel.kernel._logging import get_logger, step

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from heinzel.kernel import HostServiceManager

type LifecycleAction = Literal["start", "check", "stop"]
type WorkerState = Literal["running", "not-started", "stopped", "unavailable", "stop-incomplete"]
type TransportState = Literal["reachable", "unavailable", "not-checked"]
type _Directories = tuple[tuple[int, int], tuple[int, int]]

REGISTRATION_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z", re.ASCII)
_REFUSED = "The reviewed MCP foreground registration is unavailable or changed"
_IO_SECONDS = 2.0
_START_SECONDS = 8.0
_STOP_SECONDS = 5.0
_REVIEW_SECONDS = 60.0
_BINDING_BYTES = 32
_STDIO = (0, 1, 2)
log = get_logger("mcp")


@dataclass(frozen=True, slots=True)
class ForegroundMcpRegistration:
    """An exact installed registration selected by trusted host code, never by a locator alone."""

    installation: InstalledMcpRuntime
    plan: PrivateMcpPlan = field(repr=False)
    directories: _Directories = field(repr=False)
    native: McpNativePlan
    verify_binding: "Callable[[], None] | None" = field(default=None, repr=False, compare=False)

    @classmethod
    def capture(
        cls,
        installation: InstalledMcpRuntime,
        plan: PrivateMcpPlan,
        *,
        verify_binding: "Callable[[], None] | None" = None,
    ) -> "ForegroundMcpRegistration":
        """Freeze the installed directory identities for the worker that the host can authorize.

        Raises:
            HeinzelError: If the selected plan or isolated installed runtime is unavailable.
        """
        if not sys.flags.isolated or not REGISTRATION_ID.fullmatch(plan.generation.registration_id):
            raise HeinzelError(_REFUSED)
        native = McpNativePlan.capture(installation, plan.generation.registration_id)
        return cls(installation, plan, installation_directories(installation), native, verify_binding)

    def require_current(self) -> None:
        """Verify the exact approved installed scope before forking and before worker publication.

        Raises:
            HeinzelError: If an installation or profile directory changed or maintenance is pending.
        """
        if installation_directories(self.installation) != self.directories or installation_mutation_pending(
            self.installation.prefix, self.installation.state
        ):
            raise HeinzelError(_REFUSED)
        if self.verify_binding is not None:
            self.verify_binding()

    def offer(self, action: LifecycleAction, deadline: float) -> "dict[str, JsonValue]":
        """Describe the full reviewed foreground action; no result or proof goes to vendor transport."""
        return {
            "kind": "foreground-lifecycle",
            "action": action,
            "action_id": secrets.token_urlsafe(24),
            "registration": self.plan.generation.registration_id,
            "generation": self.plan.generation.number,
            "grant": self.plan.grant_id,
            "grant_revision": self.plan.grant_revision,
            "terms": cast("JsonValue", json.loads(self.plan.terms_json)),
            "grant_valid_until": self.plan.valid_until,
            "review_expires_at": deadline,
            "sdk": os.fspath(self.installation.prefix),
            "profile": os.fspath(self.installation.state),
            "sdk_directory": list(self.directories[0]),
            "profile_directory": list(self.directories[1]),
            "activation": "owned-foreground-child",
            "native_label": self.native.name,
        }


@dataclass(frozen=True, slots=True)
class ForegroundMcpStatus:
    """Independent process and transport observations. No readiness probe is performed here."""

    action: LifecycleAction
    worker: WorkerState
    transport: TransportState = "not-checked"
    manager: NativeMcpState = "not-checked"
    mcp_readiness: Literal["unverified"] = "unverified"


def _detach_stdio(channel: socket.socket) -> None:
    # Preserve 0..2 as inert descriptors so later application logging cannot write to a reused control FD.
    if channel.fileno() in _STDIO:
        raise HeinzelError(_REFUSED)
    close_unrelated_descriptors(channel.fileno(), *_STDIO)
    null = os.open(os.devnull, os.O_RDWR)
    try:
        for descriptor in _STDIO:
            if descriptor != null:
                os.dup2(null, descriptor)
    finally:
        if null not in _STDIO:
            os.close(null)


def _reply(channel: socket.socket, binding: bytes, status: bytes) -> None:
    channel.settimeout(_IO_SECONDS)
    channel.sendall(status + binding)


def _worker_main(registration: ForegroundMcpRegistration, channel: socket.socket, binding: bytes) -> Never:
    status = 1
    try:
        _detach_stdio(channel)
        require_owner_peer(channel, owner_uid=os.getuid())
        registration.require_current()
        with owned_installed_mcp_supervisor(registration.installation, registration.plan) as supervisor:
            registration.require_current()
            supervisor.check_transport()
            _reply(channel, binding, b"T")
            while True:
                channel.settimeout(None)
                action = channel.recv(1)
                if not action:
                    break  # Losing the retained host channel retires this worker.
                if receive_exact(channel, len(binding), time.monotonic() + _IO_SECONDS) != binding or action not in {
                    b"C",
                    b"S",
                }:
                    raise HeinzelError(_REFUSED)
                if action == b"S":
                    break
                try:
                    registration.require_current()
                    supervisor.check_transport()
                except HeinzelError:
                    _reply(channel, binding, b"U")
                else:
                    _reply(channel, binding, b"T")
        # Confirm only after the supervisor closed admission, fenced work and released its descriptors.
        if action == b"S":
            _reply(channel, binding, b"S")
        status = 0
    finally:
        os._exit(status)


@dataclass(slots=True)
class _ForegroundWorker:
    child: OwnedMcpChild
    channel: socket.socket = field(repr=False)
    binding: bytes = field(repr=False)
    stopping: bool = False

    def require_startup(self) -> None:
        require_owner_peer(self.channel, owner_uid=os.getuid())
        if self.response(_START_SECONDS) != b"T":
            raise HeinzelError(_REFUSED)

    def response(self, seconds: float) -> bytes:
        message = receive_exact(self.channel, 1 + len(self.binding), time.monotonic() + seconds)
        return message[:1] if message[1:] == self.binding else b""

    def exchange(self, action: bytes, seconds: float) -> bytes:
        self.channel.settimeout(_IO_SECONDS)
        self.channel.sendall(action + self.binding)
        return self.response(seconds)

    def retire(self) -> None:
        self.channel.close()
        self.child.retire(_STOP_SECONDS, _REFUSED)


def _spawn_worker(registration: ForegroundMcpRegistration) -> _ForegroundWorker:
    require_isolated_fork(_REFUSED)
    binding = secrets.token_bytes(_BINDING_BYTES)
    parent, child = local_stream_pair()
    with child:
        try:
            pid = os.fork()
        except OSError:
            parent.close()
            raise HeinzelError(_REFUSED) from None
        if pid == 0:
            _worker_main(registration, child, binding)
    return _ForegroundWorker(OwnedMcpChild(pid), parent, binding)


class ForegroundMcpLifecycle:
    """The trusted host's single registration and its owned child, shared by local command routing."""

    def __init__(self, registration: ForegroundMcpRegistration, *, manager: "HostServiceManager | None" = None) -> None:
        self._registration = registration
        self._manager = manager
        self._worker: _ForegroundWorker | None = None
        self._stopped = False
        self._closed = False
        self._lock = Lock()

    def perform(self, action: LifecycleAction, registration_id: str) -> ForegroundMcpStatus:
        """Review one exact action on the controlling terminal and use it once in this scope.

        Raises:
            HeinzelError: If the host, scope, review or registration cannot authorize the action.
        """
        with self._lock, step(log, "mcp.ipc.foreground_lifecycle", operation="ipc", step=action):
            registration = self._registration
            if (
                self._closed
                or registration_id != registration.plan.generation.registration_id
                or action not in {"start", "check", "stop"}
            ):
                raise HeinzelError(_REFUSED)
            # An owned Stop remains available after grant expiry or a maintenance/locator fence.
            if action == "start":
                registration.require_current()
                self._require_clear_native_label()
            deadline = time.monotonic() + _REVIEW_SECONDS
            review_mcp_lifecycle_from_terminal(registration.offer(action, deadline))
            if time.monotonic() >= deadline:
                raise HeinzelError(_REFUSED)
            if action == "start":
                manager = self._require_clear_native_label()
                status = self._start(deadline)
            else:
                status = self._stop() if action == "stop" else self._check(action)
                manager = registration.native.observe(self._manager)
            return replace(status, manager=manager)

    def _require_clear_native_label(self) -> NativeMcpState:
        state = self._registration.native.observe(self._manager)
        if state in {"conflict", "unavailable"}:
            raise HeinzelError(_REFUSED)
        return state

    def _start(self, deadline: float) -> ForegroundMcpStatus:
        if self._worker is not None:
            return self._check("start")
        self._registration.require_current()
        if time.monotonic() >= min(deadline, self._registration.plan.valid_until):
            raise HeinzelError(_REFUSED)
        worker = _spawn_worker(self._registration)
        self._worker = worker
        try:
            worker.require_startup()
        except BaseException:
            # Keep the direct child tracked if its exact exit cannot be established during cleanup.
            worker.stopping = True
            worker.retire()
            self._worker = None
            raise
        self._stopped = False
        return ForegroundMcpStatus("start", "running", "reachable")

    def _check(self, action: LifecycleAction) -> ForegroundMcpStatus:
        worker = self._worker
        if worker is None:
            return ForegroundMcpStatus(action, "stopped" if self._stopped else "not-started")
        if worker.stopping:
            return ForegroundMcpStatus(action, "stop-incomplete")
        try:
            observed = worker.exchange(b"C", _STOP_SECONDS)
        except OSError:
            observed = b""
        if observed not in {b"T", b"U"}:
            return ForegroundMcpStatus(action, "unavailable", "unavailable")
        return ForegroundMcpStatus(action, "running", "reachable" if observed == b"T" else "unavailable")

    def _stop(self) -> ForegroundMcpStatus:
        worker = self._worker
        if worker is None:
            return ForegroundMcpStatus("stop", "stopped" if self._stopped else "not-started")
        if not worker.stopping:
            worker.stopping = True
            try:
                if worker.exchange(b"S", _STOP_SECONDS) == b"S" and worker.child.wait(_STOP_SECONDS) == 0:
                    worker.channel.close()
                    self._worker = None
                    self._stopped = True
                    return ForegroundMcpStatus("stop", "stopped")
                worker.child.wait(0)  # A reaped child without the exact acknowledgement is still stop-incomplete.
            except OSError:
                pass
        return ForegroundMcpStatus("stop", "stop-incomplete")

    def close(self) -> None:
        """Release an owned child on host teardown, without reporting a reviewed Stop success."""
        with self._lock:
            self._closed = True
            if self._worker is not None:
                self._worker.retire()
                self._worker = None
