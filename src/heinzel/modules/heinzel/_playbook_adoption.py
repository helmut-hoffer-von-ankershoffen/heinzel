"""Compile installed playbook material into an inert, provenance-labelled chat draft (ADR 0109)."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import HeinzelError, Kernel, SessionWriter
from heinzel.kernel import ChatDraftTemplate, ContractTemplate, PlaybookLet
from heinzel.modules.heinzel._playbooks import playbooks
from heinzel.modules.heinzel._session_crud import SessionCrud

_MAX_TEXT = 4000
_MAX_TEMPLATES = 100
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _draft_candidates(playbook: PlaybookLet) -> tuple[ContractTemplate | ChatDraftTemplate, ...]:
    return (*playbook.templates, *playbook.chat_drafts)


@dataclass(frozen=True, slots=True)
class PlaybookChatDraft:
    """Untrusted, inert source material for review; this value conveys no permission."""

    template: str
    sha256: str
    text: str
    placeholders: tuple[str, ...]


def preview_playbook_draft(kernel: Kernel, qualified_name: str) -> PlaybookChatDraft:
    """Snapshot exactly one installed qualified template without executing or resolving it.

    Raises:
        HeinzelError: If the template is missing, ambiguous, unsafe or too large for a chat draft.
    """
    if ":" not in qualified_name:
        msg = "Choose a qualified playbook template."
        raise HeinzelError(msg)
    matches = [
        template
        for name, playbook in playbooks(kernel)
        for template in _draft_candidates(playbook)
        if f"{name}:{template.name}" == qualified_name
    ]
    if len(matches) != 1:
        msg = "The selected playbook template is unavailable or ambiguous."
        raise HeinzelError(msg)
    template = matches[0]
    source = cast("object", template.text)
    if not isinstance(source, str) or len(source) > _MAX_TEXT:
        msg = "The selected playbook template is too large or invalid."
        raise HeinzelError(msg)
    try:
        # Revalidate the exact snapshot, including the inactive-schedule rule.
        checked = (
            ChatDraftTemplate(name=template.name, text=source)
            if isinstance(template, ChatDraftTemplate)
            else ContractTemplate(name=template.name, text=source)
        )
        encoded = checked.text.encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        msg = "The selected playbook template is invalid."
        raise HeinzelError(msg) from exc
    if len(checked.text) > _MAX_TEXT or len(encoded) > _MAX_TEXT * 4 or _CONTROL.search(checked.text):
        msg = "The selected playbook template is too large or contains unsupported control characters."
        raise HeinzelError(msg)
    return PlaybookChatDraft(
        template=qualified_name,
        sha256=hashlib.sha256(encoded).hexdigest(),
        text=checked.text,
        placeholders=tuple(checked.placeholders()) if isinstance(checked, ContractTemplate) else (),
    )


def adopt_playbook_draft(kernel: Kernel, qualified_name: str, expected_sha256: str) -> PlaybookChatDraft:
    """Return the unchanged previewed material; this does not write, grant, or activate anything.

    Raises:
        HeinzelError: If the selected snapshot cannot be revalidated against the inspected digest.
    """
    if not _DIGEST.fullmatch(expected_sha256):
        msg = "Review the current playbook template before adopting it."
        raise HeinzelError(msg)
    draft = preview_playbook_draft(kernel, qualified_name)
    if draft.sha256 != expected_sha256:
        msg = "The playbook template changed; review it again."
        raise HeinzelError(msg)
    return draft


class _Preview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    template: str = Field(min_length=1, max_length=200)


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Draft(_Preview):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract: str = Field(min_length=1, max_length=3800)
    timeout_seconds: int = Field(ge=1, le=300)
    expected_revision: int = Field(ge=0)


def playbook_tool_schemas() -> list[dict[str, JsonValue]]:
    """Offer only inert template preview and draft proposal tools."""
    return [
        {"name": "playbook:list", "schema": _Empty.model_json_schema()},
        {"name": "playbook:preview", "schema": _Preview.model_json_schema()},
        {"name": "playbook:propose", "schema": _Draft.model_json_schema()},
    ]


class PlaybookDraftTools:
    """Owner-bound chat preview and inert proposal; this never activates any capability."""

    def __init__(  # noqa: PLR0913, PLR0917 - exact authenticated turn binding
        self,
        kernel: Kernel,
        crud: SessionCrud,
        session: str,
        turn: str,
        owner: str,
        writer: SessionWriter,
        current_policy: Callable[[], bool],
    ) -> None:
        self.kernel, self.crud, self.session, self.turn = kernel, crud, session, turn
        self.owner, self.writer, self.current_policy = owner, writer, current_policy

    def _check(self) -> None:
        if (
            self.crud.repository.principal_id != self.owner
            or not self.crud.repository.is_main_writer(self.session, self.writer)
            or not self.current_policy()
        ):
            msg = "This private proposal tool is unavailable"
            raise HeinzelError(msg)

    def dispatch(self, name: str, args: dict[str, JsonValue], call: int) -> str:
        """Return untrusted preview material or a reference to the inert proposal.

        Raises:
            HeinzelError: If turn authority or preview provenance cannot be established.
        """
        self._check()
        if name == "playbook:list":
            _Empty.model_validate(args)
            names = [
                f"{module}:{template.name}"
                for module, book in playbooks(self.kernel)
                for template in _draft_candidates(book)
            ]
            if len(names) > _MAX_TEMPLATES:
                msg = "Too many playbook templates to list safely"
                raise HeinzelError(msg)
            return json.dumps({"templates": names, "status": "untrusted-source"})
        if name == "playbook:preview":
            request = _Preview.model_validate(args)
            draft = preview_playbook_draft(self.kernel, request.template)
            self._check()
            with self.crud.repository.control_writer(self.session) as writer:
                writer.append(
                    "playbook.previewed",
                    key=f"{self.turn}-playbook-{call}",
                    turn=self.turn,
                    data={"template": draft.template, "sha256": draft.sha256},
                )
            return json.dumps(
                {
                    "template": draft.template,
                    "sha256": draft.sha256,
                    "text": draft.text,
                    "placeholders": draft.placeholders,
                    "status": "untrusted-source",
                }
            )
        request = _Draft.model_validate(args)
        events = self.crud.repository.control_read(self.session).events
        if not any(
            event.kind == "playbook.previewed"
            and event.turn == self.turn
            and event.data == {"template": request.template, "sha256": request.sha256}
            for event in events
        ):
            msg = "Review the installed playbook template in this turn first"
            raise HeinzelError(msg)
        draft = adopt_playbook_draft(self.kernel, request.template, request.sha256)
        header = f"Model-proposed draft after retrieving {draft.template} (SHA-256 {draft.sha256}).\n\n"
        contract = header + request.contract
        if len(contract) > _MAX_TEXT:
            msg = "The reviewed draft is too large"
            raise HeinzelError(msg)
        self._check()
        request_id = "playbook-" + hashlib.sha256(f"{self.session}\0{self.turn}\0{call}".encode()).hexdigest()
        with self.crud.repository.control_writer(self.session) as writer:
            writer.append(
                "playbook.draft-requested",
                key=request_id,
                turn=self.turn,
                data={"template": draft.template, "sha256": draft.sha256, "request_id": request_id},
            )
        if request.expected_revision:
            proposal = self.crud.amend_scheduled_agent(
                self.session, request_id, request.expected_revision, contract, None, request.timeout_seconds
            )
        else:
            proposal = self.crud.propose_scheduled_agent(
                self.session, request_id, contract, None, request.timeout_seconds
            )
        return json.dumps(
            {"request_id": proposal.request_id, "preview_sha256": proposal.preview_sha256, "status": proposal.status}
        )
