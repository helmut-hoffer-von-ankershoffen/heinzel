"""Service of the writes module: the owner's record of agents' writes and their word on plugins' direct writes.

Both are owner state, private files under the state directory: `agent-writes/journal.jsonl`, bounded, with one
line per write an agent asked for and never its arguments; and `agent-writes/allowed.json`, the plugin operations
the owner allowed to run directly. A plugin operation that is not in it is proposed, so neither a newly installed
plugin nor an update that turns an operation `direct` starts writing before the owner said yes (ADR 0162).
"""

import fcntl
import json
import os
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name
from pydantic import ValidationError

from heinzel import HeinzelError, Kernel
from heinzel.kernel import AgentWriteLog, ByArguments, Caller, OperationLet, declared_agent_writes
from heinzel.kernel._agent_writes import AgentWrites as Policy
from heinzel.kernel._agent_writes import WriteOutcome, arguments_digest, reaches_people, target_of
from heinzel.modules.system.write._models import AgentWrite, AgentWrites, CallerActivity, PluginWrite, PluginWrites

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
KEEP = 2000
"""Entries the journal keeps; past twice that it is cut back to the latest `KEEP`."""
MAX_LINE = 2000
TRIM_BYTES = 2 * KEEP * 300
"""The journal's size at which it is cut back; an entry is about 300 bytes."""
SALT_BYTES = 16


def _key(module: str, operation: OperationLet) -> str:
    """What the owner's yes names: the operation and the marker it had.

    A later version that loosens the marker (a rename that then also repoints, without asking) is another key, so
    it waits for a new yes.
    """
    marker = operation.agents
    shape = f"{marker.base}|{marker.stricter}:{marker.reason}" if isinstance(marker, ByArguments) else str(marker)
    return f"{module} {operation.name} [{shape}]"


class WriteLog(AgentWriteLog):
    """The record and the allowances, in the owner's state directory."""

    def __init__(self, kernel: Kernel) -> None:
        """Create the service; the kernel is injected for the state directory and the installed modules."""
        self._kernel = kernel
        self._directory = kernel.settings.state_dir / "agent-writes"
        self._journal = self._directory / "journal.jsonl"
        self._allowed = self._directory / "allowed.json"
        self._salt: bytes | None = None

    # --- storage ---

    def _ready(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        self._directory.chmod(PRIVATE_DIR)

    def _replace(self, path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}-{secrets.token_hex(4)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE)
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def salt(self) -> bytes:
        """This installation's key for argument digests: 16 random bytes, made once, readable by the owner alone."""
        if self._salt is None:
            self._ready()
            path = self._directory / "salt"
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(secrets.token_bytes(SALT_BYTES))
            found = path.read_bytes()
            # A short or emptied file is no key: this process then uses its own, never a guessable one.
            self._salt = found if len(found) >= SALT_BYTES else AgentWriteLog().salt()
        return self._salt

    # --- the record ---

    def record(  # noqa: PLR0913 - one flat entry
        self,
        caller: Caller | None,
        module: str,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        policy: Policy,
        outcome: WriteOutcome,
    ) -> None:
        """Append one entry; the journal is cut back when it has grown to twice what it keeps."""
        entry = AgentWrite(
            at=datetime.now(UTC),
            caller=str(caller) if caller else "unknown",
            attended=caller.attended if caller else False,
            module=module,
            operation=operation,
            target=target_of(arguments),
            arguments=arguments_digest(arguments, self.salt()),
            policy=policy,
            outcome=outcome,
        )
        line = entry.model_dump_json().encode() + b"\n"
        self._ready()
        descriptor = os.open(self._journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
        with os.fdopen(descriptor, "ab") as journal:
            fcntl.flock(journal, fcntl.LOCK_EX)
            journal.write(line)
            journal.flush()
            if journal.tell() > TRIM_BYTES:
                kept = self._journal.read_bytes().splitlines(keepends=True)[-KEEP:]
                self._replace(self._journal, b"".join(kept))

    def _entries(self) -> list[AgentWrite]:
        try:
            lines = self._journal.read_bytes().splitlines()
        except FileNotFoundError:
            return []
        entries = []
        for line in lines[-2 * KEEP :]:
            if len(line) > MAX_LINE:
                continue
            try:
                entries.append(AgentWrite.model_validate_json(line))
            except ValidationError:
                continue  # a torn or foreign line is no entry
        return entries

    def listing(self, caller: str | None, *, unattended: bool, limit: int) -> AgentWrites:
        """The entries, latest first, optionally of one agent or of unwatched turns only, and a line per agent."""
        entries = self._entries()
        day_ago = datetime.now(UTC) - timedelta(hours=24)
        activity: dict[str, CallerActivity] = {}
        for entry in entries:
            line = activity.setdefault(entry.caller, CallerActivity(caller=entry.caller, ran=0, ran_today=0, refused=0))
            if entry.outcome == "ran":
                line.ran += 1
                line.ran_today += entry.at >= day_ago
                line.last = entry.at if line.last is None else max(line.last, entry.at)
            elif entry.outcome != "failed":
                line.refused += 1
        shown = [
            entry
            for entry in reversed(entries)
            if (caller is None or entry.caller == caller) and (not unattended or not entry.attended)
        ]
        return AgentWrites(
            writes=shown[:limit],
            callers=sorted(activity.values(), key=lambda line: (-line.ran, line.caller)),
            unattended_ran=sum(1 for e in entries if e.outcome == "ran" and not e.attended and e.at >= day_ago),
        )

    # --- plugins' direct writes ---

    def _allowances(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self._allowed.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}  # unreadable: nothing is allowed, the strict side
        if not isinstance(data, dict):
            return {}
        return {
            plugin: [name for name in names if isinstance(name, str)]
            for plugin, names in data.items()
            if isinstance(plugin, str) and isinstance(names, list)
        }

    def acknowledged(self, distribution: str, module: str, operation: str) -> bool:
        """Whether the owner allowed this plugin operation to run directly, with the marker it has now."""
        found = next(
            (
                let
                for record, let in self._kernel.lets(OperationLet)
                if record.qualname == module and let.name == operation and not record.origin.builtin
            ),
            None,
        )
        if found is None:
            return False
        return _key(module, found) in self._allowances().get(canonicalize_name(distribution), [])

    def _asking(self) -> list[tuple[str, str, OperationLet]]:
        """Every plugin write whose own marker can be `direct`: distribution, module, operation."""
        return [
            (canonicalize_name(record.origin.distribution), record.qualname, operation)
            for record, operation in self._kernel.lets(OperationLet)
            if not record.origin.builtin and not operation.read_only and "direct" in declared_agent_writes(operation)
        ]

    def plugin_writes(self) -> PluginWrites:
        """The plugin operations that ask to run directly, with the owner's word on each."""
        allowed = self._allowances()
        writes = [
            PluginWrite(
                plugin=plugin,
                module=module,
                operation=operation.name,
                summary=operation.summary,
                allowed=_key(module, operation) in allowed.get(plugin, []),
                capped=reaches_people(operation),
            )
            for plugin, module, operation in self._asking()
        ]
        waiting = sorted({write.plugin for write in writes if not write.allowed and not write.capped})
        return PluginWrites(writes=writes, next=[f"hz system write allow {plugin}" for plugin in waiting])

    def allow(self, plugin: str, *, revoke: bool) -> PluginWrites:
        """Allow every direct write the installed version of `plugin` asks for, or take the allowance back.

        Raises:
            HeinzelError: If no installed plugin of that name asks for a direct write.
        """
        name = canonicalize_name(plugin if plugin.startswith("heinzel-") else f"heinzel-{plugin}")
        asking = [_key(module, operation) for found, module, operation in self._asking() if found == name]
        allowed = self._allowances()
        if revoke:
            allowed.pop(name, None)
        elif not asking:
            msg = f"No installed plugin named {name} asks to run a write directly; see `hz system write plugins`."
            raise HeinzelError(msg)
        else:
            allowed[name] = sorted(asking)
        self._ready()
        self._replace(self._allowed, json.dumps(allowed, indent=1, sort_keys=True).encode())
        return self.plugin_writes()
