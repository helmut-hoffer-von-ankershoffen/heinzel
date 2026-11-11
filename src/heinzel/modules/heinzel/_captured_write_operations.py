"""Owner-private actions for reviewed conditional writes into documents."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from heinzel import CurrentUser, HeinzelError, Module
from heinzel.kernel import CapturedWriteDraft, CapturedWriteService, Kernel
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.kernel._features import feature_enabled
from heinzel.modules.heinzel._captured_write_activity import CapturedWriteActivityPage, captured_write_activity
from heinzel.modules.heinzel._captured_write_binding import SelectedWriteService
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost, CapturedWriteStatus
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId


class CapturedWriteProposal(BaseModel):
    """Untrusted inert target terms; owner and provider binding are selected by the host."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: SessionId
    request_id: RequestId
    account: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=256)
    reference: str = Field(min_length=1, max_length=2048)
    operation: str = Field(min_length=1, max_length=128)
    section: str = Field(min_length=1, max_length=256)
    base_revision: str = Field(min_length=1, max_length=256)
    write_scope: str = Field(min_length=1, max_length=256)
    content: SecretStr


def _selected(kernel: Kernel) -> SelectedWriteService:
    if not feature_enabled(kernel.settings, "captured_writes"):
        msg = "Captured writes are not enabled"
        raise HeinzelError(msg)
    return SelectedWriteService(kernel)


def register_captured_write_operations(module: Module) -> None:
    """Register only exact owner-private proposal and trusted confirmation actions."""
    module.service(SelectedWriteService, _selected)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_captured_write(
        crud: SessionCrud, current: CurrentUser, selected: SelectedWriteService, proposal: CapturedWriteProposal
    ) -> str:
        """Persist a local draft without writing to a provider or granting authority."""
        draft = CapturedWriteDraft(
            owner=current.principal().id,
            session=proposal.session_id,
            request_id=proposal.request_id,
            provider=selected.record.qualname,
            binding_id=selected.binding_id,
            account=proposal.account,
            source=proposal.source,
            reference=proposal.reference,
            operation=proposal.operation,
            section=proposal.section,
            base_revision=proposal.base_revision,
            write_scope=proposal.write_scope,
            content=proposal.content,
        )
        CapturedWriteHost(crud, current, selected).draft(draft)
        return proposal.request_id

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def execute_captured_write(  # noqa: PLR0913, PLR0917 - exact confirmation and injected services
        crud: SessionCrud,
        current: CurrentUser,
        selected: SelectedWriteService,
        confirmation: ApprovalConfirmation,
        session_id: SessionId,
        request_id: RequestId,
        review: dict[str, JsonValue],
        approval_proof: Annotated[str, Field(min_length=1)],
    ) -> str:
        """Execute one exact confirmed conditional write or leave its outcome uncertain."""
        return CapturedWriteHost(crud, current, selected).execute(
            session_id, request_id, review, approval_proof, confirmation
        )

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def pending_captured_writes(
        crud: SessionCrud, current: CurrentUser, session_id: SessionId
    ) -> list[dict[str, JsonValue]]:
        """Drafts waiting for your decision, with their exact terms; reading them writes nothing."""
        return CapturedWriteHost(crud, current, CapturedWriteService()).pending(session_id)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def discard_captured_write(
        crud: SessionCrud, current: CurrentUser, session_id: SessionId, request_id: RequestId
    ) -> bool:
        """Set a draft aside without writing it."""
        CapturedWriteHost(crud, current, CapturedWriteService()).discard(session_id, request_id)
        return True

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def captured_write_status(
        crud: SessionCrud,
        current: CurrentUser,
        session_id: SessionId,
        request_id: RequestId,
    ) -> CapturedWriteStatus:
        """Read the owner's recorded disposition without repeating a provider operation."""
        return CapturedWriteHost(crud, current, CapturedWriteService()).projection(session_id, request_id)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def captured_write_activity_page(
        crud: SessionCrud,
        current: CurrentUser,
        kernel: Kernel,
        session_id: SessionId,
        before: Annotated[int | None, Field(ge=1, description="Exclusive activity sequence before this page.")] = None,
    ) -> CapturedWriteActivityPage:
        """Inspect recorded write activity without contacting a provider or authorizing effects.

        Raises:
            HeinzelError: If captured writes are disabled or the activity cannot be verified.
        """
        if not feature_enabled(kernel.settings, "captured_writes"):
            msg = "Captured writes are not enabled"
            raise HeinzelError(msg)
        return captured_write_activity(crud, current, session_id, before=before)
