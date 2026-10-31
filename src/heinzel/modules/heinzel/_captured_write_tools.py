"""The tool a turn drafts into a document with: the draft waits for its owner, and only the approved source qualifies.

Text the model read is data. A document's text asking for a write elsewhere names a document no grant covers, so the
host refuses it here, whatever the model chooses to call.
"""

import contextlib
import hashlib
import json
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel import CapturedWriteDraft
from heinzel.modules.heinzel._captured_write_binding import OPERATION, SelectedWriteService
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from heinzel.modules.heinzel._session_crud import SessionCrud

DRAFT_MARK = "DRAFT (from your Heinzelmännchen, not yet reviewed by you)"
"""What a person reading the document sees first; it carries no authority downstream."""


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section: str = Field(
        min_length=1, max_length=256, description="Exact heading text to insert after, or `end` for the document's end."
    )
    content: str = Field(min_length=1, max_length=8000, description="Plain text to insert, without any draft mark.")


def document_draft_schema() -> dict[str, JsonValue]:
    """Offered only while a document read is approved; drafting grants nothing."""
    return {
        "name": "document:draft",
        "description": "Propose text for the approved document. Your owner reviews it; nothing is written before that.",
        "schema": _Draft.model_json_schema(),
    }


class DocumentDraftTools:
    """Bind drafting to the trusted turn and the exact document of the approved read."""

    def __init__(  # noqa: PLR0913 - independent trusted host bindings
        self,
        crud: SessionCrud,
        current: CurrentUser,
        selected: SelectedWriteService,
        *,
        session: str,
        turn: str,
        approved_reference: Callable[[], str | None],
        on_draft: Callable[[CapturedWriteDraft], None] | None = None,
    ) -> None:
        self._crud, self._current, self._selected = crud, current, selected
        self._session, self._turn, self._reference, self._on_draft = session, turn, approved_reference, on_draft

    def dispatch(self, arguments: dict[str, JsonValue], call: int) -> str:
        """Record an inert draft against the document's current revision.

        Raises:
            HeinzelError: If no document read is approved or the request is invalid.
        """
        reference = self._reference()
        if reference is None:
            msg = "Drafting needs an approved document"
            raise HeinzelError(msg)
        request = _Draft.model_validate(arguments)
        target = self._selected.target(reference)
        identity = hashlib.sha256(f"{self._session}\0{self._turn}\0{call}\0document:draft".encode()).hexdigest()
        draft = CapturedWriteDraft(
            owner=self._current.principal().id,
            session=self._session,
            request_id="draft-" + identity,
            provider=self._selected.record.qualname,
            binding_id=self._selected.binding_id,
            account=target.account,
            source=target.source,
            reference=target.reference,
            operation=OPERATION,
            section=request.section,
            base_revision=target.base_revision,
            write_scope=target.write_scope,
            content=SecretStr(f"{DRAFT_MARK}\n{request.content}"),
        )
        CapturedWriteHost(self._crud, self._current, self._selected).draft_in_turn(draft, self._turn)
        if self._on_draft is not None:
            with contextlib.suppress(Exception):  # another place to answer; the draft stands without it
                self._on_draft(draft)
        return json.dumps({"request_id": draft.request_id, "status": "waiting for your owner"}, separators=(",", ":"))
