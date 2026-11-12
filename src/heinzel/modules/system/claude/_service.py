"""Service of the claude submodule: what Claude has configured on this computer, and which servers are on.

Scanning is read-only (ADR 0062). Switching a server off or on writes only hz's own state file (ADR 0146); it
starts, stops and connects to nothing.
"""

import logging
from collections import Counter
from pathlib import Path

from heinzel import Check, HeinzelError, Kernel, Status
from heinzel.kernel._logging import step
from heinzel.modules.system.claude._models import (
    ClaudeScan,
    ClaudeSummary,
    FirstRunNotice,
    FoundServer,
    NoticeClosed,
    ServerSwitch,
)
from heinzel.modules.system.claude._scan import LINK, Scanner
from heinzel.modules.system.claude._switch import SwitchState, SwitchStore, Tombstone, apply

logger = logging.getLogger("heinzel.claude")

STATE_FILE = Path("claude") / "servers.json"


class ClaudeService:
    """Scans Claude Code's and Claude Desktop's files in a home directory and remembers which servers are off."""

    def __init__(self, home: Path, store: SwitchStore | None = None) -> None:
        """Scan `home`: tests pass a fake home directory. Without a `store` nothing can be switched."""
        self._home = home
        self._store = store

    @classmethod
    def for_user(cls, kernel: Kernel) -> "ClaudeService":
        """The service for your own home directory, with its switches in hz's state folder."""
        return cls(Path.home(), SwitchStore(kernel.settings.state_dir / STATE_FILE))

    def _state(self) -> SwitchState | None:
        return SwitchState() if self._store is None else self._store.read()

    def scan(self) -> ClaudeScan:
        """Everything found, with names only for environment variables and headers, never their values."""
        scanner = Scanner(self._home)
        scanner.scan()
        state = self._state()
        servers = apply(scanner.servers, state)
        found = bool(servers or scanner.skills or scanner.connectors)
        return ClaudeScan(
            servers=servers,
            notice=_notice(servers, state),
            state_problem=self._problem() if state is None else None,
            skills=scanner.skills,
            connectors=scanner.connectors,
            skipped=scanner.skipped,
            next=[] if found else ["Nothing found: Claude Code and Claude Desktop have no servers or skills here."],
        )

    def _problem(self) -> str:
        path = self._store.path if self._store else STATE_FILE
        return (
            f"{path} cannot be read, so every server is switched off. Move the file away to start again with every "
            "found server on."
        )

    def summary(self) -> ClaudeSummary:
        """Counts per source, without a single name."""
        scan = self.scan()
        return ClaudeSummary(
            servers=dict(Counter(s.source for s in scan.servers)),
            skills=dict(Counter(s.source for s in scan.skills)),
            connectors=scan.connectors,
            skipped=len(scan.skipped),
            enabled=sum(s.enabled for s in scan.servers),
        )

    def switch(self, server_id: str, *, on: bool) -> ServerSwitch:
        """Switch the server `server_id` on or off and remember it; nothing is started or stopped.

        Raises:
            HeinzelError: If no such server is found or remembered, it is hz itself, or the state cannot be written.
        """
        store = self._require_store()
        # The id can name a project directory, so the log gets the scope and the outcome, never the id or name.
        with step(logger, "claude.server.switch", on=on) as running, store.change() as state:
            scanner = Scanner(self._home)
            scanner.scan()
            server = next((s for s in scanner.servers if s.id == server_id), None)
            remembered = next((t for t in state.disabled if t.id == server_id), None)
            if server is None and (on is False or remembered is None):
                msg = f"No MCP server has the id {server_id!r}. `hz system claude list --json` shows every id."
                raise HeinzelError(msg)
            if server is not None and server.status == "self":
                msg = "That is hz's own MCP server; it is never offered back to itself, so there is nothing to switch."
                raise HeinzelError(msg)
            before = len(state.disabled)
            was_off = remembered is not None or (server is not None and state.off(server))
            if on:
                state.disabled = [
                    t for t in state.disabled if t.id != server_id and not (server is not None and t.matches(server))
                ]
            elif server is not None and not was_off:
                state.disabled.append(Tombstone(id=server.id, name=server.name, fingerprint=server.fingerprint))
            changed = len(state.disabled) != before
            running.note(scope=server.scope if server else None, changed=changed)
            return ServerSwitch(
                id=server_id,
                name=server.name if server else remembered.name if remembered else server_id,
                scope=server.scope if server else None,
                enabled=on,
                changed=changed,
            )

    def acknowledge(self) -> NoticeClosed:
        """Close the first-run notice for every server found now; one found later shows it again.

        Raises:
            HeinzelError: If the state cannot be written.
        """
        store = self._require_store()
        with step(logger, "claude.notice.acknowledge") as running, store.change() as state:
            scanner = Scanner(self._home)
            scanner.scan()
            known = set(state.acknowledged)
            fresh = [s.id for s in scanner.servers if s.status != "self" and s.id not in known]
            state.acknowledged = sorted(known | set(fresh))
            running.note(servers=len(fresh))
            return NoticeClosed(servers=len(fresh))

    def _require_store(self) -> SwitchStore:
        if self._store is None:
            msg = "This service has no state file, so nothing can be switched."
            raise HeinzelError(msg)
        return self._store

    def checks(self) -> list[Check]:
        """Whether every Claude file and hz's own switch file could be read; counts only, so doctor names nothing."""
        scan = self.scan()
        checks: list[Check] = []
        if scan.state_problem:
            checks.append(
                Check(
                    name="claude switches",
                    status=Status.DEGRADED,
                    detail="The file that remembers switched-off MCP servers cannot be read; every server is off",
                    fix="hz system claude list",
                )
            )
        broken = [s for s in scan.skipped if s.reason != LINK]  # a refused link is listed, not a fault
        if broken:
            checks.append(
                Check(
                    name="claude files",
                    status=Status.DEGRADED,
                    detail=f"{len(broken)} Claude file(s) could not be read: unreadable, invalid or too large",
                    fix="hz system claude list",
                )
            )
        else:
            checks.append(Check(name="claude files", status=Status.UP))
        return checks


def _notice(servers: list[FoundServer], state: SwitchState | None) -> FirstRunNotice | None:
    """The switched-on servers the person has not closed the notice for; none when there is nothing new."""
    if state is None:
        return None
    known = set(state.acknowledged)
    fresh = [s for s in servers if s.enabled and s.id not in known]
    if not fresh:
        return None
    return FirstRunNotice(
        servers=[s.id for s in fresh],
        project=sum(s.scope == "project" for s in fresh),
        plugin=sum(s.scope == "plugin" for s in fresh),
    )
