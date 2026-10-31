"""Ask for a draft's approval on the owner's other channels, and verify every reply before it counts.

A reply is a claim from outside this process. It counts only if the actor is the owner's identity from that channel's
own sign-in, its digest matches the exact draft, and the request has not expired. The decision then goes through the
same typed confirmation as a button in the app; text in a reply is never read as an instruction.
"""

from datetime import datetime, timedelta
from typing import Literal

from heinzel import (
    ApprovalChannel,
    ChannelApprovalRequest,
    ChannelReplySource,
    Config,
    CurrentUser,
    HeinzelError,
    Kernel,
    SessionEvent,
    VerifiedChannelReply,
)
from heinzel.kernel import CapturedWriteDraft
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._captured_write_host import review_payload
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._session_crud import SessionCrud

_EXPIRY = timedelta(hours=24)


class ChannelApprovals(ChannelReplySource):
    """Post drafts to the channels the owner chose in `heinzel.approval_channels`, and read replies back."""

    def __init__(self, crud: SessionCrud, current: CurrentUser, kernel: Kernel) -> None:
        self.crud, self.current, self.kernel = crud, current, kernel

    def _channels(self) -> dict[str, ApprovalChannel]:
        chosen = Config.load().settings(HeinzelSettings, "heinzel").approval_channels
        available = self.kernel.implementations(ApprovalChannel)  # type: ignore[type-abstract]
        return {channel.name: channel for channel in available if channel.name in chosen}

    def post(self, draft: CapturedWriteDraft) -> None:
        """Show one draft on each chosen channel; a failure leaves the card in the app, which always works."""
        now = self.crud.clock()
        request = ChannelApprovalRequest(
            request_id=draft.request_id,
            helper=self.crud.snapshot(draft.session).name,
            action="Add this text to a document",
            preview=draft.content.get_secret_value()[:4000],
            link=draft.reference,
            arguments_sha256=arguments_hash(review_payload(draft)),
            expires_at=now + _EXPIRY,
        )
        for name, channel in self._channels().items():
            receipt = channel.post(request)
            if receipt.reference is None or receipt.status in {"refused", "unknown"}:
                continue
            with self.crud.repository.control_writer(draft.session) as writer:
                writer.append(
                    "approval-channel.posted",
                    key=f"{draft.request_id}-{name}",
                    data={
                        "channel": name,
                        "request_id": draft.request_id,
                        "reference": receipt.reference,
                        "arguments_sha256": request.arguments_sha256,
                        "expires_at": request.expires_at.isoformat(),
                    },
                )

    def _open(self, session: str) -> list[SessionEvent]:
        events = self.crud.repository.control_read(session).events
        closed = {event.key for event in events if event.kind == "approval-channel.settled"}
        return [event for event in events if event.kind == "approval-channel.posted" and event.key not in closed]

    def verified(self) -> list[VerifiedChannelReply]:
        """Replies that passed every check; each failed reply is ignored, and only its count is ever visible."""
        channels = self._channels()
        found: list[VerifiedChannelReply] = []
        if not channels:
            return found
        for head in self.crud.repository.heads():
            if head.problem:
                continue
            for posted in self._open(head.session):
                channel = channels.get(str(posted.data.get("channel")))
                if channel is None:
                    continue
                reply = self._check(channel, posted)
                if reply is not None:
                    found.append(
                        VerifiedChannelReply(
                            session=head.session,
                            request_id=reply[0],
                            decision=reply[1],
                            channel=channel.name,
                        )
                    )
        return found

    @staticmethod
    def _check(channel: ApprovalChannel, posted: SessionEvent) -> tuple[str, Literal["approve", "decline"]] | None:
        owner = channel.owner_actor()
        if owner is None:
            return None
        for reply in channel.replies([str(posted.data["reference"])]):
            if (
                reply.actor == owner
                and reply.request_id == posted.data.get("request_id")
                and reply.arguments_sha256 == posted.data.get("arguments_sha256")
                and reply.at <= datetime.fromisoformat(str(posted.data.get("expires_at")))
            ):
                return reply.request_id, reply.decision
        return None

    def settled(
        self, reply: VerifiedChannelReply, outcome: Literal["approved", "declined", "expired", "superseded"]
    ) -> None:
        """Close the request here first, so it is never acted on twice; telling the channel is best effort."""
        key = f"{reply.request_id}-{reply.channel}"
        posted = [event for event in self._open(reply.session) if event.key == key]
        with self.crud.repository.control_writer(reply.session) as writer:
            writer.append(
                "approval-channel.settled",
                key=key,
                data={"channel": reply.channel, "request_id": reply.request_id, "outcome": outcome},
            )
        channel = self._channels().get(reply.channel)
        if channel is not None and posted:
            try:
                channel.settle(str(posted[0].data["reference"]), outcome)
            except HeinzelError:
                return
