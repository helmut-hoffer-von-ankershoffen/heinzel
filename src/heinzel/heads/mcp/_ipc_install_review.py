"""Local review and read-only verification of a manual MCP Install."""

import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

from heinzel.heads.mcp._client_config import ClientConfigAction
from heinzel.heads.mcp._client_config_files import client_config_scope
from heinzel.heads.mcp._client_config_host import HostClientConfigWorkflow
from heinzel.heads.mcp._ipc_cli_review import require_local_mcp_terminal, review_mcp_lifecycle_from_terminal
from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._mcp_handoff import MCP_HANDOFF_SECONDS, mcp_handoff, require_mcp_handoff

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from contextlib import AbstractContextManager

    from pydantic import JsonValue

    from heinzel.heads.mcp._client_config import ClientConfigContext
    from heinzel.heads.mcp._client_config_files import PinnedClientBridge
    from heinzel.heads.mcp._client_config_host import ClientChangeReview, NativeObservation

log = get_logger("mcp")
_UNAVAILABLE = "The local MCP Install session is unavailable or has expired."


@dataclass(frozen=True, slots=True)
class ManualInstallAuthority:
    """Host-supplied scope and revocation fence for one manual Install target.

    The fence must compare the expected current authority digest while holding the same lock used for revocation.
    A host retains one reviewer per target. No production startup supplies this authority.
    """

    context: "ClientConfigContext" = field(repr=False)
    pinned: "PinnedClientBridge" = field(repr=False)
    observe: "NativeObservation" = field(repr=False)
    fence: "Callable[[str], AbstractContextManager[None]]" = field(repr=False)


@dataclass(frozen=True, slots=True)
class _ManualHandoff:
    registration: str
    token: str = field(repr=False)
    exact: "dict[str, JsonValue]" = field(repr=False)
    authority_digest: str = field(repr=False)
    config_digest: str = field(repr=False)
    scope: tuple[tuple[int, int], tuple[int, int]]
    deadline: float


@dataclass(slots=True)
class _ManualSession:
    lock: Lock = field(default_factory=Lock)
    pending: _ManualHandoff | None = None
    active: bool = False


@dataclass(frozen=True, slots=True)
class McpInstallReview:
    """Trusted installed composition must provide the exact SDK, grant, target and client preimage."""

    snapshot: "Callable[[str], dict[str, JsonValue]]" = field(repr=False)
    manual: ManualInstallAuthority | None = field(default=None, repr=False, kw_only=True)
    clock: "Callable[[], float]" = field(default=time.monotonic, repr=False, kw_only=True)
    _session: _ManualSession = field(default_factory=_ManualSession, init=False, repr=False, compare=False)

    def _exact(self, registration: str) -> "dict[str, JsonValue]":
        return {
            "kind": "foreground-lifecycle",
            "action": "install",
            "registration": registration,
            "review_mode": "review-only",
            "install_plan": self.snapshot(registration),
        }

    def prepare(self, registration: str, expires_at: int) -> str:
        """Commit to a client config preimage and plan without applying or approving it.

        Raises:
            HeinzelError: If the manual target is unavailable or another session owns it.
        """
        if self.manual is None:
            return mcp_handoff(self._exact(registration), expires_at)
        return self._prepare_manual(self.manual, registration, expires_at)

    def _prepare_manual(self, authority: ManualInstallAuthority, registration: str, expires_at: int) -> str:
        session = self._session
        with session.lock:
            now = self.clock()
            remaining = expires_at - time.time()
            if (
                session.active
                or (session.pending is not None and now < session.pending.deadline)
                or authority.pinned.binding.registration != registration
                or not 0 < remaining <= MCP_HANDOFF_SECONDS
            ):
                raise HeinzelError(_UNAVAILABLE)
            host = self.snapshot(registration)
            if not {"generation", "grant", "grant_revision", "sdk_fingerprint"} <= host.keys():
                raise HeinzelError(_UNAVAILABLE)
            authority_digest = arguments_hash(host)
            scope = client_config_scope(authority.context)
            workflow = self._workflow(authority, lambda _: False)
            config_digest = workflow.commitment(ClientConfigAction.INSTALL)
            exact: dict[str, JsonValue] = {
                "kind": "foreground-lifecycle",
                "action": "install",
                "registration": registration,
                "review_mode": "manual-install",
                "client": authority.context.client.value,
                "profile": str(authority.context.home),
                "workspace": str(authority.context.workspace),
                "scope_identity": [list(identity) for identity in scope],
                "authority": host,
                "config_preimage": config_digest,
                "session": secrets.token_hex(16),
            }
            handoff = mcp_handoff(exact, expires_at)
            with authority.fence(authority_digest):
                if self.clock() >= now + remaining:
                    raise HeinzelError(_UNAVAILABLE)
                session.pending = _ManualHandoff(
                    registration, handoff, exact, authority_digest, config_digest, scope, now + remaining
                )
            return handoff

    def review(self, registration: str, handoff: str) -> bool:
        """Review the exact plan and return whether the manual configuration was verified.

        Returns:
            False for the existing review-only route, True for a verified manual edit.
        """
        with step(log, "mcp.ipc.install_review", operation="ipc", step="install_review"):
            if self.manual is not None:
                self._review_manual(self.manual, registration, handoff)
                return True
            require_local_mcp_terminal()
            exact = self._exact(registration)
            require_mcp_handoff(handoff, exact)
            review_mcp_lifecycle_from_terminal(
                exact | {"action_id": secrets.token_urlsafe(24), "review_started": time.time()}
            )
            require_mcp_handoff(handoff, self._exact(registration))
            return False

    def validate(self, registration: str, handoff: str) -> None:
        """Recheck a locator for Settings without claiming or extending the terminal session.

        Raises:
            HeinzelError: If the retained plan, grant, scope or configuration changed.
        """
        authority = self.manual
        if authority is None:
            require_mcp_handoff(handoff, self._exact(registration))
            return
        session = self._session
        with session.lock:
            pending = self._pending_handoff(registration, handoff)
        with self._current_binding(authority, pending):
            pass
        workflow = self._workflow(authority, lambda _: False)
        if workflow.commitment(ClientConfigAction.INSTALL) != pending.config_digest:
            raise HeinzelError(_UNAVAILABLE)
        with session.lock:
            if session.pending is not pending:
                raise HeinzelError(_UNAVAILABLE)
            with self._current_binding(authority, pending):
                pass

    def _workflow(
        self, authority: ManualInstallAuthority, approve: "Callable[[ClientChangeReview], bool]"
    ) -> HostClientConfigWorkflow:
        return HostClientConfigWorkflow(
            authority.context, authority.pinned, observe=authority.observe, approve_local=approve, clock=self.clock
        )

    @contextmanager
    def _current_binding(self, authority: ManualInstallAuthority, pending: _ManualHandoff) -> "Generator[None]":
        require_mcp_handoff(pending.token, pending.exact)
        if (
            self.clock() >= pending.deadline
            or client_config_scope(authority.context) != pending.scope
            or arguments_hash(self.snapshot(pending.registration)) != pending.authority_digest
        ):
            raise HeinzelError(_UNAVAILABLE)
        # The final successful observation linearizes under this same revocation fence.
        with authority.fence(pending.authority_digest):
            if self.clock() >= pending.deadline:
                raise HeinzelError(_UNAVAILABLE)
            yield

    def _require_session(
        self, authority: ManualInstallAuthority, pending: _ManualHandoff, terminal: tuple[int, int]
    ) -> None:
        with self._current_binding(authority, pending):
            require_local_mcp_terminal()
            if self.clock() >= pending.deadline or terminal != (os.getsid(0), os.getpgrp()):
                raise HeinzelError(_UNAVAILABLE)

    def _pending_handoff(self, registration: str, handoff: str) -> _ManualHandoff:
        pending = self._session.pending
        if pending is None or pending.registration != registration or pending.token != handoff:
            raise HeinzelError(_UNAVAILABLE)
        return pending

    def _review_manual(self, authority: ManualInstallAuthority, registration: str, handoff: str) -> None:
        session = self._session
        with session.lock:
            pending = self._pending_handoff(registration, handoff)
            session.pending = None
            session.active = True
        try:
            self._verify_manual(authority, pending)
        except KeyboardInterrupt:
            raise HeinzelError(_UNAVAILABLE) from None
        finally:
            with session.lock:
                session.active = False

    def _verify_manual(self, authority: ManualInstallAuthority, pending: _ManualHandoff) -> None:
        terminal = os.getsid(0), os.getpgrp()

        def approve(review: "ClientChangeReview") -> bool:
            self._require_session(authority, pending, terminal)
            if review.digest != pending.config_digest:
                raise HeinzelError(_UNAVAILABLE)
            arguments: list[JsonValue] = list(review.arguments)
            exact = pending.exact.copy()
            exact.update(server=review.server_name, command=review.command, args=arguments)
            review_mcp_lifecycle_from_terminal(exact, deadline=pending.deadline)
            self._require_session(authority, pending, terminal)
            return True

        self._require_session(authority, pending, terminal)
        workflow = self._workflow(authority, approve)
        identifier = workflow.prepare(ClientConfigAction.INSTALL)
        edit = workflow.review(identifier)
        self._require_session(authority, pending, terminal)
        review_mcp_lifecycle_from_terminal(
            {
                "kind": "foreground-lifecycle",
                "action": "install",
                "registration": pending.registration,
                "session": pending.exact["session"],
                "review_mode": "verify-manual-edit",
                "installation": "not-applied",
                "instruction": "Add only the reviewed entry to the selected file, then confirm verification.",
                "path": str(edit.path),
                "table": edit.table,
                "server": edit.server_name,
                "entry": edit.install_snippet,
            },
            deadline=pending.deadline,
        )
        self._require_session(authority, pending, terminal)
        workflow.verify(identifier)
        authority.pinned.verify()
        self._require_session(authority, pending, terminal)
