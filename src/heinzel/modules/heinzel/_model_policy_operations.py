"""Owner-private inert model-policy review operations; no runner is exposed here."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, ValidationError

from heinzel import ConfiguredApiAccess, HeinzelError, Kernel, Module
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._priced_model import MODEL, resolve_pricing
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId


class ModelPolicyReview(BaseModel):
    """The exact inert proposal, with explicit billing semantics."""

    session_id: SessionId = Field(description="Owner-bound durable session receiving the proposal.")
    status: Literal["proposed", "approved"] = Field(
        default="proposed", description="The policy proposal or approval state."
    )
    billing: Literal["api-priced"] = Field(default="api-priced", description="Bounded standard API pricing policy.")
    proposal: dict[str, JsonValue] = Field(description="Exact model, request, revision, limits and tool fingerprints.")


class ModelPolicyService:
    """Validate and stage a policy, or consume a trusted confirmation of its exact intent."""

    def __init__(self, crud: SessionCrud, binding: ConfiguredApiAccess) -> None:
        self.crud = crud
        self.binding = binding

    @staticmethod
    def _supported(grant: ModelGrant, billing: str) -> None:
        if billing != "api-priced" or grant.access != "pydantic-ai" or grant.model != MODEL:
            msg = "This model billing policy is not supported for review"
            raise HeinzelError(msg)
        if grant.allowed_tools or grant.allowed_skill_tools:
            msg = "Tool grants require a verified catalog review that is not yet available"
            raise HeinzelError(msg)
        resolve_pricing(MODEL, "standard", datetime.now(UTC))

    def propose(self, session: str, billing: str, proposal: dict[str, JsonValue]) -> ModelPolicyReview:
        """Store an inert, versioned exact proposal; never creates approval.

        Raises:
            HeinzelError: If the proposal is invalid or unsupported.
        """
        if "binding" in proposal:
            msg = "Model policy binding is supplied only by the host"
            raise HeinzelError(msg)
        try:
            grant = ModelGrant.model_validate(proposal)
        except ValidationError:
            msg = "The model policy proposal is invalid"
            raise HeinzelError(msg) from None
        self._supported(grant, billing)
        grant = grant.model_copy(update={"binding": self.binding.capture()})
        if grant.binding is None or grant.binding.access != grant.access or grant.binding.model != grant.model:
            msg = "The model policy does not match the selected access"
            raise HeinzelError(msg)
        saved = ModelGrants(self.crud).propose(session, grant)
        status: Literal["approved", "proposed"] = (
            "approved" if ModelGrants(self.crud).applied(session, saved.request_id) else "proposed"
        )
        return ModelPolicyReview(session_id=session, status=status, proposal=saved.model_dump(mode="json"))

    def review(self, session: str, request: str) -> ModelPolicyReview:
        """Read a current owner-private proposal, without issuing a proof.

        Raises:
            HeinzelError: If the selected access differs from the reviewed binding.
        """
        grant = ModelGrants(self.crud).review(session, request)
        self._supported(grant, "api-priced")
        if grant.binding is None:
            msg = "The model policy has no verified configured access binding"
            raise HeinzelError(msg)
        self.binding.recheck(grant.binding)
        return ModelPolicyReview(session_id=session, proposal=grant.model_dump(mode="json"))

    def confirm(
        self, confirmation: ApprovalConfirmation, session: str, request: str, review: dict[str, JsonValue], proof: str
    ) -> ModelPolicyReview:
        """Consume proof only inside the exact trusted action-card confirmation context.

        Raises:
            HeinzelError: If the caller is not the trusted confirmation boundary or the grant is stale.
        """
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "approve-model-grant", args):
            msg = "Approval is invalid or unavailable"
            raise HeinzelError(msg)
        if self.review(session, request).proposal != review:
            msg = "The reviewed model policy changed"
            raise HeinzelError(msg)
        grant = ModelGrants(self.crud).approve(session, request, proof)
        return ModelPolicyReview(session_id=session, status="approved", proposal=grant.model_dump(mode="json"))


def register_model_policy_operations(module: Module) -> None:
    """Register private policy review; trusted confirmation must stage and issue its own exact proof."""

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_model_policy(
        service: SessionCrud,
        kernel: Kernel,
        session_id: Annotated[SessionId, Field(description="Owner-bound durable agent session.")],
        billing: Annotated[str, Field(description="Billing policy; only api-priced is currently supported.")],
        proposal: Annotated[dict[str, JsonValue], Field(description="Exact model grant terms and request identity.")],
    ) -> ModelPolicyReview:
        """Stage an inert model policy for independent human review."""
        return ModelPolicyService(service, kernel.resolve_builtin_service(ConfiguredApiAccess, "ai.access")).propose(
            session_id, billing, proposal
        )

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def model_policy(
        service: SessionCrud,
        kernel: Kernel,
        session_id: Annotated[SessionId, Field(description="Owner-bound durable agent session.")],
        request_id: Annotated[RequestId, Field(description="The exact proposed policy request id.")],
    ) -> ModelPolicyReview:
        """Read the current exact proposed policy; no permission is granted."""
        return ModelPolicyService(service, kernel.resolve_builtin_service(ConfiguredApiAccess, "ai.access")).review(
            session_id, request_id
        )

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def approve_model_grant(  # noqa: PLR0913, PLR0917 - exact reviewed terms and trusted proof
        service: SessionCrud,
        confirmation: ApprovalConfirmation,
        kernel: Kernel,
        session_id: Annotated[SessionId, Field(description="Owner-bound durable agent session.")],
        request_id: Annotated[RequestId, Field(description="The exact proposed policy request id.")],
        review: Annotated[dict[str, JsonValue], Field(description="Exact policy terms displayed in the action card.")],
        approval_proof: Annotated[
            str, Field(description="In-process proof issued only by trusted human confirmation.")
        ],
    ) -> ModelPolicyReview:
        """Apply an exact human-confirmed policy; direct callers cannot issue or consume approval."""
        return ModelPolicyService(service, kernel.resolve_builtin_service(ConfiguredApiAccess, "ai.access")).confirm(
            confirmation, session_id, request_id, review, approval_proof
        )
