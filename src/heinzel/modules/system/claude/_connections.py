"""hz as the MCP client of every found server that is switched on: one connection each, started on first use (ADR 0162).

A server is started the first time a Heinzel's turn needs its tools, and stopped after `IDLE` seconds without a call,
when it is switched off, when its entry changes, or when it fails. How to start it is read from its source file at
each start (`_launch`); no secret is stored, and the switch file stays the only state (ADR 0146). Its tool listing
(names, descriptions, schemas: no secret) is kept in memory while the entry is unchanged, so a later turn lists the
tools without starting the server again; the server starts again when one of them is called.

Tool names are `mcp__<server>__<tool>`. A server whose name, made safe, is another server's (or hz's own) is refused,
as is a tool whose name is another tool's: an agent can never call one server's tool by another's name. Lifecycle
logs carry the server's scope and counts, never its id, arguments or output; a failed start's error names the server
(names can reveal employers and customers, and the log stays on this computer).
"""

import atexit
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from heinzel import Check, HeinzelError, Status
from heinzel.kernel import FoundMcpServers, FoundMcpTool, clean_text
from heinzel.kernel._logging import log_event, step
from heinzel.modules.system.claude._client import Client, HttpClient, StdioClient, call_tool, list_tools
from heinzel.modules.system.claude._launch import Launch, NotUsableError, launch
from heinzel.modules.system.claude._models import FoundServer
from heinzel.modules.system.claude._scan import Scanner
from heinzel.modules.system.claude._switch import SwitchStore, apply, claude_home, switch_file

logger = logging.getLogger("heinzel.claude")

IDLE = 600.0
"""Seconds without a call after which a server is stopped."""
NAME_LIMIT = 128
DESCRIPTION_LIMIT = 1024
SCHEMA_LIMIT = 64 * 1024
OWN = {"hz", "heinzel"}
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def namespace(name: str) -> str:
    """A server's name as tool names carry it: letters, digits, `_` and `-`."""
    return _UNSAFE.sub("_", name)


@dataclass(slots=True)
class _Connection:
    """What hz holds for one server: its client while it runs, its listing while its entry is unchanged."""

    digest: str
    client: Client | None = None
    listing: list[dict[str, Any]] | None = None
    used: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True, slots=True)
class ServerState:
    """One switched-on server as `status` shows it: no id, so nothing here names a project."""

    name: str
    scope: str
    running: bool
    tools: int
    problem: str | None


class FoundConnections(FoundMcpServers):
    """The client side of every found, switched-on MCP server, for one home directory."""

    def __init__(
        self,
        home: Path,
        store: SwitchStore | None,
        *,
        idle: float = IDLE,
        clock: Callable[[], float] = time.monotonic,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """Serve the servers found in `home`, switched by `store` (none: every server is off)."""
        self._home = home
        self._store = store
        self._idle = idle
        self._clock = clock
        self._environ = environ
        self._lock = threading.Lock()
        self._connections: dict[str, _Connection] = {}
        self._problems: dict[str, str] = {}
        self._wake = threading.Event()
        self._reaper: threading.Thread | None = None
        self._registered = False

    @classmethod
    def for_user(cls) -> "FoundConnections":
        """The client for your own home directory and switches."""
        return cls(claude_home(), SwitchStore(switch_file()))

    # --- what is switched on

    def _servers(self, want: str | None = None) -> tuple[list[FoundServer], Scanner]:
        """The switched-on servers that are not hz itself; with `want`, that one's raw entry is captured."""
        scanner = Scanner(self._home, want=want)
        scanner.scan()
        state = self._store.read() if self._store is not None else None
        if state is None:  # no switch file can be read: every server is off
            return [], scanner
        return [s for s in apply(scanner.servers, state) if s.enabled and s.status == "found"], scanner

    def _namespaces(self, servers: list[FoundServer]) -> dict[str, str]:
        """Each server's id to its namespace; a namespace two servers share, or hz's own, is refused for all."""
        counts: dict[str, int] = {}
        for server in servers:
            counts[namespace(server.name).lower()] = counts.get(namespace(server.name).lower(), 0) + 1
        names: dict[str, str] = {}
        for server in servers:
            space = namespace(server.name)
            if not space or space.lower() in OWN or counts[space.lower()] > 1:
                self._problems[server.id] = (
                    f"{server.name} is not offered: its name is another server's, or hz's own, in tool names"
                )
                continue
            names[server.id] = space
        return names

    # --- FoundMcpServers

    def tools(self) -> list[FoundMcpTool]:
        """The tools of every usable switched-on server; a server whose tools aren't known yet is started now."""
        self.reap()
        servers, _ = self._servers()
        on = {server.id for server in servers}
        with self._lock:
            off = [ident for ident in self._connections if ident not in on]
        for ident in off:  # switched off or gone since it started: stopped, and its listing forgotten
            self.stop(ident)
        spaces = self._namespaces(servers)
        found: list[FoundMcpTool] = []
        for server in servers:
            space = spaces.get(server.id)
            if space is None:
                continue
            try:
                listing = self._listing(server)
            except HeinzelError as exc:
                self._problems[server.id] = str(exc)
                continue
            found += self._tools(server, space, listing)
        return found

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        """Call `tool` of the switched-on server `server`, starting it if it is not running.

        Raises:
            HeinzelError: If the server is off or gone, not usable, has no such tool, fails or times out.
        """
        self.reap()
        found = self._switched_on(server)
        connection = self._connect(found)
        names = {str(t["name"]) for t in connection.listing or []}
        if tool not in names:
            msg = f"The MCP server {found.name} has no tool {clean_text(tool)[:NAME_LIMIT]}"
            raise HeinzelError(msg)
        with connection.lock:
            client = connection.client
            if client is None or not client.alive():
                msg = f"The MCP server {found.name} stopped; call it again to restart it"
                raise HeinzelError(msg)
            try:
                text = call_tool(client, tool, arguments)
            except HeinzelError:
                self._drop(found.id, connection)
                raise
            connection.used = self._clock()
        return text

    # --- lifecycle

    def start(self, server: str) -> None:
        """Start the switched-on server `server` now, if it isn't running.

        Raises:
            HeinzelError: If it is off, gone or not usable, or does not start.
        """
        self._connect(self._switched_on(server))

    def stop(self, server: str) -> bool:
        """Stop the server `server` if hz runs it; whether it ran."""
        with self._lock:
            connection = self._connections.pop(server, None)
        if connection is None:
            return False
        running = connection.client is not None
        self._drop(server, connection)
        return running

    def restart(self, server: str) -> None:
        """Stop the server `server`, then start it again from its entry as it is now.

        Raises:
            HeinzelError: If it is off, gone or not usable, or does not start.
        """
        self.stop(server)
        self.start(server)

    def stop_all(self) -> None:
        """Stop every server hz runs; the reaper ends too."""
        with self._lock:
            connections, self._connections = self._connections, {}
        for server, connection in connections.items():
            self._drop(server, connection)
        self._wake.set()

    def reap(self) -> int:
        """Stop every server idle for longer than `idle` seconds; how many stopped. Its listing stays known."""
        now = self._clock()
        with self._lock:
            idle = [
                (server, c)
                for server, c in self._connections.items()
                if c.client is not None and now - c.used > self._idle and not c.lock.locked()
            ]
        for _, connection in idle:
            with connection.lock:
                self._stop_client(connection)
        return len(idle)

    def status(self) -> list[ServerState]:
        """Every switched-on server: whether hz runs it, how many tools it offers, and why it can't be used."""
        servers, _ = self._servers()
        with self._lock:
            connections = dict(self._connections)
        rows: list[ServerState] = []
        for server in servers:
            connection = connections.get(server.id)
            running = connection is not None and connection.client is not None and connection.client.alive()
            listing = connection.listing if connection else None
            rows.append(
                ServerState(server.name, server.scope, running, len(listing or []), self._problems.get(server.id))
            )
        return rows

    def checks(self) -> list[Check]:
        """Counts only, so doctor names no server: how many run, and how many can't be used."""
        rows = self.status()
        broken = sum(row.problem is not None for row in rows)
        running = sum(row.running for row in rows)
        if broken:
            return [
                Check(
                    name="claude servers",
                    status=Status.DEGRADED,
                    detail=f"{running} running; {broken} switched-on MCP server(s) can't be used by Heinzels",
                    fix="hz system claude list",
                )
            ]
        return [Check(name="claude servers", status=Status.UP, detail=f"{running} running")]

    # --- internals

    def _switched_on(self, server_id: str) -> FoundServer:
        servers, _ = self._servers()
        found = next((s for s in servers if s.id == server_id), None)
        if found is None:
            self.stop(server_id)
            msg = "That MCP server is switched off or no longer found; nothing was called"
            raise HeinzelError(msg)
        return found

    def _listing(self, server: FoundServer) -> list[dict[str, Any]]:
        """The server's tools: from memory while its entry is unchanged, else from the server, started now.

        Raises:
            HeinzelError: If it is not usable or does not start.
        """
        spec = self._launch(server)
        with self._lock:
            connection = self._connections.get(server.id)
        if connection is not None and connection.digest == spec.digest and connection.listing is not None:
            return connection.listing
        return self._connect(server, spec).listing or []

    def _launch(self, server: FoundServer) -> Launch:
        _, scanner = self._servers(want=server.id)
        if scanner.captured is None:
            msg = "That MCP server is no longer found"
            raise NotUsableError(msg)
        return launch(self._home, server, scanner.captured, self._environ)

    def _connect(self, server: FoundServer, spec: Launch | None = None) -> _Connection:
        """The running connection to `server`, started now if needed: one per server, also across threads.

        Raises:
            HeinzelError: If it is not usable or does not start.
        """
        spec = spec or self._launch(server)
        with self._lock:
            connection = self._connections.get(server.id)
            if connection is None or connection.digest != spec.digest:
                stale = connection
                connection = _Connection(digest=spec.digest)
                self._connections[server.id] = connection
            else:
                stale = None
        if stale is not None:
            self._drop(server.id, stale)
        with connection.lock:
            if connection.client is not None and connection.client.alive():
                return connection
            self._stop_client(connection)  # one that died on its own is ended and closed before the next starts
            client: Client = (
                StdioClient(server.name, spec) if spec.transport == "stdio" else HttpClient(server.name, spec)
            )
            with step(logger, "claude.server.start", scope=server.scope, transport=spec.transport) as running:
                try:
                    client.start()
                    connection.listing = list_tools(client)
                except HeinzelError as exc:
                    client.stop()
                    self._problems[server.id] = str(exc)
                    raise
                running.note(tools=len(connection.listing))
            connection.client, connection.used = client, self._clock()
            self._problems.pop(server.id, None)
        self._start_reaper()
        if not self._registered:  # hz's exit (the daemon's, the web server's, a CLI run's) stops what it started
            self._registered = True
            atexit.register(self.stop_all)
        return connection

    def _tools(self, server: FoundServer, space: str, listing: list[dict[str, Any]]) -> list[FoundMcpTool]:
        """The listing as tools; a name that is unsafe, too long or shared by two tools is left out."""
        counts: dict[str, int] = {}
        for row in listing:
            key = namespace(str(row["name"])).lower()
            counts[key] = counts.get(key, 0) + 1
        tools: list[FoundMcpTool] = []
        refused = 0
        for row in listing:
            name = str(row["name"])
            title = f"mcp__{space}__{namespace(name)}"
            schema = row.get("inputSchema")
            if not isinstance(schema, dict) or cast("dict[str, Any]", schema).get("type") != "object":
                schema = EMPTY_SCHEMA
            if (
                counts[namespace(name).lower()] > 1
                or len(title) > NAME_LIMIT
                or len(json.dumps(schema, default=str)) > SCHEMA_LIMIT
            ):
                refused += 1
                continue
            annotations = row.get("annotations")
            hints = cast("dict[str, Any]", annotations) if isinstance(annotations, dict) else {}
            description = row.get("description")
            tools.append(
                FoundMcpTool(
                    server=server.id,
                    namespace=space,
                    name=name,
                    title=title,
                    description=clean_text(description if isinstance(description, str) else "")[:DESCRIPTION_LIMIT],
                    schema=cast("dict[str, Any]", schema),
                    # A tool that says it is read-only and destructive is a write: proposed.
                    read_only=hints.get("readOnlyHint") is True and hints.get("destructiveHint") is not True,
                )
            )
        if refused:
            log_event(logger, "claude.server.tools_refused", level=logging.WARNING, scope=server.scope, tools=refused)
        return tools

    def _drop(self, server: str, connection: _Connection) -> None:
        with self._lock:
            if self._connections.get(server) is connection:
                del self._connections[server]
        self._stop_client(connection)

    def _stop_client(self, connection: _Connection) -> None:
        client, connection.client = connection.client, None
        if client is not None:
            client.stop()
            log_event(logger, "claude.server.stop", servers=1)

    def _start_reaper(self) -> None:
        with self._lock:
            if self._reaper is not None and self._reaper.is_alive():
                return
            self._wake.clear()
            self._reaper = threading.Thread(target=self._reap_loop, name="mcp-found-reaper", daemon=True)
            self._reaper.start()

    def _reap_loop(self) -> None:
        while not self._wake.wait(min(self._idle / 4, 60.0)):
            self.reap()
            with self._lock:
                if not any(c.client is not None for c in self._connections.values()):
                    return
