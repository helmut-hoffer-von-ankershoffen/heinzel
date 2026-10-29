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

    def _owner(self, session: str, request: str) -> None:
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
        if len(matches) != 1 or matches[0].data.get("origin") not in _HEADS:
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
        self._owner(session, request)
        events = self._crud.repository.control_read(session).events
        turn = _turn_id(request)
        prior = next((event for event in events if event.kind == "message.replied" and event.key == turn), None)
        if prior is not None:
            return UserChatAnswer(session_id=session, request_id=request, message=self._crud._message(session, prior))  # noqa: SLF001
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
        policy = SubscriptionAdmission(self._current.principal().id, session, 1, _MAX_REQUESTS, _TIMEOUT, _MAX_OUTPUT)

        def unchanged() -> bool:
            self._owner(session, request)
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
            limits=TurnLimits(requests=_MAX_REQUESTS, total_tokens=None, output_tokens=None, timeout_seconds=_TIMEOUT),
            allow_ungranted_tools=False,
            user_proposal_policy=unchanged,
            backend_binding=BackendBinding(backend="claude-code", mode="cli-model", revision=1),
        )
        return UserChatAnswer(session_id=session, request_id=request, message=message)

    def activity(self, session: str, limit: int = 30) -> UserToolActivity:
        """Return only owner-bound host-observed tool activity."""
        return UserToolActivity(session_id=session, items=InteractiveTurn(self._crud).activity(session, limit=limit))
