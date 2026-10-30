"""Owner-bound inert schedule suggestions from an authenticated personal chat turn.

These records are suggestions only: no scheduler, approval issuer, provider or external effect is invoked.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Literal, Never
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from heinzel import HeinzelError, SessionWriter
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId

_CRON_FIELDS = 5


def _invalid() -> Never:
    raise ValueError


class SuggestedRead(BaseModel):
    """An unverified capability reference, not a grant or provider selection."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    capability: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64, description="Suggested capability name; not a grant."
    )
    method: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Suggested read method; not verified.")


class SuggestedTrigger(BaseModel):
    """An inert five-field cron expression and its explicit IANA timezone."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    cron: str = Field(
        min_length=1,
        max_length=100,
        description="Suggested five-field cron expression, not necessarily supported by the scheduler.",
    )
    timezone: str = Field(
        min_length=1, max_length=128, description="Explicit IANA timezone for the suggested expression."
    )

    @field_validator("cron")
    @classmethod
    def valid_cron(cls, value: str) -> str:
        """Reject shorthand, seconds and unsupported cron expressions.

        Raises:
            ValueError: If the expression is malformed.
        """
        if not value.isascii() or len(value.split()) != _CRON_FIELDS or not croniter.is_valid(value):
            msg = "The suggested trigger must be a valid five-field cron expression"
            raise ValueError(msg)
        return " ".join(value.split())

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        """Require a real IANA timezone without guessing the user's timezone.

        Raises:
            ValueError: If the timezone is unknown.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            msg = "The suggested timezone is invalid"
            raise ValueError(msg) from None
        return value


class ScheduleSuggestionRequest(BaseModel):
    """Exact chat-suggested terms; this has no activation or access authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    expected_revision: int = Field(ge=0, description="Current suggestion revision, or zero for the first.")
    trigger: SuggestedTrigger = Field(description="Suggested schedule; no activation is performed.")
    suggested_reads: list[SuggestedRead] = Field(
        max_length=16, description="Unverified suggested capabilities, not granted access."
    )


class ScheduleSuggestion(BaseModel):
    """Stored owner-private, revisioned schedule suggestion with explicit inert status."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    session_id: SessionId = Field(description="Owner-private durable agent session.")
    request_id: RequestId = Field(description="Stable host-derived suggestion identity.")
    revision: int = Field(ge=1, description="Suggestion revision.")
    based_on_revision: int = Field(ge=0, description="Revision the suggestion amended.")
    trigger: SuggestedTrigger = Field(description="Suggested schedule, not yet approved or activated.")
    suggested_reads: list[SuggestedRead] = Field(max_length=16, description="Unverified suggested read capabilities.")
    status: Literal["suggested"] = Field(default="suggested", description="Inert suggestion, without authority.")


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def latest_schedule_suggestion(crud: SessionCrud, session: str) -> ScheduleSuggestion | None:
    """Read the current private suggestion without accepting or activating it.

    Raises:
        HeinzelError: If the saved session and request identities are inconsistent.
    """
    events = crud.repository.control_read(session).events
    event = next((item for item in reversed(events) if item.kind == "schedule-suggestion.proposed"), None)
    if event is None:
        return None
    suggestion = ScheduleSuggestion.model_validate(crud.read_body(session, event))
    call = event.data.get("call")
    expected = "turn-" + hashlib.sha256(f"{session}\0{event.turn}\0{call}\0schedule:propose".encode()).hexdigest()
    if (
        suggestion.session_id != session
        or suggestion.request_id != event.key
        or suggestion.request_id != expected
        or event.turn is None
        or type(call) is not int
        or call < 1
        or suggestion.revision != suggestion.based_on_revision + 1
    ):
        msg = "The private schedule suggestion is inconsistent"
        raise HeinzelError(msg)
    return suggestion


def schedule_suggestion_schemas() -> list[dict[str, JsonValue]]:
    """Describe inert suggestions; host user-origin and writer checks remain mandatory."""
    return [
        {"name": "schedule:current", "schema": _Empty.model_json_schema()},
        {"name": "schedule:propose", "schema": ScheduleSuggestionRequest.model_json_schema()},
    ]


class ScheduleSuggestionTools:
    """Derive owner, session, request and authorization exclusively from a trusted host turn."""

    def __init__(  # noqa: PLR0913, PLR0917 - trusted turn boundary
        self,
        crud: SessionCrud,
        session: str,
        turn: str,
        owner: str,
        writer: SessionWriter,
        current_policy: Callable[[], bool],
    ) -> None:
        self._crud, self._session, self._turn, self._owner, self._writer = crud, session, turn, owner, writer
        self._policy = current_policy

    def _check(self) -> None:
        repo = self._crud.repository
        if (
            repo.principal_id != self._owner
            or not repo.is_main_writer(self._session, self._writer)
            or not self._policy()
        ):
            msg = "The private schedule suggestion is unavailable"
            raise HeinzelError(msg)

    def _current(self) -> ScheduleSuggestion | None:
        return latest_schedule_suggestion(self._crud, self._session)

    def dispatch(self, name: str, arguments: dict[str, JsonValue], call: int) -> str:
        """Store/replay a suggestion or return its exact current terms, without granting authority.

        Raises:
            HeinzelError: If the owner, turn, request or revision cannot be verified.
        """
        self._check()
        try:
            if name == "schedule:current":
                _Empty.model_validate(arguments)
                current = self._current()
                return json.dumps(current.model_dump(mode="json") if current else None, separators=(",", ":"))
            if name != "schedule:propose" or type(call) is not int or call < 1:
                _invalid()
            draft = ScheduleSuggestionRequest.model_validate(arguments)
            if len({(item.capability, item.method) for item in draft.suggested_reads}) != len(draft.suggested_reads):
                _invalid()
            request = "turn-" + hashlib.sha256(f"{self._session}\0{self._turn}\0{call}\0{name}".encode()).hexdigest()
            repo = self._crud.repository
            with repo.control_writer(self._session) as writer:
                events = repo.control_read(self._session).events
                matches = [e for e in events if e.kind == "schedule-suggestion.proposed" and e.key == request]
                if matches:
                    if len(matches) != 1:
                        _invalid()
                    previous = ScheduleSuggestion.model_validate(self._crud.read_body(self._session, matches[0]))
                    expected = ScheduleSuggestion(
                        session_id=self._session,
                        request_id=request,
                        revision=previous.revision,
                        based_on_revision=draft.expected_revision,
                        trigger=draft.trigger,
                        suggested_reads=draft.suggested_reads,
                    )
                    if arguments_hash(previous.model_dump(mode="json")) != arguments_hash(
                        expected.model_dump(mode="json")
                    ):
                        _invalid()
                    result = previous
                else:
                    current = self._current()
                    revision = current.revision if current else 0
                    if draft.expected_revision != revision:
                        _invalid()
                    result = ScheduleSuggestion(
                        session_id=self._session,
                        request_id=request,
                        revision=revision + 1,
                        based_on_revision=revision,
                        trigger=draft.trigger,
                        suggested_reads=draft.suggested_reads,
                    )
                    body = immutable_body(writer, "schedule-suggestion", result.model_dump(mode="json"), control=True)
                    writer.append(
                        "schedule-suggestion.proposed",
                        key=request,
                        turn=self._turn,
                        data={"call": call, **body.model_dump(mode="json")},
                    )
            return json.dumps(
                {"request_id": result.request_id, "revision": result.revision, "status": result.status},
                separators=(",", ":"),
            )
        except HeinzelError:
            raise
        except Exception:  # noqa: BLE001 - private model data must not escape diagnostics
            msg = "The private schedule suggestion could not be recorded"
            raise HeinzelError(msg) from None
