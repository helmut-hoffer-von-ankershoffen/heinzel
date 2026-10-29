"""User-requested durable chat on the selected Claude Code subscription.

This invokes only the installed user-selected CLI with its documented restricted flags. Host tools are denied by
default, except for bounded private proposal operations. Subscription usage remains unpriced and uses plan limits.
"""

import asyncio
import json
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from heinzel import Config, CurrentUser, HeinzelError
from heinzel.kernel._bindings import CapabilitySettings
from heinzel.kernel._identity import PRIVATE_WRITE
from heinzel.modules.heinzel._claude_subscription_model import (
    _COMMAND_FLAGS,
    _VERSION,
    ClaudeSubscriptionModel,
    SubscriptionAdmission,
    executable_digest,
    subscription_environment,
)
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn, ToolActivity, _turn_id
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import BackendBinding, RequestId, SessionId, SessionMessage
from heinzel.modules.heinzel._subscription_slots import SubscriptionSlots
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Outcome, run_bounded

_HEADS = frozenset({"cli", "api-bearer", "web-session", "web-device"})
_MAX_REQUESTS = 4
_TIMEOUT = 120
_MAX_OUTPUT = 128 * 1024


class UserChatAnswer(BaseModel):
    """A persisted answer for exactly the accepted request; not a schedule or approval."""

    session_id: SessionId
    request_id: RequestId
    message: SessionMessage


class UserTurnResolution(BaseModel):
    """The exact stopped turn was explicitly set aside, not replayed or undone."""

    session_id: SessionId
    request_id: RequestId
    status: Literal["abandoned"] = "abandoned"


class UserTurnState(BaseModel):
    """The recorded state of one owner-bound turn; unresolved may still be running."""

    session_id: SessionId
    request_id: RequestId
    status: Literal["pending", "unresolved", "abandoned", "completed"]


class UserToolActivity(BaseModel):
    """Owner-private, host-observed tool decisions without arguments or secrets."""

    session_id: SessionId
    items: list[ToolActivity] = Field(max_length=100)


class _ClaudeVerifier:
    """Continuity of the user's selected executable and documented no-native-tool invocation, not code attestation."""

    def __init__(self, check: Callable[[], bool], path: Path, digest: str, policy: SubscriptionAdmission) -> None:
        self._check, self._path, self._digest, self._policy = check, path, digest, policy

    def verify(
        self, executable: Path, digest: str, version: str, flags: tuple[str, ...], policy: SubscriptionAdmission
    ) -> bool:
        return (
            self._check()
            and executable == self._path
            and digest == self._digest
            and version == _VERSION
            and flags == _COMMAND_FLAGS
            and policy == self._policy
        )


class UserChatService:
    """Dispatch one already accepted direct-user message using current selected access, never a schedule."""

    def __init__(
        self,
        crud: SessionCrud,
        current: CurrentUser,
        *,
        load: Callable[[], Config] = lambda: Config.load(Path.home()),
        which: Callable[[str], str | None] = shutil.which,
        execute: Callable[..., Outcome] = run_bounded,
    ) -> None:
        self._crud, self._current, self._load, self._which, self._execute = crud, current, load, which, execute

    def _selected(self) -> tuple[HeinzelSettings, RunnerSettings]:
        config = self._load()
        choices = config.settings(CapabilitySettings, "capabilities")
        runner = config.settings(RunnerSettings, "system.runner")
        limits = config.settings(HeinzelSettings, "heinzel")
        if choices.ai_access != "claude-code" or choices.agent_runner != "claude-code":
            msg = "The selected AI access is not the supported Claude Code subscription"
            raise HeinzelError(msg)
        if limits.subscription_budget_mode != "plan-unknown":
            msg = "Claude subscription usage has unknown dollar cost and cannot enforce the configured USD caps"
            raise HeinzelError(msg)
        if runner.model is not None:
            msg = "This Claude chat bridge cannot enforce the configured model override"
            raise HeinzelError(msg)
        return limits, runner

    def _owner(self, session: str, request: str, *, allow_daemon_origin: bool = False) -> None:
        context = self._current.context()
        if (
            context.authentication not in _HEADS
            or PRIVATE_WRITE not in context.permissions
            or context.principal != self._current.owner()
            or self._crud.repository.principal_id != context.principal.id
        ):
            msg = "This chat turn requires an authenticated user request"
            raise HeinzelError(msg)
        events = self._crud.repository.control_read(session).events
        matches = [event for event in events if event.kind == "message.accepted" and event.key == f"message-{request}"]
        allowed_origins = _HEADS | ({"daemon"} if allow_daemon_origin else set())
        if len(matches) != 1 or matches[0].data.get("origin") not in allowed_origins:
            msg = "This message has no authenticated user-chat origin"
            raise HeinzelError(msg)

    def _login(self, executable: Path) -> None:
        env = subscription_environment()
        try:
            with tempfile.TemporaryDirectory(prefix="hz-auth-") as directory:
                result = self._execute(
                    [str(executable), "auth", "status", "--json"],
                    input=None,
                    cwd=Path(directory),
                    env=env,
                    timeout=10,
                    limit=16_384,
                )
            data = json.loads(result.stdout)
            if (
                result.returncode != 0
                or result.truncated
                or not isinstance(data, dict)
                or data.get("loggedIn") is not True
                or data.get("authMethod") != "claude.ai"
                or data.get("apiProvider") != "firstParty"
            ):
                raise ValueError  # noqa: TRY301 - all status errors are redacted
        except Exception:  # noqa: BLE001 - status may contain private user details
            msg = "The selected Claude Code subscription could not be confirmed"
            raise HeinzelError(msg) from None

    @staticmethod
    def _executable(found: str) -> tuple[Path, str]:
        try:
            executable = Path(found).resolve(strict=True)
            return executable, executable_digest(executable)
        except OSError:
            pass
        msg = "The selected Claude Code executable is unavailable"
        raise HeinzelError(msg)

    async def answer(self, session: str, request: str) -> UserChatAnswer:
        """Run or recover this exact request; an interrupted turn is never launched again.

        Raises:
            HeinzelError: If user origin, configured access, bounds, or the stored turn cannot be verified.
        """
        return await self._answer(
            session, request, lambda: self._owner(session, request), _MAX_REQUESTS, _TIMEOUT, user_proposals=True
        )

    async def _answer(  # noqa: PLR0913 - private dispatcher receives exact host policy
        self,
        session: str,
        request: str,
        verify: Callable[[], None],
        max_requests: int,
        duration: int,
        *,
        user_proposals: bool,
        prepare: Callable[[], None] | None = None,
    ) -> UserChatAnswer:
        verify()
        events = self._crud.repository.control_read(session).events
        turn = _turn_id(request)
        prior = next((event for event in events if event.kind == "message.replied" and event.key == turn), None)
        if prior is not None:
            return UserChatAnswer(session_id=session, request_id=request, message=self._crud._message(session, prior))  # noqa: SLF001
        if any(event.kind == "interactive.abandoned" and event.key == turn for event in events):
            msg = "This turn was set aside and will not be retried"
            raise HeinzelError(msg)
        if any(event.kind == "interactive.started" and event.key == turn for event in events):
            msg = "This interrupted turn has an uncertain outcome and cannot be retried automatically"
            raise HeinzelError(msg)
        initial, runner = self._selected()
        found = self._which(runner.claude_executable)
        if found is None:
            msg = "The selected Claude Code executable is unavailable"
            raise HeinzelError(msg)
        executable, digest = await asyncio.to_thread(self._executable, found)
        await asyncio.to_thread(self._login, executable)
        policy = SubscriptionAdmission(self._current.principal().id, session, 1, max_requests, duration, _MAX_OUTPUT)

        def unchanged() -> bool:
            verify()
            current, selected = self._selected()
            resolved = self._which(selected.claude_executable)
            return (
                current == initial
                and selected == runner
                and resolved is not None
                and Path(resolved).resolve() == executable
            )

        verifier = _ClaudeVerifier(unchanged, executable, digest, policy)
        model = ClaudeSubscriptionModel(
            executable,
            policy,
            verifier,
            lambda: policy if unchanged() else replace(policy, revision=2),
            slots=SubscriptionSlots(self._crud.repository),
            turn_id=turn,
            execute=self._execute,
        )
        message = await InteractiveTurn(self._crud).run(
            session,
            request,
            model,
            limits=TurnLimits(requests=max_requests, total_tokens=None, output_tokens=None, timeout_seconds=duration),
            allow_ungranted_tools=False,
            user_proposal_policy=unchanged if user_proposals else None,
            prepare=prepare,
            backend_binding=BackendBinding(backend="claude-code", mode="cli-model", revision=1),
        )
        return UserChatAnswer(session_id=session, request_id=request, message=message)

    def turn_state(self, session: str, request: str) -> UserTurnState:
        """Read the exact turn state without retrying or claiming an unresolved process has stopped.

        Raises:
            HeinzelError: If the journal contains an inconsistent resolution.
        """
        self._owner(session, request, allow_daemon_origin=True)
        repo = self._crud.repository
        turn = _turn_id(request)
        events = repo.control_read(session).events
        starts = [e for e in events if e.kind == "interactive.started" and e.key == turn]
        replies = [e for e in events if e.kind == "message.replied" and e.key == turn]
        resolutions = [e for e in events if e.kind == "interactive.abandoned" and e.key == turn]
        if len(starts) > 1 or len(replies) > 1 or len(resolutions) > 1 or (replies and resolutions):
            msg = "This turn has inconsistent recorded state"
            raise HeinzelError(msg)
        status: Literal["pending", "unresolved", "abandoned", "completed"] = "pending"
        if resolutions:
            event = resolutions[0]
            unstarted = event.data.get("unstarted") is True
            accepted = next(e for e in events if e.kind == "message.accepted" and e.key == f"message-{request}")
            if (
                (unstarted and (starts or accepted.data.get("origin") != "daemon"))
                or (
                    not unstarted
                    and (len(starts) != 1 or event.seq <= starts[0].seq or starts[0].data.get("message_id") != request)
                )
                or event.data.get("message_id") != request
                or event.data.get("owner") != repo.principal_id
                or event.data.get("origin") not in _HEADS
            ):
                msg = "This turn has an invalid resolution record"
                raise HeinzelError(msg)
            status = "abandoned"
        elif replies:
            self._crud._message(session, replies[0])  # noqa: SLF001 - verify private recorded reply
            status = "completed"
        elif starts:
            if starts[0].data.get("message_id") != request:
                msg = "This turn has an invalid start record"
                raise HeinzelError(msg)
            status = "unresolved"
        return UserTurnState(session_id=session, request_id=request, status=status)

    def abandon(self, session: str, request: str) -> UserTurnResolution:
        """Record an explicit owner decision to proceed without replaying a stopped uncertain turn.

        Raises:
            HeinzelError: If the turn is active, finished, not owner-bound, or an unstarted user message.
        """
        self._owner(session, request, allow_daemon_origin=True)
        repo = self._crud.repository
        turn = _turn_id(request)
        with repo.main_writer(session):
            self._owner(session, request, allow_daemon_origin=True)
            with repo.control_writer(session) as writer:
                events = repo.control_read(session).events
                starts = [e for e in events if e.kind == "interactive.started" and e.key == turn]
                resolutions = [e for e in events if e.kind == "interactive.abandoned" and e.key == turn]
                accepted = next(e for e in events if e.kind == "message.accepted" and e.key == f"message-{request}")
                unstarted = not starts and accepted.data.get("origin") == "daemon"
                if not unstarted and (len(starts) != 1 or starts[0].data.get("message_id") != request):
                    msg = "Only a stopped uncertain turn or unstarted daemon occurrence can be set aside"
                    raise HeinzelError(msg)
                if any(e.kind == "message.replied" and e.key == turn for e in events):
                    msg = "This turn already has a recorded reply"
                    raise HeinzelError(msg)
                if resolutions:
                    if (
                        len(resolutions) != 1
                        or resolutions[0].data.get("message_id") != request
                        or resolutions[0].data.get("owner") != repo.principal_id
                        or resolutions[0].data.get("origin") not in _HEADS
                        or resolutions[0].data.get("unstarted", False) != unstarted
                        or (not unstarted and resolutions[0].seq <= starts[0].seq)
                    ):
                        msg = "This turn has an invalid resolution record"
                        raise HeinzelError(msg)
                    return UserTurnResolution(session_id=session, request_id=request)
                writer.append(
                    "interactive.abandoned",
                    key=turn,
                    turn=turn,
                    data={
                        "message_id": request,
                        "owner": repo.principal_id,
                        "origin": self._current.context().authentication,
                        **({"unstarted": True} if unstarted else {}),
                    },
                )
        return UserTurnResolution(session_id=session, request_id=request)

    def activity(self, session: str, limit: int = 30) -> UserToolActivity:
        """Return only owner-bound host-observed tool activity."""
        return UserToolActivity(session_id=session, items=InteractiveTurn(self._crud).activity(session, limit=limit))
