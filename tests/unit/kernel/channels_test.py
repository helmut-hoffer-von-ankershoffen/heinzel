"""Channels are found by their base class and their messages are bounded."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from heinzel import ChannelApprovalReply, ChannelNotice, ChannelReceipt, Module, NotificationChannel

pytestmark = pytest.mark.unit


class _Channel(NotificationChannel):
    name = "fixture"

    def available(self):
        return True

    def deliver(self, notice):
        return ChannelReceipt(channel=self.name, status="queued", reference=notice.key)


def test_implementations_finds_registered_channels(make_kernel):
    module = Module("fixture", "Offline fixture")
    module.service(_Channel)
    kernel = make_kernel(fixture=module)

    found = kernel.implementations(NotificationChannel)

    assert [type(item) for item in found] == [_Channel]
    notice = ChannelNotice(key="k", helper="Mira", title="Break", body="Stand up", route="/heinzel")
    assert found[0].deliver(notice).status == "queued"


def test_implementations_is_empty_without_channels(make_kernel):
    assert make_kernel().implementations(NotificationChannel) == []


def test_a_reply_must_name_a_digest_and_a_known_decision():
    reply = {
        "request_id": "r",
        "arguments_sha256": "0" * 64,
        "decision": "approve",
        "actor": "U1",
        "at": datetime(2026, 10, 31, tzinfo=UTC),
    }
    assert ChannelApprovalReply.model_validate(reply).decision == "approve"
    with pytest.raises(ValidationError):
        ChannelApprovalReply.model_validate({**reply, "decision": "maybe"})
    with pytest.raises(ValidationError):
        ChannelApprovalReply.model_validate({**reply, "arguments_sha256": "short"})
