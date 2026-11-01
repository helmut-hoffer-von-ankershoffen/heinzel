"""Ports other channels plug into: where a helper's notices and approval requests can reach its owner (ADR 0117).

A channel is an ordinary service a plugin registers with `module.service(MyChannel)`, a subclass of
`NotificationChannel`, `ApprovalChannel` or both; `Kernel.implementations` finds them. The core names no channel.

A channel carries words, it never decides. Which helper may use which channel is reviewed in that helper's schedule.
An `ApprovalReply` is a claim from outside this process: the host checks the actor against the identity the owner
linked for that channel, and the digest and expiry against the exact pending request, before it consumes any approval.
A reply that fails any check is recorded and ignored, and text inside it never grants anything.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChannelNotice(_Message):
    """One notice for the owner; `key` is stable across retries, so a channel can send it at most once."""

    key: str = Field(min_length=1, max_length=128, description="Stable idempotency key for this notice.")
    helper: str = Field(min_length=1, max_length=100, description="The helper's name, shown as the sender.")
    title: str = Field(min_length=1, max_length=120, description="Short headline.")
    body: str = Field(max_length=500, description="Plain text; helper-written, may repeat untrusted source text.")
    route: str = Field(description="Path in the web app that opens this helper's chat, such as /heinzel?chat=hz-…")


class ChannelReceipt(_Message):
    """What a channel observed; never upgrade `unknown` to `delivered` by guessing."""

    channel: str = Field(description="The channel's name.")
    status: Literal["delivered", "queued", "refused", "unknown"] = Field(
        description="`delivered`: the channel confirmed it. `queued`: accepted, not confirmed shown. "
        "`refused`: not sent, safe to retry. `unknown`: may have been sent, never retried automatically."
    )
    reference: str | None = Field(default=None, max_length=256, description="The channel's id for the posted item.")
    detail: str | None = Field(default=None, max_length=200, description="A short reason, with no secrets.")


class ChannelApprovalRequest(_Message):
    """One decision for the owner, bound to exact arguments by digest and limited in time."""

    request_id: str = Field(min_length=1, max_length=128, description="Stable identity of this pending approval.")
    helper: str = Field(min_length=1, max_length=100, description="The helper asking.")
    action: str = Field(min_length=1, max_length=100, description="What would happen, in a few plain words.")
    preview: str = Field(max_length=4000, description="The exact draft or terms to be approved, as plain text.")
    link: str | None = Field(default=None, description="Where to read the full draft, when it lives elsewhere.")
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Digest of the exact arguments.")
    expires_at: AwareDatetime = Field(description="After this, a reply is ignored and the request must be re-issued.")


class ChannelApprovalReply(_Message):
    """A decision observed on a channel; a claim the host verifies, not an approval."""

    request_id: str = Field(description="The pending approval this answers.")
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="The digest shown to the person.")
    decision: Literal["approve", "decline"] = Field(description="What the person chose.")
    actor: str = Field(min_length=1, max_length=256, description="The channel's stable user id of who answered.")
    at: AwareDatetime = Field(description="When they answered, as the channel reports it.")
    reason: str | None = Field(default=None, max_length=500, description="Optional words; data, never instructions.")


class NotificationChannel(ABC):
    """Somewhere a notice can reach the owner: a native banner, a direct message."""

    name: str
    """Stable lower-case identity, shown when a schedule is reviewed; the owner picks channels by this name."""

    @abstractmethod
    def available(self) -> bool:
        """Whether this channel is signed in and usable now, without network side effects beyond a status check."""

    @abstractmethod
    def deliver(self, notice: ChannelNotice) -> ChannelReceipt:
        """Send one notice. Must not raise for a delivery failure: return `refused` or `unknown` instead."""


class ApprovalChannel(ABC):
    """Somewhere the owner can read a draft and answer: an app button, a message with reply buttons."""

    name: str
    """Stable lower-case identity; the owner links their identity on it before a reply can count."""

    @abstractmethod
    def owner_actor(self) -> str | None:
        """The owner's stable user id on this channel from its own sign-in, or None when not signed in."""

    @abstractmethod
    def post(self, request: ChannelApprovalRequest) -> ChannelReceipt:
        """Show one request; idempotent by `request_id`, and its `reference` is what `replies` and `settle` take."""

    @abstractmethod
    def replies(self, references: Sequence[str]) -> list[ChannelApprovalReply]:
        """Decisions seen so far on these posted requests, oldest first; repeats are fine, the host deduplicates."""

    @abstractmethod
    def settle(self, reference: str, outcome: Literal["approved", "declined", "expired", "superseded"]) -> None:
        """Mark a posted request as no longer open, best effort, so nobody answers it again."""


class VerifiedChannelReply(_Message):
    """A reply the host verified against the owner's channel identity and the exact pending request."""

    session: str = Field(description="The helper's session.")
    request_id: str = Field(description="The pending approval it answers.")
    decision: Literal["approve", "decline"] = Field(description="What the owner chose.")
    channel: str = Field(description="Where they answered.")


class ChannelReplySource(ABC):
    """What the confirmation boundary reads to act on verified replies; the host implements it, not a plugin."""

    @abstractmethod
    def verified(self) -> list[VerifiedChannelReply]:
        """Replies that passed every check and are not settled yet."""

    @abstractmethod
    def settled(
        self, reply: VerifiedChannelReply, outcome: Literal["approved", "declined", "expired", "superseded"]
    ) -> None:
        """Record what became of a reply, and tell the channel, so it is never acted on twice."""


class ChannelReplyConsumer(ABC):
    """The trusted confirmation boundary, which acts on verified replies; implemented by the host's chat service."""

    @abstractmethod
    def apply_channel_replies(self) -> int:
        """Act on verified replies; how many were settled."""
