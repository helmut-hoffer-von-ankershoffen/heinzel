"""Registered owner CLI and normal confirmation actions for the bounded release-draft journey."""

import asyncio
import json
import re
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, JsonValue

from heinzel import (
    CurrentUser,
    HeinzelError,
    Kernel,
    Module,
    OperationLet,
    OwnerNoticeChannel,
    Prompter,
    ReviewedLinkChannel,
    ServiceLet,
)
from heinzel.kernel._approval import ApprovalConfirmation, ApprovalIssuer, arguments_hash
from heinzel.modules.heinzel._draft_workflow_journal import grant_payload, matching
from heinzel.modules.heinzel._draft_workflow_models import (
    DraftPublicationRequest,
    DraftPublicationTerms,
    DraftShareReview,
)
from heinzel.modules.heinzel._draft_workflow_notice import LocalDraftNotice
from heinzel.modules.heinzel._draft_workflow_publication import DraftWorkflowPublication
from heinzel.modules.heinzel._draft_workflow_runtime import tick
from heinzel.modules.heinzel._draft_workflow_share import DraftWorkflowShare, share_payload
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId
from heinzel.modules.heinzel._user_chat import UserChatService

Identity = Annotated[SessionId, Field(description="The owning durable personal chat identity.")]
Request = Annotated[RequestId, Field(description="Stable identity for this exact unchanged request.")]
DraftKey = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="The stable host-derived release draft key.")]
Review = Annotated[dict[str, JsonValue], Field(description="The complete exact terms displayed at human review.")]
Proof = Annotated[
    str, Field(description="Private proof supplied only by the trusted in-process confirmation boundary.")
]


def _channel(kernel: Kernel, contract: type[OwnerNoticeChannel] | type[ReviewedLinkChannel]) -> str:
    if contract is OwnerNoticeChannel:
        return kernel.resolve_builtin_service(LocalDraftNotice, "heinzel").name
    names = [
        kernel.resolve(let.provides).name
        for _record, let in kernel.lets(ServiceLet)
        if let.provides is not contract and issubclass(let.provides, contract)
    ]
    if len(names) != 1:
        msg = "Configure exactly one private owner notice channel and one reviewed link channel before publication"
        raise HeinzelError(msg)
    return names[0]


def prompt_request(kernel: Kernel, request: str, prompt: str) -> DraftPublicationRequest:
    """Extract explicit references from one owner prompt without allowing model-selected authority.

    Raises:
        HeinzelError: If the owner prompt lacks an unambiguous name, changelog and rolling-document link.
    """
    urls: list[str] = re.findall(r"https://[^\s<>\"']+", prompt)
    urls = [url.rstrip(".,;)") for url in urls]
    feeds = [url for url in urls if "changelog" in urlsplit(url).path.lower()]
    documents = [url for url in urls if url not in feeds]
    name = re.search(r"\b(?:named|called|name)\s+([A-Za-z][A-Za-z0-9_-]{0,63})", prompt, re.IGNORECASE)
    if len(feeds) != 1 or len(documents) != 1 or name is None:
        msg = "Supply one prompt naming your Heinzel (named Turing), one HTTPS changelog link and one document link"
        raise HeinzelError(msg)
    return DraftPublicationRequest(
        request_id=request,
        name=name.group(1),
        prompt=prompt,
        feed_reference=feeds[0],
        document_reference=documents[0],
        section="release-poems",
        publication_channel=_channel(kernel, ReviewedLinkChannel),
        notice_channel=_channel(kernel, OwnerNoticeChannel),
    )


def _confirm(kernel: Kernel, prompter: Prompter, operation: str, session: str, request: str) -> object:
    current = kernel.resolve(CurrentUser)
    if current.context().authentication != "cli" or not prompter.interactive:
        msg = "Review this action in an interactive owner CLI; unattended publication and sharing are refused"
        raise HeinzelError(msg)
    issuer = kernel.resolve(ApprovalIssuer)
    args: dict[str, JsonValue] = {"session_id": session, "request_id": request}
    review = issuer.review("heinzel", operation, args)
    prompter.say(json.dumps(review, ensure_ascii=False, indent=2))
    if not prompter.confirm("Approve these exact terms?", default=False):
        msg = "The owner declined; no permission or external effect was added"
        raise HeinzelError(msg)
    args["review"] = review
    proof = issuer.issue("heinzel", operation, args, "cli-publish:" + request[:100])
    args["approval_proof"] = proof
    marker = kernel.resolve(ApprovalConfirmation)
    operation_let = next(
        let for record, let in kernel.lets(OperationLet) if record.qualname == "heinzel" and let.name == operation
    )
    with marker.using("heinzel", operation, args):
        return kernel.invoke(operation_let, **args)


def register_draft_workflow_operations(module: Module) -> None:
    """Declare the complete CLI journey and the same exact trusted web/chat approval operations."""
    module.service(LocalDraftNotice)
    module.service(DraftWorkflowPublication)
    module.service(DraftWorkflowShare)

    @module.operation(private=True, read_only=False, heads={"cli"}, audience="everyday")
    def start_release_workflow(  # noqa: PLR0913, PLR0917 - exact inputs and injected authority services
        kernel: Kernel,
        service: SessionCrud,
        chat: UserChatService,
        host: DraftWorkflowPublication,
        prompter: Prompter,
        request_id: Request,
        prompt: Annotated[
            str,
            Field(
                min_length=1,
                max_length=32000,
                description="The exact first chat message, including name and both supplied links.",
            ),
        ],
    ) -> DraftPublicationTerms:
        """Create a named Heinzel from one prompt, then review and publish its bounded recurring draft grant."""
        host.owner(human=True)
        request = prompt_request(kernel, request_id, prompt)
        created = service.new_chat(request_id, request.name, initial_message=prompt)
        asyncio.run(chat.answer(created.session_id, request_id))
        host.propose(created.session_id, request)
        _confirm(kernel, prompter, "publish-draft-workflow", created.session_id, request_id)
        return host.published(created.session_id, request_id)

    @module.operation(private=True, read_only=False, heads={"cli", "web", "api-private"}, audience="everyday")
    def publish_draft_workflow(  # noqa: PLR0913, PLR0917 - exact inputs and injected authority services
        host: DraftWorkflowPublication,
        confirmation: ApprovalConfirmation,
        session_id: Identity,
        request_id: Request,
        review: Review,
        approval_proof: Proof,
    ) -> DraftPublicationTerms:
        """Publish only the exact standing draft grant approved through the trusted human boundary."""
        return host.publish(session_id, request_id, review, approval_proof, confirmation)

    @module.operation(private=True, read_only=False, heads={"cli", "web", "api-private"}, audience="everyday")
    def review_release_draft(
        service: DraftWorkflowShare, session_id: Identity, draft_key: DraftKey
    ) -> DraftShareReview:
        """Capture the human-edited marked section and revision for exact share review; send nothing."""
        return service.propose(session_id, draft_key)

    @module.operation(private=True, read_only=False, heads={"cli"}, audience="everyday")
    def approve_release_draft(
        kernel: Kernel, service: DraftWorkflowShare, prompter: Prompter, session_id: Identity, draft_key: DraftKey
    ) -> str:
        """Review the current edited section and explicitly approve one document-link delivery.

        Raises:
            HeinzelError: If the human declines or exact delivery is unavailable.
        """
        review = service.propose(session_id, draft_key)
        result = _confirm(kernel, prompter, "share-draft-link", session_id, arguments_hash(share_payload(review)))
        if not isinstance(result, str):
            msg = "The link-sharing result is unavailable"
            raise HeinzelError(msg)
        return result

    @module.operation(private=True, read_only=False, heads={"cli", "web", "api-private"}, audience="everyday")
    def share_draft_link(  # noqa: PLR0913, PLR0917 - exact inputs and injected authority services
        service: DraftWorkflowShare,
        confirmation: ApprovalConfirmation,
        session_id: Identity,
        request_id: Request,
        review: Review,
        approval_proof: Proof,
    ) -> str:
        """Deliver one document link for the exact edited revision approved by its owner."""
        return service.execute(session_id, request_id, review, approval_proof, confirmation)

    @module.operation(private=True, heads={"cli", "web", "api-private"}, audience="everyday")
    def release_workflow(host: DraftWorkflowPublication, session_id: Identity) -> dict[str, JsonValue]:
        """Read the published workflow and durable release dispositions from this same chat."""
        host.owner(read=True)
        terms = host.published(session_id, active=False)
        return {
            "publication": grant_payload(terms),
            "events": [
                {"kind": event.kind, "key": event.key, "value": host.crud.read_body(session_id, event)}
                for event in host.journal.events(session_id)
                if event.kind.startswith("draft.") and event.kind != "draft.workflow-message"
            ],
        }

    @module.operation(private=True, read_only=False, heads={"cli", "web", "api-private"}, audience="everyday")
    def revoke_release_workflow(host: DraftWorkflowPublication, session_id: Identity, request_id: Request) -> bool:
        """Revoke future recurring draft and notice effects under the shared document fence."""
        host.revoke(session_id, request_id)
        return True


def workflow_jobs(host: DraftWorkflowPublication) -> list[tuple[str, str]]:
    """Find only actual persisted owner publications, with no inferred prompt authority."""
    result: list[tuple[str, str]] = []
    try:
        host.current_user.context()
    except HeinzelError:
        return result
    for head in host.crud.repository.heads():
        if head.problem:
            continue
        try:
            if not matching(host.journal.events(head.session), "draft.published"):
                continue
            terms = host.published(head.session)
        except HeinzelError:
            continue
        result.append((terms.session_id, terms.request.request_id))
    return result


def run_workflow(host: DraftWorkflowPublication, session: str, request: str, slot: datetime) -> JsonValue:
    """Dispatch one actual daemon slot through the registered bounded workflow runtime."""
    asyncio.run(tick(host, session, request, slot))
    return {"status": "completed"}
