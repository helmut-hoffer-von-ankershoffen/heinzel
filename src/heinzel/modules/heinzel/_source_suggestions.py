"""Owner-bound inert source suggestions; these never contact or authorize a provider."""

import hashlib
import json
from collections.abc import Callable
from typing import Literal, Never

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import HeinzelError, SessionWriter
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId

_MAX_ARGUMENT_BYTES = 8192


def _invalid() -> Never:
    raise ValueError


class SuggestedWindow(BaseModel):
    """A time window that follows each read, for a source that changes: elapsed seconds around that moment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    lookback_seconds: int = Field(ge=0, le=2678400, description="Seconds before each read to include.")
    lookahead_seconds: int = Field(ge=0, le=2678400, description="Seconds after each read to include.")
    timezone: str = Field(min_length=1, max_length=128, description="IANA timezone the person sees times in.")


class SourceSuggestionRequest(BaseModel):
    """Exact chat-suggested terms; this has no activation or access authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    expected_revision: int = Field(ge=0, description="Current suggestion revision, or zero for the first.")
    capability: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64, description="Unverified capability."
    )
    method: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Unverified read method.")
    fixed_arguments: dict[str, JsonValue] = Field(description="Exact suggested arguments; no access is granted.")
    window: SuggestedWindow | None = Field(
        default=None,
        description="For a method with a time window: the window follows each read. Leave it out of fixed_arguments.",
    )


class SourceSuggestion(BaseModel):
    """Stored private source suggestion; no access or approval is implied."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    session_id: SessionId = Field(description="Owner-private agent session.")
    request_id: RequestId = Field(description="Host-derived suggestion identity.")
    revision: int = Field(ge=1, description="Suggestion revision.")
    based_on_revision: int = Field(ge=0, description="Revision this suggestion amended.")
    capability: str = Field(description="Unverified suggested capability.")
    method: str = Field(description="Unverified suggested method.")
    fixed_arguments: dict[str, JsonValue] = Field(description="Exact suggested arguments, not provider-verified.")
    window: SuggestedWindow | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Suggested window that follows each read, not yet approved.",
    )
    status: Literal["suggested"] = Field(default="suggested", description="Inert suggestion, without authority.")


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def latest_source_suggestion(crud: SessionCrud, session: str) -> SourceSuggestion | None:
    """Read the current private suggestion without accepting or activating it.

    Raises:
        HeinzelError: If the saved session and request identities are inconsistent.
    """
    events = crud.repository.control_read(session).events
    event = next((item for item in reversed(events) if item.kind == "source-suggestion.proposed"), None)
    if event is None:
        return None
    suggestion = SourceSuggestion.model_validate(crud.read_body(session, event))
    call = event.data.get("call")
    expected = "turn-" + hashlib.sha256(f"{session}\0{event.turn}\0{call}\0source:propose".encode()).hexdigest()
    if (
        suggestion.session_id != session
        or suggestion.request_id != event.key
        or suggestion.request_id != expected
        or event.turn is None
        or type(call) is not int
        or call < 1
        or suggestion.revision != suggestion.based_on_revision + 1
    ):
        msg = "The private source suggestion is inconsistent"
        raise HeinzelError(msg)
    return suggestion


def source_suggestion_schemas() -> list[dict[str, JsonValue]]:
    """Describe inert suggestions; host user-origin and writer checks remain mandatory."""
    return [
        {"name": "source:current", "schema": _Empty.model_json_schema()},
        {"name": "source:propose", "schema": SourceSuggestionRequest.model_json_schema()},
    ]


class SourceSuggestionTools:
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
            msg = "The private source suggestion is unavailable"
            raise HeinzelError(msg)

    def _current(self) -> SourceSuggestion | None:
        return latest_source_suggestion(self._crud, self._session)

    def dispatch(self, name: str, arguments: dict[str, JsonValue], call: int) -> str:
        """Store/replay a suggestion or return its exact current terms, without granting authority.

        Raises:
            HeinzelError: If the owner, turn, request or revision cannot be verified.
        """
        self._check()
        try:
            if name == "source:current":
                _Empty.model_validate(arguments)
                current = self._current()
                return json.dumps(current.model_dump(mode="json") if current else None, separators=(",", ":"))
            if name != "source:propose" or type(call) is not int or call < 1:
                _invalid()
            draft = SourceSuggestionRequest.model_validate(arguments)
            encoded = json.dumps(draft.fixed_arguments, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            if len(encoded.encode()) > _MAX_ARGUMENT_BYTES:
                _invalid()
            request = "turn-" + hashlib.sha256(f"{self._session}\0{self._turn}\0{call}\0{name}".encode()).hexdigest()
            repo = self._crud.repository
            with repo.control_writer(self._session) as writer:
                events = repo.control_read(self._session).events
                matches = [e for e in events if e.kind == "source-suggestion.proposed" and e.key == request]
                if matches:
                    if len(matches) != 1:
                        _invalid()
                    previous = SourceSuggestion.model_validate(self._crud.read_body(self._session, matches[0]))
                    expected = SourceSuggestion(
                        session_id=self._session,
                        request_id=request,
                        revision=previous.revision,
                        based_on_revision=draft.expected_revision,
                        capability=draft.capability,
                        method=draft.method,
                        fixed_arguments=draft.fixed_arguments,
                        window=draft.window,
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
                    result = SourceSuggestion(
                        session_id=self._session,
                        request_id=request,
                        revision=revision + 1,
                        based_on_revision=revision,
                        capability=draft.capability,
                        method=draft.method,
                        fixed_arguments=draft.fixed_arguments,
                        window=draft.window,
                    )
                    body = immutable_body(writer, "source-suggestion", result.model_dump(mode="json"), control=True)
                    writer.append(
                        "source-suggestion.proposed",
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
            msg = "The private source suggestion could not be recorded"
            raise HeinzelError(msg) from None
