"""Inert conversational suggestions for bounded fixed read sets."""

import hashlib
import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import HeinzelError, SessionWriter
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId

_MAX_ARGUMENT_BYTES = 8192


class SourceSetItem(BaseModel):
    """One unverified model-suggested fixed read."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    capability: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64, description="The suggested configured capability."
    )
    method: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="The suggested fixed read operation.")
    fixed_arguments: dict[str, JsonValue] = Field(description="Suggested arguments for the person to inspect.")


class SourceSetRequest(BaseModel):
    """Exact unverified suggestions, never a provider observation or grant."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    expected_revision: int = Field(ge=0, description="The last suggestion revision inspected by this turn.")
    entries: tuple[SourceSetItem, ...] = Field(
        min_length=1, max_length=4, description="Suggested sources only; no provider is contacted or access granted."
    )


class SourceSetSuggestion(BaseModel):
    """Owner-private inert suggestion, with host-derived identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    session_id: SessionId = Field(description="The owner's private agent conversation.")
    request_id: RequestId = Field(description="The host-derived identity of this exact suggestion.")
    revision: int = Field(ge=1, description="The recorded suggestion revision.")
    based_on_revision: int = Field(ge=0, description="The previous revision this suggestion replaces.")
    entries: tuple[SourceSetItem, ...] = Field(
        min_length=1, max_length=4, description="Unverified sources suggested for the person to select and review."
    )
    status: Literal["suggested"] = Field(default="suggested", description="A draft suggestion, never an approval.")


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def latest_source_set_suggestion(crud: SessionCrud, session: str) -> SourceSetSuggestion | None:
    """Project only a consistent, owner-session-bound inert suggestion.

    Raises:
        HeinzelError: If the stored identity is inconsistent.
    """
    events = crud.repository.control_read(session).events
    event = next((item for item in reversed(events) if item.kind == "source-set-suggestion.proposed"), None)
    if event is None:
        return None
    suggestion = SourceSetSuggestion.model_validate_json(json.dumps(crud.read_body(session, event)))
    call = event.data.get("call")
    expected = "turn-" + hashlib.sha256(f"{session}\0{event.turn}\0{call}\0source-set:propose".encode()).hexdigest()
    if (
        suggestion.session_id != session
        or suggestion.request_id != event.key
        or suggestion.request_id != expected
        or event.turn is None
        or type(call) is not int
        or call < 1
        or suggestion.revision != suggestion.based_on_revision + 1
    ):
        msg = "The private source-set suggestion is inconsistent"
        raise HeinzelError(msg)
    return suggestion


def source_set_suggestion_schemas() -> list[dict[str, JsonValue]]:
    """Describe draft-only tools; the host enforces owner and turn authority."""
    return [
        {"name": "source-set:current", "schema": _Empty.model_json_schema()},
        {"name": "source-set:propose", "schema": SourceSetRequest.model_json_schema()},
    ]


class SourceSetSuggestionTools:
    """Store only a draft from an authorized host turn; never observe a provider."""

    def __init__(  # noqa: PLR0913, PLR0917 - trusted turn boundary
        self, crud: SessionCrud, session: str, turn: str, owner: str, writer: SessionWriter, policy: Callable[[], bool]
    ) -> None:
        self.crud, self.session, self.turn, self.owner, self.writer, self.policy = (
            crud,
            session,
            turn,
            owner,
            writer,
            policy,
        )

    def dispatch(self, name: str, arguments: dict[str, JsonValue], call: int) -> str:
        """Return or save an exact versioned draft under the active writer.

        Raises:
            HeinzelError: If authority or draft validation fails.
            ValueError: If the supplied draft cannot be parsed.
        """
        repo = self.crud.repository
        if repo.principal_id != self.owner or not repo.is_main_writer(self.session, self.writer) or not self.policy():
            msg = "The private source-set suggestion is unavailable"
            raise HeinzelError(msg)
        try:
            if name == "source-set:current":
                _Empty.model_validate(arguments)
                current = latest_source_set_suggestion(self.crud, self.session)
                return json.dumps(current.model_dump(mode="json") if current else None, separators=(",", ":"))
            if name != "source-set:propose" or type(call) is not int or call < 1:
                raise ValueError  # noqa: TRY301
            draft = SourceSetRequest.model_validate_json(json.dumps(arguments, allow_nan=False))
            encoded = json.dumps(
                draft.model_dump(mode="json"), ensure_ascii=True, allow_nan=False, separators=(",", ":")
            )
            if len(encoded.encode()) > _MAX_ARGUMENT_BYTES:
                raise ValueError  # noqa: TRY301
            request = "turn-" + hashlib.sha256(f"{self.session}\0{self.turn}\0{call}\0{name}".encode()).hexdigest()
            result = self._record(request, draft, call)
            return json.dumps(
                {"request_id": result.request_id, "revision": result.revision, "status": result.status},
                separators=(",", ":"),
            )
        except HeinzelError:
            raise
        except Exception:  # noqa: BLE001 - model data is private
            msg = "The private source-set suggestion could not be recorded"
            raise HeinzelError(msg) from None

    def _record(self, request: str, draft: SourceSetRequest, call: int) -> SourceSetSuggestion:
        """Store or replay a suggestion.

        Raises:
            ValueError: If the draft revision or replay is inconsistent.
        """
        repo = self.crud.repository

        with repo.control_writer(self.session) as writer:
            events = repo.control_read(self.session).events
            matches = [
                event for event in events if event.kind == "source-set-suggestion.proposed" and event.key == request
            ]
            if len(matches) > 1:
                raise ValueError
            if matches:
                previous = SourceSetSuggestion.model_validate_json(
                    json.dumps(self.crud.read_body(self.session, matches[0]))
                )
                result = SourceSetSuggestion(
                    session_id=self.session,
                    request_id=request,
                    revision=previous.revision,
                    based_on_revision=draft.expected_revision,
                    entries=draft.entries,
                )
                if arguments_hash(previous.model_dump(mode="json")) != arguments_hash(result.model_dump(mode="json")):
                    raise ValueError
            else:
                current = latest_source_set_suggestion(self.crud, self.session)
                revision = current.revision if current else 0
                if draft.expected_revision != revision:
                    raise ValueError
                result = SourceSetSuggestion(
                    session_id=self.session,
                    request_id=request,
                    revision=revision + 1,
                    based_on_revision=revision,
                    entries=draft.entries,
                )
                body = immutable_body(writer, "source-set-suggestion", result.model_dump(mode="json"), control=True)
                writer.append(
                    "source-set-suggestion.proposed",
                    key=request,
                    turn=self.turn,
                    data={"call": call, **body.model_dump(mode="json")},
                )
            return result
