"""Owner-private operations that read and set a Heinzel's intelligence level (ADR 0158).

Not on MCP: a model can't change its own level. Setting goes through the profile's exact revision and an idempotent
request id, like the name and the look.
"""

from typing import Annotated

from pydantic import BaseModel, Field

from heinzel import HeinzelError, Module
from heinzel.kernel import Effort
from heinzel.modules.heinzel._levels import HeinzelIntelligence, Level, TurnLevel
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId
from heinzel.modules.heinzel._user_chat import UserChatService

Request = Annotated[RequestId, Field(description="A stable client request id; reuse it only for an exact retry.")]
Identity = Annotated[SessionId, Field(description="The stable personal session id returned by new-chat or sessions.")]


class LevelRunsAs(BaseModel):
    """What a turn of this Heinzel would run on now: for its details, never shown in the chat."""

    provider: str | None = Field(description="The plugin's AI access option; none for the configured AI access.")
    model: str | None = Field(description="The model id; none when the provider runs its own default.")
    effort: Effort | None = Field(description="The effort passed; none when the model takes none.")
    level: TurnLevel = Field(description="The level asked, the level it runs as, the rule and how it was chosen.")


class LevelView(BaseModel):
    """A Heinzel's level, provider and exact model, the profile revision they were read at, and what they run as."""

    session_id: SessionId = Field(description="The owner's durable Heinzel.")
    level: Level = Field(description="How hard this Heinzel thinks: auto, low, medium or high.")
    provider: str | None = Field(description="Its AI provider; none for the configured AI access.")
    model: str | None = Field(
        description="Its exact model, an advanced choice that wins over the level; none for none."
    )
    profile_revision: int = Field(description="The profile revision to pass to set-level.", ge=1)
    runs_as: LevelRunsAs | None = Field(
        description="What a turn runs on now; none when it can't start, and `problem` says why."
    )
    problem: str | None = Field(description="Why a turn couldn't start on this choice now; none when it can.")


def level_view(chat: UserChatService, crud: SessionCrud, session_id: str) -> LevelView:
    """Read the Heinzel's intelligence and preview what a turn would run on, without starting one."""
    intelligence, revision = crud.intelligence(session_id)
    runs_as: LevelRunsAs | None = None
    problem: str | None = None
    try:
        plugin = chat.plugin_selected(session_id)
        if plugin is not None and plugin.level is not None:
            runs_as = LevelRunsAs(
                provider=plugin.source.name, model=plugin.offer.id, effort=plugin.effort, level=plugin.level
            )
        elif plugin is None and (record := chat.claude_level(session_id)) is not None:
            runs_as = LevelRunsAs(provider=intelligence.provider, model=None, effort=None, level=record)
    except HeinzelError as exc:
        problem = str(exc)
    return LevelView(
        session_id=session_id,
        level=intelligence.level,
        provider=intelligence.provider,
        model=intelligence.model,
        profile_revision=revision,
        runs_as=runs_as,
        problem=problem,
    )


def changed(
    current: HeinzelIntelligence, level: Level | None = None, provider: str | None = None, model: str | None = None
) -> HeinzelIntelligence:
    """`current` with the given fields set; an empty provider or model clears it.

    Raises:
        HeinzelError: If nothing is given, or a provider or model isn't a valid name.
    """
    if level is None and provider is None and model is None:
        msg = "Couldn't set this Heinzel's level: give a level, a provider or an exact model"
        raise HeinzelError(msg)
    fields = current.model_dump()
    if level is not None:
        fields["level"] = level
    if provider is not None:
        fields["provider"] = provider or None
    if model is not None:
        fields["model"] = model or None
    try:
        return HeinzelIntelligence.model_validate(fields)
    except ValueError:
        msg = "Couldn't set this Heinzel's level: the provider or model isn't a valid name"
        raise HeinzelError(msg) from None


def register_level_operations(module: Module) -> None:
    """Declare the owner-private read and set of a Heinzel's intelligence level."""

    @module.operation(private=True, heads={"cli", "web", "api-private"}, audience="everyday")
    def level(chat: UserChatService, service: SessionCrud, session_id: Identity) -> LevelView:
        """Read this Heinzel's intelligence level, provider and exact model, and what a turn runs on now."""
        return level_view(chat, service, session_id)

    @module.operation(private=True, read_only=False, heads={"cli", "web", "api-private"}, audience="everyday")
    def set_level(  # noqa: PLR0913, PLR0917 - flat private body binds request identity and profile revision
        chat: UserChatService,
        service: SessionCrud,
        session_id: Identity,
        request_id: Request,
        expected_revision: Annotated[
            int, Field(description="The exact profile_revision you last read.", strict=True, ge=1)
        ],
        level: Annotated[
            Level | None, Field(description="auto, low, medium or high; null leaves it unchanged.")
        ] = None,
        provider: Annotated[
            str | None,
            Field(
                description="An AI access option's name; empty for the configured one; null leaves it.", max_length=64
            ),
        ] = None,
        model: Annotated[
            str | None,
            Field(description="An exact model id; empty clears it; null leaves it unchanged.", max_length=200),
        ] = None,
    ) -> LevelView:
        """Set this Heinzel's level, provider or exact model against the exact profile revision you saw."""
        current, _revision = service.intelligence(session_id)
        wanted = changed(current, level, provider, model)
        service.profile(session_id, request_id, expected_revision, intelligence=wanted)
        return level_view(chat, service, session_id)
