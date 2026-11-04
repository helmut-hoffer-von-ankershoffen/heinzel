"""Thin private operations for exact channel proposals, human delivery consent and bounded reply polling (ADR 0120)."""

from typing import Annotated

from pydantic import Field, JsonValue

from heinzel import ChannelNotice, ChannelReplyConsumer, Module
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._channel_delivery import ChannelDelivery, ChannelDeliveryTerms
from heinzel.modules.heinzel._session_models import RequestId, SessionId


def register_channel_operations(module: Module) -> None:
    """Register host-owned services; no transport or reply may provide approval authority."""
    module.service(ChannelDelivery)

    def consumer(delivery: ChannelDelivery) -> ChannelReplyConsumer:
        return delivery

    module.service(ChannelReplyConsumer, consumer)  # type: ignore[type-abstract]  # host implements this port

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def propose_channel_item(  # noqa: PLR0913, PLR0917 - exact proposal and injected host service
        delivery: ChannelDelivery,
        session_id: SessionId,
        request_id: RequestId,
        channel: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")],
        notice: ChannelNotice | None = None,
        write_request: RequestId | None = None,
    ) -> ChannelDeliveryTerms:
        """Stage exact posting terms for separate human consent; no message is sent."""
        return delivery.propose(session_id, request_id, channel, notice=notice, write_request=write_request)

    @module.operation(private=True, read_only=False, heads={"web", "api-private"}, audience="everyday")
    def deliver_channel_item(  # noqa: PLR0913, PLR0917 - exact confirmation and injected services
        delivery: ChannelDelivery,
        confirmation: ApprovalConfirmation,
        session_id: SessionId,
        request_id: RequestId,
        review: dict[str, JsonValue],
        approval_proof: Annotated[str, Field(min_length=1)],
    ) -> str:
        """Post one exact human-confirmed message; posting consent does not approve the draft."""
        return delivery.deliver(session_id, request_id, review, approval_proof, confirmation)

    @module.operation(private=True, heads={"web", "api-private"}, audience="everyday")
    def channel_item_status(delivery: ChannelDelivery, session_id: SessionId, request_id: RequestId) -> str:
        """Read the recorded posting outcome without retrying it."""
        return delivery.status(session_id, request_id)

    @module.job(schedule="* * * * *")
    def consume_channel_replies(delivery: ChannelDelivery) -> int:
        """Consume genuine owner decisions on previously consented exact channel previews."""
        return delivery.apply_channel_replies()
