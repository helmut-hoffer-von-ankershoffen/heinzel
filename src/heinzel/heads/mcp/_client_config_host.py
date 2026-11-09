"""Private host review and verification of one manual vendor configuration change."""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from heinzel.heads.mcp._client_config import (
    _ABSENT,
    ClientBridgeBinding,
    ClientConfigAction,
    ClientConfigContext,
    ManualClientConfigPlan,
    McpClient,
    NativeClientConfig,
    _require_native,
    _same,
    _split,
    _validate_binding,
    plan_manual_client_config,
)
from heinzel.heads.mcp._client_config_files import (
    ClientFileSnapshot,
    PinnedClientBridge,
    read_client_config,
    require_plain_workspace,
)
from heinzel.heads.mcp._client_config_native import NativeInspection, observe_client_config
from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp")
_REVIEW_SECONDS = 300.0
_MANUAL_SECONDS = 3600.0
_UNAVAILABLE = "The local MCP configuration review is unavailable or has expired."


@dataclass(frozen=True, slots=True)
class ClientChangeReview:
    """Exact, safe-to-display terms for a trusted local reviewer, with no existing config bytes."""

    identifier: str
    digest: str = field(repr=False)
    action: ClientConfigAction
    context: ClientConfigContext
    server_name: str
    command: str = field(repr=False)
    arguments: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ManualClientEdit:
    """Manual instructions issued after the trusted host accepts the exact current review."""

    identifier: str
    action: ClientConfigAction
    path: Path = field(repr=False)
    table: str
    server_name: str
    install_snippet: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Pending:
    review: ClientChangeReview
    plan: ManualClientConfigPlan
    before: ClientFileSnapshot | None = field(repr=False)
    expires_at: float


@dataclass(frozen=True, slots=True)
class _Approved:
    pending: _Pending
    expires_at: float


type NativeObservation = Callable[[ClientConfigContext, ClientBridgeBinding], NativeInspection]


class HostClientConfigWorkflow:
    """Host-owned workflow for an inventory binding and standard local profile.

    The host owns the effective-config observation and physical review capability. Neither is an API input.
    An observer must inspect the actual client in the given workspace and account for project and managed
    layers. It must not launch untrusted commands. It returns unavailable when it cannot prove selection.
    The reviewer shows the exact terms and explicitly accepts or declines on the trusted local host.
    No path is ever changed here. The host records install/removal only after successful verification.
    """

    def __init__(
        self,
        context: ClientConfigContext,
        pinned: PinnedClientBridge,
        *,
        observe: NativeObservation,
        approve_local: Callable[[ClientChangeReview], bool],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate_binding(context, pinned.binding)
        self._context = context
        self._pinned = pinned
        self._observe = observe
        self._approve_local = approve_local
        self._clock = clock
        self._lock = Lock()
        self._pending: _Pending | None = None
        self._approved: _Approved | None = None
        self._reviewing = False

    def prepare(self, action: ClientConfigAction) -> str:
        """Return an opaque local review handle for the current exact change.

        Raises:
            HeinzelError: If inspection fails or a local review or manual edit is already active.
        """
        with step(log, "mcp.client_config.prepare_review", client=self._context.client.value, action=action.value):
            before, native = self._inspect()
            binding = self._pinned.binding
            plan = plan_manual_client_config(action, self._context, binding, _bytes(before), native)
            review = ClientChangeReview(
                secrets.token_hex(16),
                arguments_hash(
                    {
                        "config_plan": plan.digest,
                        "file_identity": None if before is None else list(before.identity),
                        "interpreter_sha256": self._pinned.interpreter_sha256,
                        "bridge_sha256": self._pinned.bridge_sha256,
                    }
                ),
                action,
                self._context,
                binding.name,
                str(binding.interpreter),
                ("-I", "-S", str(binding.bridge)),
            )
            with self._lock:
                now = self._clock()
                if self._reviewing or (self._approved is not None and now < self._approved.expires_at):
                    raise HeinzelError(_UNAVAILABLE)
                self._pending = _Pending(review, plan, before, now + _REVIEW_SECONDS)
            return review.identifier

    def review(self, identifier: str) -> ManualClientEdit:
        """Call the installed trusted reviewer once and recheck the same intent before revealing instructions.

        Raises:
            HeinzelError: If the request is stale, declined, already consumed or no longer matches the config.
        """
        with step(log, "mcp.client_config.review", client=self._context.client.value):
            with self._lock:
                pending = self._pending
                if (
                    self._reviewing
                    or pending is None
                    or pending.review.identifier != identifier
                    or self._clock() >= pending.expires_at
                ):
                    raise HeinzelError(_UNAVAILABLE)
                self._pending = None
                self._reviewing = True
            try:
                self._require_current(pending)
                if self._approve_local(pending.review) is not True:
                    raise HeinzelError(_UNAVAILABLE)
                self._require_current(pending)
                edit = _instruction(pending)
                with self._lock:
                    now = self._clock()
                    if now >= pending.expires_at:
                        raise HeinzelError(_UNAVAILABLE)
                    self._approved = _Approved(pending, now + _MANUAL_SECONDS)
                return edit
            finally:
                with self._lock:
                    self._reviewing = False

    def verify(self, identifier: str) -> ClientConfigAction:
        """Consume the exact approved edit after the native client and file both prove the requested outcome.

        Raises:
            HeinzelError: If there is no matching live approval or the requested outcome cannot be verified.
        """
        with self._lock:
            approval = self._approved
            if (
                approval is None
                or approval.pending.review.identifier != identifier
                or self._clock() >= approval.expires_at
            ):
                raise HeinzelError(_UNAVAILABLE)
        after, native = self._inspect()
        approval.pending.plan.verify(_bytes(approval.pending.before), _bytes(after), native)
        with self._lock:
            if self._approved is not approval or self._clock() >= approval.expires_at:
                raise HeinzelError(_UNAVAILABLE)
            self._approved = None
        return approval.pending.plan.action

    def check(self) -> None:
        """Check the installed config and native selection for this trusted inventory binding.

        Raises:
            HeinzelError: If the exact installation is not currently selected.
        """
        with step(log, "mcp.client_config.check", client=self._context.client.value):
            current, native = self._inspect()
            binding = self._pinned.binding
            entry, _ = _split(self._context, binding.name, _bytes(current))
            if not _same(entry, binding.entry(self._context.client)):
                msg = "The recorded MCP bridge is absent or differs from the installed config."
                raise HeinzelError(msg)
            _require_native(self._context, binding, native, present=True)

    def _require_current(self, pending: _Pending) -> None:
        current, native = self._inspect()
        if current != pending.before:
            raise HeinzelError(_UNAVAILABLE)
        pending.plan.require_current(_bytes(current), native)

    def _inspect(self) -> tuple[ClientFileSnapshot | None, NativeClientConfig]:
        if self._context.client is McpClient.CURSOR:
            msg = "The native MCP config is unavailable for Cursor."
            raise HeinzelError(msg)
        self._pinned.verify()
        before = read_client_config(self._context)
        binding = self._pinned.binding
        require_plain_workspace(self._context, binding.name, before)
        entry, _ = _split(self._context, binding.name, _bytes(before))
        if entry is not _ABSENT and not _same(entry, binding.entry(self._context.client)):
            msg = "The selected profile already contains a different MCP entry."
            raise HeinzelError(msg)
        native = observe_client_config(self._context, binding, self._observe(self._context, binding))
        if read_client_config(self._context) != before:
            msg = "The MCP config changed during native inspection. Inspect again."
            raise HeinzelError(msg)
        require_plain_workspace(self._context, binding.name, before)
        self._pinned.verify()
        return before, native


def _bytes(snapshot: ClientFileSnapshot | None) -> bytes | None:
    return None if snapshot is None else snapshot.data


def _instruction(pending: _Pending) -> ManualClientEdit:
    plan = pending.plan
    install = plan.install_snippet() if plan.action is ClientConfigAction.INSTALL else None
    return ManualClientEdit(
        pending.review.identifier, plan.action, plan.context.path, plan.context.table, plan.binding.name, install
    )
