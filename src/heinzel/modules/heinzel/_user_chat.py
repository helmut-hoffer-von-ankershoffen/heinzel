"""User-requested durable chat on the selected Claude Code subscription or a plugin's models (ADR 0159).

On Claude Code this invokes only the installed user-selected CLI with its documented restricted flags. On a plugin's
`ai-models` option hz's engine runs the model the plugin built, checking before each request that the choice holds.
Host tools are denied by default, except for bounded private proposal operations. Subscription and plugin model
usage remains unpriced: tokens are recorded, with no dollar cost.
"""

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field

from heinzel import Config, CurrentUser, HeinzelError, Kernel, Notifications, SessionWriter, UsageEntry
from heinzel.kernel import (
    PRIVATE_WRITE,
    AiModelsSource,
    CapabilitySettings,
    Effort,
    ModelOffer,
    RunnerSnapshot,
    RunnerSnapshotProvider,
    UsageCause,
)
from heinzel.kernel import ProcessOutcome as Outcome
from heinzel.kernel import run_bounded_process as run_bounded
from heinzel.kernel._ai_models import PluginModelChoice, PluginModelError, build_model, choose, list_offers
from heinzel.modules.heinzel._captured_write_binding import SelectedWriteService
from heinzel.modules.heinzel._captured_write_tools import DocumentDraftTools
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews
from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._scheduled_notifications import ScheduledNotificationPolicy, ScheduledNotifications
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import (
    QUESTION_ASKED,
    BackendBinding,
    RequestId,
    SessionId,
    SessionMessage,
)
from heinzel.modules.heinzel._subscription_slots import SubscriptionSlots
from heinzel.modules.heinzel._turn_activity import ToolActivity, TurnActivityProjection, interactive_turn_id

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from heinzel.modules.heinzel._claude_subscription_model import SubscriptionAdmission
    from heinzel.modules.heinzel._plugin_turn import PluginTurnGuard
    from heinzel.modules.heinzel._self_chat_questions import QuestionRelay

_HEADS = frozenset({"cli", "api-bearer", "web-session", "web-device"})
_ANSWERED = _HEADS | {"self-chat"}
"""Who a person's own turn comes from: a head, or the owner's answer in chat (ADR 0160); never the daemon."""
# A turn that creates an agent reads and proposes its contract, identity, schedule and sources, then reports.
_MAX_REQUESTS = 12
MAX_TURN_SECONDS = 120
_MAX_OUTPUT = 128 * 1024
PLUGIN_TURN_TOKENS = 1_000_000
"""The most tokens one turn on a plugin's model may move, input and output, across its requests (ADR 0159)."""
BUILD_TIMEOUT = 30.0
"""How long a plugin may take to build its model; building must not call it."""


class PluginModels(Protocol):
    """Where a plugin's `ai-models` adapters come from: the kernel, for hz's own trusted engine (ADR 0159)."""

    def offers(self, access: str) -> bool:
        """Whether the AI access option `access` is a plugin's `ai-models` adapter."""
        ...

    def source(self, access: str) -> AiModelsSource:
        """That adapter, with its plugin's name and version."""
        ...


@dataclass(frozen=True, slots=True)
class PluginTurnChoice:
    """The plugin model a turn runs on: checked before the turn and again before each request."""

    limits: HeinzelSettings
    source: AiModelsSource
    offer: ModelOffer
    effort: Effort | None
    setting: str | None = None
    """`[ai.access] plugin_model` as configured, which the offer was chosen by; none for the first usable one."""
    configured_effort: Effort | None = None
    """`[ai.access] plugin_effort` as configured; `effort` is none for a model that takes no effort."""

    def held(self) -> tuple[object, ...]:
        """What hz owns and checks before every request of a turn: never the plugin's list, which may change."""
        return (
            self.source.name,
            self.source.version,
            self.setting,
            self.configured_effort,
            self.limits.subscription_budget_mode,
        )

    def terms(self) -> dict[str, object]:
        """What binds an approved schedule or draft to this choice: access, plugin version, model, effort, budget."""
        return {
            "access": self.source.name,
            "distribution": self.source.distribution,
            "version": self.source.version,
            "model": self.offer.id,
            "effort": self.effort,
            "budget_mode": self.limits.subscription_budget_mode,
        }


class UserChatAnswer(BaseModel):
    """A persisted answer for exactly the accepted request; not a schedule or approval."""

    session_id: SessionId = Field(description="The authenticated owner's durable agent session.")
    request_id: RequestId = Field(description="The accepted message request answered by this turn.")
    message: SessionMessage = Field(description="The persisted assistant reply for this exact request.")


class UserTurnResolution(BaseModel):
    """The exact stopped turn was explicitly set aside, not replayed or undone."""

    session_id: SessionId = Field(description="The authenticated owner's durable agent session.")
    request_id: RequestId = Field(description="The stopped request explicitly set aside by its owner.")
    status: Literal["abandoned"] = Field(
        default="abandoned", description="The turn was set aside without replaying or undoing possible effects."
    )


class UserTurnState(BaseModel):
    """The recorded state of one owner-bound turn; unresolved may still be running."""

    session_id: SessionId = Field(description="The authenticated owner's durable agent session.")
    request_id: RequestId = Field(description="The accepted request whose journal state was inspected.")
    status: Literal["pending", "unresolved", "abandoned", "completed"] = Field(
        description="Recorded request state; an unresolved turn may still be running."
    )


class UserToolActivity(BaseModel):
    """Owner-private, host-observed tool decisions without arguments or secrets."""

    session_id: SessionId = Field(description="The authenticated owner's durable agent session.")
    items: list[ToolActivity] = Field(
        max_length=100, description="Recent host-recorded tool events, with no arguments or credentials."
    )


class _ClaudeVerifier:
    """Continuity of the user's selected executable and documented no-native-tool invocation, not code attestation."""

    def __init__(self, check: Callable[[], bool], path: Path, digest: str, policy: "SubscriptionAdmission") -> None:
        self._check, self._path, self._digest, self._policy = check, path, digest, policy
        self._release: str | None = None

    def verify(
        self, executable: Path, digest: str, version: str, flags: tuple[str, ...], policy: "SubscriptionAdmission"
    ) -> bool:
        from heinzel.modules.heinzel._claude_subscription_model import (  # noqa: PLC0415 - model runtime only during an admitted turn
            COMMAND_FLAGS,
            VERSION,
            in_supported_range,
        )

        # Before launch the bridge names the reviewed range; after `--version` the observed release, which must lie
        # inside it and stay the same for the whole turn (the digest is bound too).
        if version != VERSION:
            if not in_supported_range(version) or self._release not in {None, version}:
                return False
            self._release = version
        return (
            self._check()
            and executable == self._path
            and digest == self._digest
            and flags == COMMAND_FLAGS
            and policy == self._policy
        )


class UserChatService:
    """Dispatch one already accepted direct-user message using current selected access, never a schedule."""

    def __init__(  # noqa: PLR0913 - independent host authority and model seams
        self,
        crud: SessionCrud,
        current: CurrentUser,
        runner_settings: RunnerSnapshotProvider,
        *,
        connected: Callable[[], ConnectedReadGrants] | None = None,
        notifications: Callable[[], Notifications] | None = None,
        playbook_kernel: Kernel | None = None,
        load: Callable[[], Config] = lambda: Config.load(Path.home()),
        execute: Callable[..., Outcome] = run_bounded,
        models: "PluginModels | None" = None,
        usage: Callable[[UsageEntry], None] | None = None,
    ) -> None:
        self._crud, self.current, self._load, self.which, self._execute = crud, current, load, shutil.which, execute
        self._models = models
        self._usage = usage
        self._runner_settings = runner_settings
        self._connected = connected
        self._notifications = notifications
        self._playbook_kernel = playbook_kernel
        self.questions: QuestionRelay | None = None
        """Asks a turn's question in chat too, when the owner switched that on (ADR 0160); None asks nothing."""

    def connected_grants(self) -> ConnectedReadGrants:
        """Resolve owner-bound connected grants only inside an authenticated host call.

        Raises:
            HeinzelError: If connected reads are not configured.
        """
        if self._connected is None:
            msg = "Connected reads are unavailable"
            raise HeinzelError(msg)
        return self._connected()

    def plugin_selected(self) -> PluginTurnChoice | None:
        """The plugin model the selected AI access runs on hz's engine, or None for Claude Code (ADR 0159).

        Raises:
            HeinzelError: If the plugin's models can't serve a turn now: the dollar caps can't be enforced, or the
                chosen model is unavailable (`ModelUnavailableError`, naming others to pick).
        """
        if self._models is None:
            return None
        config = self._load()
        access = config.settings(CapabilitySettings, "capabilities").ai_access
        if access is None or not self._models.offers(access):
            return None
        limits = config.settings(HeinzelSettings, "heinzel")
        if limits.subscription_budget_mode != "plan-unknown":
            msg = "A plugin's models report no dollar cost and cannot enforce the configured USD caps"
            raise HeinzelError(msg)
        choice = config.settings(PluginModelChoice, "ai.access")
        source = self._models.source(access)
        offers, _note = list_offers(source)
        offer = choose(offers, choice.plugin_model, choice.plugin_effort)
        effort = choice.plugin_effort if offer.thinking is not None else None
        return PluginTurnChoice(limits, source, offer, effort, choice.plugin_model, choice.plugin_effort)

    def _plugin_held(self) -> tuple[object, ...] | None:
        """What `held` says now, read from the configuration only: no call to the plugin."""
        if self._models is None:
            return None
        config = self._load()
        access = config.settings(CapabilitySettings, "capabilities").ai_access
        if access is None or not self._models.offers(access):
            return None
        choice = config.settings(PluginModelChoice, "ai.access")
        limits = config.settings(HeinzelSettings, "heinzel")
        source = self._models.source(access)
        return (source.name, source.version, choice.plugin_model, choice.plugin_effort, limits.subscription_budget_mode)

    def binding_terms(self) -> dict[str, object]:
        """What an approved schedule or draft is bound to: the selected access as it runs now, never a credential.

        Raises:
            HeinzelError: If the selected access cannot be observed.
        """
        plugin = self.plugin_selected()
        if plugin is not None:
            return plugin.terms()
        limits, runner = self.selected()
        path = self.which(runner.claude_executable)
        if path is None:
            msg = "The selected Claude Code executable is unavailable"
            raise HeinzelError(msg)
        executable, digest = self.executable(path)
        return {
            "executable": str(executable),
            "digest": digest,
            "budget_mode": limits.subscription_budget_mode,
            "model": runner.model,
            "runner_revision": runner.revision,
        }

    def selected(self) -> tuple[HeinzelSettings, RunnerSnapshot]:
        config = self._load()
        choices = config.settings(CapabilitySettings, "capabilities")
        runner = self._runner_settings.snapshot(config)
        limits = config.settings(HeinzelSettings, "heinzel")
        if choices.ai_access != "claude-code" or choices.agent_runner != "claude-code":
            msg = "The selected AI access is not the supported Claude Code subscription"
            raise HeinzelError(msg)
        if limits.subscription_budget_mode != "plan-unknown":
            msg = "Claude subscription usage has unknown dollar cost and cannot enforce the configured USD caps"
            raise HeinzelError(msg)
        from heinzel.modules.heinzel._claude_subscription_model import require_no_model_override  # noqa: PLC0415 - model runtime only when checking selected AI access

        require_no_model_override(runner.model)
        return limits, runner

    def _owner(self, session: str, request: str, *, allow_daemon_origin: bool = False) -> None:
        context = self.current.context()
        if (
            context.authentication not in _HEADS
            or PRIVATE_WRITE not in context.permissions
            or context.principal != self.current.owner()
            or self._crud.repository.principal_id != context.principal.id
        ):
            msg = "This chat turn requires an authenticated user request"
            raise HeinzelError(msg)
        events = self._crud.repository.control_read(session).events
        matches = [event for event in events if event.kind == "message.accepted" and event.key == f"message-{request}"]
        allowed_origins = _HEADS
        if allow_daemon_origin:
            allowed_origins = _ANSWERED | frozenset(["daemon"])
        if len(matches) != 1 or matches[0].data.get("origin") not in allowed_origins:
            msg = "This message has no authenticated user-chat origin"
            raise HeinzelError(msg)

    def _login(self, executable: Path) -> None:
        from heinzel.modules.heinzel._claude_subscription_model import require_subscription_login  # noqa: PLC0415 - model runtime only when checking selected AI access

        require_subscription_login(executable, self._execute)

    @staticmethod
    def executable(found: str) -> tuple[Path, str]:
        from heinzel.modules.heinzel._claude_subscription_model import executable_digest  # noqa: PLC0415 - model runtime only when checking selected AI access

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
        return await self.dispatch(
            session,
            request,
            lambda: self._owner(session, request),
            _MAX_REQUESTS,
            MAX_TURN_SECONDS,
            user_proposals=True,
        )

    async def dispatch(  # noqa: PLR0913 - private dispatcher receives exact host policy
        self,
        session: str,
        request: str,
        verify: Callable[[], None],
        max_requests: int,
        duration: int,
        *,
        user_proposals: bool,
        prepare: Callable[[], None] | None = None,
        connected_allowed: bool = False,
        notification_policy: ScheduledNotificationPolicy | None = None,
        notification_contract: str | None = None,
        notification_schedule: str | None = None,
        notification_binding: str | None = None,
    ) -> UserChatAnswer:
        verify()
        events = self._crud.repository.control_read(session).events
        turn = interactive_turn_id(request)
        prior = next((event for event in events if event.kind == "message.replied" and event.key == turn), None)
        if prior is not None:
            return UserChatAnswer(
                session_id=session, request_id=request, message=self._crud.read_message(session, prior)
            )
        if any(event.kind == "interactive.abandoned" and event.key == turn for event in events):
            msg = "This turn was set aside and will not be retried"
            raise HeinzelError(msg)
        if any(event.kind == "interactive.started" and event.key == turn for event in events):
            msg = "This interrupted turn has an uncertain outcome and cannot be retried automatically"
            raise HeinzelError(msg)
        from heinzel.modules.heinzel._engine import TurnLimits  # noqa: PLC0415 - model execution only during a turn
        from heinzel.modules.heinzel._interactive_turn import InteractiveTurn  # noqa: PLC0415 - model execution only during a turn

        plugin = await asyncio.to_thread(self.plugin_selected)
        hooks: list[PluginTurnGuard] = []
        limits = TurnLimits(requests=max_requests, total_tokens=None, output_tokens=None, timeout_seconds=duration)
        if plugin is None:
            model, unchanged, binding = await self._claude_model(session, turn, verify, max_requests, duration)
        else:
            cause: UsageCause = "chat" if user_proposals else "scheduled"
            model, unchanged, binding, hook = await self._plugin_model(plugin, verify, cause)
            hooks.append(hook)
            limits = limits.model_copy(update={"total_tokens": PLUGIN_TURN_TOKENS})
        scheduled_notice = None
        if notification_policy is not None:
            if (
                self._notifications is None
                or notification_contract is None
                or notification_schedule is None
                or notification_binding is None
                or user_proposals
            ):
                msg = "Scheduled notification authority is unavailable"
                raise HeinzelError(msg)
            scheduled_notice = ScheduledNotifications(
                self._crud,
                self.current,
                self._notifications(),
                session=session,
                turn=turn,
                contract_sha256=notification_contract,
                schedule_request=notification_schedule,
                schedule_sha256=notification_binding,
                policy=notification_policy,
                verify=verify,
                connected=self.connected_grants() if notification_policy.item_evidence is not None else None,
            )
        connected = self._connected() if (user_proposals or connected_allowed) and self._connected else None
        connected_set = (
            ConnectedReadSetApprovals(ConnectedReadSetReviews(ConnectedReadSets(self._crud, self.current), connected))
            if user_proposals and connected is not None
            else None
        )
        run = InteractiveTurn(self._crud).run(
            session,
            request,
            model,
            limits=limits,
            allow_ungranted_tools=False,
            user_proposal_policy=unchanged if user_proposals else None,
            playbook_kernel=self._playbook_kernel if user_proposals else None,
            prepare=prepare,
            connected=connected,
            connected_set=connected_set,
            draft_tools=lambda writer: self._document_drafts(session, turn, connected, writer),
            scheduled_notifications=scheduled_notice,
            backend_binding=binding,
            capabilities=hooks,
        )
        try:
            message = await run
        except PluginModelError as exc:
            if exc.before_response:
                await asyncio.to_thread(self._set_aside, session, request)
            raise
        if self.questions is not None and any(
            e.kind == QUESTION_ASKED and e.key == f"question-{turn}"
            for e in self._crud.repository.control_read(session).events
        ):
            self.questions.asked(session, turn)
        return UserChatAnswer(session_id=session, request_id=request, message=message)

    def _set_aside(self, session: str, request: str) -> None:
        """A plugin's model failed before answering anything: no tool ran, so the turn is set aside, not uncertain.

        The person sends the message again; nothing is retried behind their back. Only for a person's own turn: a
        scheduled one stays as it is, for the schedule's own recovery.
        """
        context = self.current.context()
        origin = context.authentication
        repo = self._crud.repository
        if origin not in _ANSWERED or context.principal.id != repo.principal_id:
            return  # only the turn's own owner's request sets it aside
        turn = interactive_turn_id(request)
        with repo.control_writer(session) as writer:
            events = repo.control_read(session).events
            if any(e.kind in {"interactive.abandoned", "message.replied"} and e.key == turn for e in events):
                return
            if not any(e.kind == "message.accepted" and e.key == f"message-{request}" for e in events):
                return
            writer.append(
                "interactive.abandoned",
                key=turn,
                turn=turn,
                data={"message_id": request, "owner": repo.principal_id, "origin": origin},
            )

    async def answer_from_chat(self, session: str, request: str) -> UserChatAnswer:
        """Run the turn for an answer the owner gave in chat, as if typed (ADR 0160); only the relay calls it.

        Raises:
            HeinzelError: If the call isn't the owner's `self-chat` identity, or the message didn't come from chat.
        """

        def verify() -> None:
            context = self.current.context()
            events = self._crud.repository.control_read(session).events
            accepted = [e for e in events if e.kind == "message.accepted" and e.key == f"message-{request}"]
            if (
                context.authentication != "self-chat"
                or PRIVATE_WRITE not in context.permissions
                or context.principal != self.current.owner()
                or len(accepted) != 1
                or accepted[0].data.get("origin") != "self-chat"
            ):
                msg = "This message has no chat answer origin"
                raise HeinzelError(msg)

        return await self.dispatch(session, request, verify, _MAX_REQUESTS, MAX_TURN_SECONDS, user_proposals=True)

    async def _claude_model(
        self, session: str, turn: str, verify: Callable[[], None], max_requests: int, duration: int
    ) -> "tuple[Model, Callable[[], bool], BackendBinding]":
        """The selected Claude Code subscription as the turn's model, with its continuity check.

        Raises:
            HeinzelError: If the selected executable is unavailable or not signed in.
        """
        initial, runner = self.selected()
        found = self.which(runner.claude_executable)
        if found is None:
            msg = "The selected Claude Code executable is unavailable"
            raise HeinzelError(msg)
        executable, digest = await asyncio.to_thread(self.executable, found)
        await asyncio.to_thread(self._login, executable)
        from heinzel.modules.heinzel._claude_subscription_model import (  # noqa: PLC0415 - model construction after owner and access checks
            ClaudeSubscriptionModel,
            SubscriptionAdmission,
        )

        policy = SubscriptionAdmission(self.current.principal().id, session, 1, max_requests, duration, _MAX_OUTPUT)

        def unchanged() -> bool:
            verify()
            current, selected = self.selected()
            resolved = self.which(selected.claude_executable)
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
        return model, unchanged, BackendBinding(backend="claude-code", mode="cli-model", revision=1)

    async def _plugin_model(
        self, plugin: "PluginTurnChoice", verify: Callable[[], None], cause: UsageCause
    ) -> "tuple[Model, Callable[[], bool], BackendBinding, PluginTurnGuard]":
        """The plugin's own Pydantic AI model and hz's capability for the turn (ADR 0159).

        Raises:
            HeinzelError: If the plugin can't build the model in time, or returns no Pydantic AI model.
        """
        from heinzel.modules.heinzel._plugin_turn import guard  # noqa: PLC0415 - model execution only during a turn

        held = plugin.held()

        def unchanged() -> bool:
            verify()
            return self._plugin_held() == held

        def still() -> None:
            if not unchanged():
                msg = "The selected AI access or model changed during this turn"
                raise HeinzelError(msg)

        try:
            model = await asyncio.wait_for(asyncio.to_thread(build_model, plugin.source, plugin.offer), BUILD_TIMEOUT)
        except TimeoutError:
            msg = f"The plugin {plugin.source.distribution} took longer than {BUILD_TIMEOUT:g}s to build the model"
            raise HeinzelError(msg) from None
        hook = guard(
            access=plugin.source.name,
            offer=plugin.offer,
            effort=plugin.effort,
            verify=still,
            record=self._usage or (lambda _entry: None),
            cause=cause,
        )
        binding = BackendBinding(
            backend=plugin.source.name, mode="api", model=plugin.offer.id, effort=plugin.effort, revision=1
        )
        return model, unchanged, binding, hook

    def _document_drafts(
        self, session: str, turn: str, connected: ConnectedReadGrants | None, writer: SessionWriter
    ) -> DocumentDraftTools | None:
        """Offer inert proposals only through the source-bound default-off write service."""
        kernel = self._playbook_kernel
        if connected is None or kernel is None:
            return None
        try:
            selected = kernel.resolve(SelectedWriteService)
            selected.check_available()
        except HeinzelError:
            return None
        writes_to = selected.registration.adapter

        def approved_reference() -> str | None:
            try:
                grant = connected.approved(session)
                connected.verify_approved(session, grant)
            except HeinzelError:
                return None
            ref = grant.review.fixed_arguments.get("ref")
            if grant.capability != "documents" or grant.method != "read" or not isinstance(ref, str):
                return None
            # Drafts are written by the default documents connector: offer them only for a document it holds, never
            # for one another connector that is on holds (ADR 0161).
            try:
                routed = kernel.capability_route("documents", "read", {"ref": ref})
            except HeinzelError:
                return None
            return ref if routed in {None, writes_to} else None

        return DocumentDraftTools(
            self._crud,
            self.current,
            selected,
            session=session,
            turn=turn,
            writer=writer,
            approved_reference=approved_reference,
        )

    def turn_state(self, session: str, request: str) -> UserTurnState:
        """Read the exact turn state without retrying or claiming an unresolved process has stopped.

        Raises:
            HeinzelError: If the journal contains an inconsistent resolution.
        """
        self._owner(session, request, allow_daemon_origin=True)
        repo = self._crud.repository
        turn = interactive_turn_id(request)
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
                (unstarted and (starts or accepted.data.get("origin") != "daemon" or event.seq <= accepted.seq))
                or (
                    not unstarted
                    and (len(starts) != 1 or event.seq <= starts[0].seq or starts[0].data.get("message_id") != request)
                )
                or event.data.get("message_id") != request
                or event.data.get("owner") != repo.principal_id
                or event.data.get("origin") not in _ANSWERED
            ):
                msg = "This turn has an invalid resolution record"
                raise HeinzelError(msg)
            status = "abandoned"
        elif replies:
            self._crud.read_message(session, replies[0])
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
        turn = interactive_turn_id(request)
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
                        or resolutions[0].data.get("origin") not in _ANSWERED
                        or resolutions[0].data.get("unstarted", False) != unstarted
                        or resolutions[0].seq <= accepted.seq
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
                        "origin": self.current.context().authentication,
                        **({"unstarted": True} if unstarted else {}),
                    },
                )
        return UserTurnResolution(session_id=session, request_id=request)

    def activity(self, session: str, limit: int = 30) -> UserToolActivity:
        """Return only owner-bound host-observed tool activity."""
        return UserToolActivity(
            session_id=session, items=TurnActivityProjection(self._crud).activity(session, limit=limit)
        )
