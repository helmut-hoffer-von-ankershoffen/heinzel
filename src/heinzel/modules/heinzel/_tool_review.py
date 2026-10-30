"""Read-only personal-session-bound descriptor seam for a future human policy review.

These views establish no tool approval, code attestation, provider account, or authorization to invoke.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel.modules.heinzel._host_tools import ToolDescriptor


class ToolDescriptorProvider(Protocol):
    """Trusted provider must verify current principal and exact personal-session ownership on each call."""

    def review_descriptors_for(self, session: str) -> list[ToolDescriptor]:
        """Describe registered host-configured read operations for this owner-bound session."""
        ...


class ToolReviewItem(BaseModel):
    """Presentation data, including limitations; no field here confers permission."""

    model_config = ConfigDict(extra="forbid")
    identity: str
    description: str
    input_schema: dict[str, JsonValue] = Field(validation_alias="schema", serialization_alias="schema")
    binding_sha256: str
    distribution: str
    version: str
    builtin: bool
    fixed_arguments: dict[str, str | int | bool | None]
    policy_sha256: str
    implementation_sha256: str
    code_evidence: str
    scope_evidence: str
    executable: bool = Field(description="Currently eligible under this internal catalog, not an approval or grant.")


class ToolReviewPage(BaseModel):
    """A bounded point-in-time view, which must be refreshed before any later review or execution."""

    model_config = ConfigDict(extra="forbid")
    session_id: str
    items: list[ToolReviewItem] = Field(max_length=32)


class ToolReviewFacade:
    """Present exactly the owner-bound provider's current descriptors without activating any tool."""

    def __init__(self, provider: ToolDescriptorProvider) -> None:
        self._provider = provider

    def review(self, session: str) -> ToolReviewPage:
        """Read the registered descriptors; provider must enforce principal/session access each time."""
        return ToolReviewPage(
            session_id=session,
            items=[
                ToolReviewItem.model_validate(item, from_attributes=True)
                for item in self._provider.review_descriptors_for(session)
            ],
        )
