"""What an agent may do with an operation that changes things: the write policy per operation (ADR 0162, amends 0045).

Every write operation carries a marker, `@module.operation(..., agents=...)`:

- `direct`: an agent's call runs at once and is audited;
- `approve`: an agent's call never runs by itself: in a Heinzel's chat it is proposed to the person (`ActionGate`),
  for an outside agent over MCP it is refused with the command the person can run;
- `never`: the person's own operations (confirming, declining, approving): refused on every agent path;
- `propose`: the behaviour before this policy (proposed in a chat, run over MCP), for code not yet reviewed.

No marker means `propose`. The marker is the operation's own word, so the kernel bounds it: a plugin's write that
requires a capability which reaches other people (`CEILING`) is never above `approve`, whatever it declares.
A marker may depend on the call's arguments (`ByArguments`): changing a bookmark's address is `approve`, renaming it
is `direct`. Rule for whoever marks an operation: the marker is at least as strict as everything the operation can
trigger.

The caller of an operation (`Caller`) is set by the head that authenticated the call, read from a context variable
and injected by type. It is never built from arguments, and an operation asking for it without one is refused.
"""

import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, get_args

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._logging import get_logger, log_event

if TYPE_CHECKING:
    from heinzel.kernel._lets import OperationLet

audit = get_logger("agent.audit")

AgentWrites = Literal["direct", "propose", "approve", "never"]
AGENT_WRITES: Final = frozenset(get_args(AgentWrites))
_STRICTNESS: Final[dict[str, int]] = {"direct": 0, "propose": 1, "approve": 2, "never": 3}
CEILING: Final = frozenset({"mail", "chat", "calendar"})
"""Capabilities whose writes reach other people: a plugin's operation writing one is at most `approve`."""

CallerKind = Literal["person", "heinzel", "local-agent", "token", "daemon"]
WriteOutcome = Literal["ran", "failed", "proposed", "refused", "over-budget"]


@dataclass(frozen=True, slots=True)
class ByArguments:
    """A marker that depends on the call: `stricter` when `when(arguments)` holds, else `base`."""

    base: AgentWrites
    stricter: AgentWrites
    when: Callable[[Mapping[str, Any]], bool]
    reason: str
    """Which calls are stricter, for the reference and the refusal: "when it changes the address"."""

    def __post_init__(self) -> None:
        """Refuse an order that would make the special case the weaker one.

        Raises:
            ValueError: If a value is unknown or `stricter` is not stricter than `base`.
        """
        if self.base not in AGENT_WRITES or self.stricter not in AGENT_WRITES:
            msg = f"An agents= marker is one of {', '.join(sorted(AGENT_WRITES))}"
            raise ValueError(msg)
        if _STRICTNESS[self.stricter] <= _STRICTNESS[self.base]:
            msg = f"ByArguments needs a stricter case than {self.base!r}; got {self.stricter!r}"
            raise ValueError(msg)


def stricter(first: AgentWrites, second: AgentWrites) -> AgentWrites:
    """The stricter of two markers."""
    return first if _STRICTNESS[first] >= _STRICTNESS[second] else second


def declared_agent_writes(operation: "OperationLet") -> frozenset[AgentWrites]:
    """Every value the operation's own marker can give; empty for none (which is treated as `propose`)."""
    marker = operation.agents
    if marker is None:
        return frozenset()
    if isinstance(marker, ByArguments):
        return frozenset({marker.base, marker.stricter})
    return frozenset({marker})


def reaches_people(operation: "OperationLet") -> bool:
    """Whether the operation writes a capability under the kernel's ceiling: mail, chat or a calendar."""
    return any(r.capability in CEILING and "write" in r.modes for r in operation.requires)


def agent_writes(
    operation: "OperationLet", arguments: Mapping[str, Any], *, builtin: bool, acknowledged: bool = False
) -> AgentWrites:
    """The policy for one call of a write operation by an agent.

    `builtin` is the module record's own origin, set by discovery, never by the module. `acknowledged`: the owner
    allowed this plugin operation to run directly; a plugin's `direct` without that is `propose`, so neither a new
    plugin nor an update that turns an operation `direct` starts writing before the owner said yes. A marker that
    fails to decide is the strictest it could have been.
    """
    marker = operation.agents
    if marker is None:
        policy: AgentWrites = "propose"
    elif isinstance(marker, ByArguments):
        try:
            policy = marker.stricter if marker.when(arguments) else marker.base
        except Exception:  # noqa: BLE001 - a marker must never fail open
            policy = marker.stricter
    else:
        policy = marker
    if not builtin and reaches_people(operation):
        policy = stricter(policy, "approve")
    if not builtin and policy == "direct" and not acknowledged:
        policy = "propose"
    return policy


@dataclass(frozen=True, slots=True)
class Caller:
    """Who calls an operation, as the head that authenticated the call established it."""

    kind: CallerKind
    id: str
    """The Heinzel's chat session, the token's key id, or the head's name for a person, a local agent, the daemon."""
    session: str | None = None
    """The chat session of a Heinzel's turn; operations taking a session id compare it with this."""
    attended: bool = False
    """Whether a person started the turn and is there to see it. Unknown counts as not: a scheduled turn, a token
    and an agent over stdio are unattended, and only the head that knows a person watches says otherwise."""

    @property
    def agent(self) -> bool:
        """Whether this is an agent, not the person or the daemon."""
        return self.kind in {"heinzel", "local-agent", "token"}

    def __str__(self) -> str:
        """`heinzel:<session>`, `token:<key id>`, `person:cli`."""
        return f"{self.kind}:{self.id}"


_caller: ContextVar[Caller | None] = ContextVar("hz_caller", default=None)


@contextlib.contextmanager
def calling(caller: Caller | None) -> Generator[None]:
    """Scope the caller a trusted head established; None clears an inherited one.

    Yields:
        Nothing; the previous caller is restored afterwards.
    """
    token = _caller.set(caller)
    try:
        yield
    finally:
        _caller.reset(token)


def current_caller() -> Caller:
    """The caller of this call.

    Raises:
        HeinzelError: If no head established one; there is no fallback to the person.
    """
    caller = _caller.get()
    if caller is None:
        msg = "This operation needs to know who calls it, and this call path does not say"
        raise HeinzelError(msg)
    return caller


HUMAN_AUTHENTICATIONS: Final = frozenset({"cli", "api-bearer", "web-session", "web-device"})
"""The authentications a person holds. An agent's (`mcp-stdio`, a token, a Heinzel's turn) is never among them."""


def person_caller(authentication: str | None) -> Caller | None:
    """The person as caller for a call a human head authenticated; None for any other, which then has no caller."""
    return Caller("person", authentication, attended=True) if authentication in HUMAN_AUTHENTICATIONS else None


def caller_or_none() -> Caller | None:
    """The caller if a head established one; for audit lines, never for a decision."""
    return _caller.get()


SESSION_ARGUMENTS: Final = ("session", "session_id")
"""Arguments that name a session as plain data: a Heinzel's turn may name only its own."""


def bound_to_caller(policy: AgentWrites, caller: Caller, arguments: Mapping[str, Any]) -> AgentWrites:
    """Hold a Heinzel's direct write to its own identity: what it names as plain data must be itself.

    A session other than the turn's own is refused. A `heinzel` it names (who did something, in the team's
    telemetry) cannot be checked against a chat, so that write waits for the person, who sees the name.

    Raises:
        HeinzelError: If a Heinzel's turn names another session.
    """
    if caller.kind != "heinzel":
        return policy
    for name in SESSION_ARGUMENTS:
        value = arguments.get(name)
        if value is not None and value != caller.session:
            msg = "This turn can act only for its own session; it named another one"
            raise HeinzelError(msg)
    named = arguments.get("heinzel")
    if policy == "direct" and named is not None and named != caller.id:
        return "approve"
    return policy


@dataclass(frozen=True, slots=True)
class Budget:
    """How many direct writes one caller may make: per minute and per day."""

    minute: int
    day: int


ATTENDED: Final = Budget(minute=30, day=500)
UNATTENDED: Final = Budget(minute=10, day=100)
"""A turn nobody watches writes less: its input was seen by no one."""


class WriteBudget:
    """Counts the direct writes of each agent caller in this process and refuses past the budget."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start empty; `clock` is injected by tests."""
        self._clock = clock
        self._lock = threading.Lock()
        self._writes: dict[str, deque[float]] = {}

    def take(self, caller: Caller) -> None:
        """Count one direct write of `caller`.

        Raises:
            HeinzelError: If the caller's budget for the minute or the day is used up; nothing was counted.
        """
        budget = ATTENDED if caller.attended else UNATTENDED
        now = self._clock()
        with self._lock:
            writes = self._writes.setdefault(str(caller), deque())
            while writes and now - writes[0] >= 86400:  # noqa: PLR2004 - seconds of a day
                writes.popleft()
            last_minute = sum(1 for moment in writes if now - moment < 60)  # noqa: PLR2004 - seconds of a minute
            if last_minute >= budget.minute or len(writes) >= budget.day:
                span = "minute" if last_minute >= budget.minute else "day"
                msg = (
                    f"This agent used its write budget for the {span} "
                    f"({budget.minute} a minute, {budget.day} a day); the person can make the change themselves"
                )
                raise HeinzelError(msg)
            writes.append(now)


_PROCESS_SALT: Final = secrets.token_bytes(16)
"""The salt where no installation salt is kept: digests then compare within one process only."""


class AgentWriteLog:
    """What the owner sees and allows of agents' writes; a module provides a subclass (`system.writes`).

    A base class rather than a protocol, so the kernel injects it by type. Without a provider nothing is recorded
    and no plugin write is acknowledged, which is the strict side.
    """

    def record(  # noqa: PLR0913 - one flat entry
        self,
        caller: "Caller | None",
        module: str,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        policy: AgentWrites,
        outcome: WriteOutcome,
    ) -> None:
        """Keep one entry of a write an agent asked for, for the owner's list; never arguments, only their digest."""

    def acknowledged(self, distribution: str, module: str, operation: str) -> bool:  # noqa: ARG002
        """Whether the owner allowed this plugin's operation to run directly for agents; no by default."""
        return False

    def salt(self) -> bytes:
        """The installation's key for argument digests; without a provider, one of this process."""
        return _PROCESS_SALT


def arguments_digest(arguments: Mapping[str, Any], salt: bytes = _PROCESS_SALT) -> str:
    """A short keyed digest of a call's arguments: enough to tell two calls apart, nothing to read.

    Keyed with a salt of this installation, so a guessed argument (a short title, an address) cannot be confirmed
    from a log line by someone who does not hold the salt.
    """
    text = json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)
    return hmac.new(salt, text.encode(), hashlib.sha256).hexdigest()[:16]


_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#-]{0,79}")
"""What counts as an id in the record: no spaces and no `@`, so neither free text nor an email address is kept."""


def target_of(arguments: Mapping[str, Any]) -> str | None:
    """The id of what the call changes, where its arguments name one; never free text or an address."""
    for name in ("entry", "id", "name", "issue", "session", "session_id"):
        value = arguments.get(name)
        if isinstance(value, str) and _ID.fullmatch(value):
            return value
    return None


def audit_write(  # noqa: PLR0913 - one flat audit line
    caller: Caller | None,
    module: str,
    operation: str,
    arguments: Mapping[str, Any],
    *,
    policy: AgentWrites,
    outcome: WriteOutcome,
    error: str | None = None,
    salt: bytes = _PROCESS_SALT,
) -> None:
    """One `agent.write` line for every write an agent asked for, on every path; arguments only as a digest."""
    log_event(
        audit,
        "agent.write",
        level=logging.INFO if outcome in {"ran", "proposed"} else logging.WARNING,
        caller=str(caller) if caller else "unknown",
        attended=caller.attended if caller else None,
        # `module` is a log record's own attribute (the source file's), so the fields say whose it is.
        write_module=module,
        write_operation=operation,
        target=target_of(arguments),
        arguments=arguments_digest(arguments, salt),
        policy=policy,
        outcome=outcome,
        error=error,
    )
