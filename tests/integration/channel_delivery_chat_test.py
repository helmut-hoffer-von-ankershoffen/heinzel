"""Offline installed transports preserve real head confirmation and source-bound channel reply consumption."""

import json
from datetime import UTC, datetime

import pytest

from heinzel import ActionGate, Config, HeinzelError, ProposedCall
from heinzel.modules.heinzel._channel_delivery import ChannelDelivery
from tests.integration import captured_write_chat_test
from tests.integration.chat_test import AUTH

captured_client = captured_write_chat_test.captured_client
_SOURCE = captured_write_chat_test._SOURCE

pytestmark = pytest.mark.integration
_CHANNEL = r"""
from heinzel import ApprovalChannel, ChannelTarget, ChannelReceipt, ChannelApprovalReply
_STATE.update({"posts": [], "replies": [], "settles": [], "actor": "owner-one", "mutate_reply": False})
class FixtureChannel(ApprovalChannel):
    name = "fixture"
    def delivery_target(self):
        return ChannelTarget(account="fixture-account", destination="owner-inbox", scope="owner-review")
    def owner_actor(self):
        return _STATE["actor"]
    def post(self, request):
        _STATE["posts"].append(request)
        return ChannelReceipt(channel="fixture", status="delivered", reference="message-one")
    def post_reviewed(self, request, target):
        assert target == self.delivery_target()
        return self.post(request)
    def replies(self, references):
        assert references == ["message-one"]
        if _STATE["mutate_reply"]:
            Path(__file__).write_text("# transport changed during reply observation\n")
        return _STATE["replies"]
    def settle(self, reference, outcome):
        _STATE["settles"].append(outcome)
module.service(FixtureChannel)
"""


def _enable(kernel, monkeypatch):
    features = frozenset({"captured_writes", "connected_channels"})
    config = Config(files=(), data={"capabilities": {"documents": "fakewrite"}, "hz": {"features": sorted(features)}})
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    monkeypatch.setattr(kernel, "settings", kernel.settings.model_copy(update={"features": features}))


def _stage(client, session, proposal):
    drafted = client.post("/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": proposal})
    assert drafted.status_code == 200, drafted.text
    terms = client.post(
        "/api/heinzel/propose-channel-item",
        headers=AUTH,
        json={"session_id": session, "request_id": "post-one", "channel": "fixture", "write_request": "draft-one"},
    )
    assert terms.status_code == 200, terms.text
    staged = client.post(
        "/api/system/runner/chat/propose-channel-item",
        headers=AUTH,
        params={"durable_session": session, "request_id": "post-one"},
    )
    assert staged.status_code == 200, staged.text
    chat = staged.json()
    action = next(item for item in chat["actions"] if item["operation"] == "deliver-channel-item")
    return terms.json(), chat, action


@pytest.mark.parametrize("captured_client", [_SOURCE + _CHANNEL], indirect=True)
def test_human_head_consents_to_post_then_daemon_consumes_the_real_owner_reply(
    captured_client,
    monkeypatch,
):
    client, kernel, current, session, proposal, state, _source = captured_client
    _enable(kernel, monkeypatch)
    terms, chat, action = _stage(client, session, proposal)
    assert state["posts"] == []
    assert state["writes"] == 0
    assert action["arguments"]["review"]["target"]["destination"] == "owner-inbox"
    assert json.loads(action["arguments"]["review"]["approval"]["preview"])["content"] == proposal["content"]
    with current.local("mcp-stdio"), pytest.raises(HeinzelError, match="human review surface"):
        kernel.resolve(ActionGate).propose(
            ProposedCall(
                session=chat["id"],
                module="heinzel",
                operation="deliver-channel-item",
                tool="heinzel_deliver_channel_item",
                arguments=action["arguments"],
                command="forged posting consent",
            )
        )
    direct = client.post(
        "/api/heinzel/deliver-channel-item", headers=AUTH, json={**action["arguments"], "approval_proof": "forged"}
    )
    assert direct.status_code == 400
    decision = {"session": chat["id"], "action": action["id"]}
    assert client.post("/api/system/runner/chat/confirm", params=decision).status_code == 401
    confirmed = client.post("/api/system/runner/chat/confirm", headers=AUTH, params=decision)
    assert confirmed.status_code == 200, confirmed.text
    assert len(state["posts"]) == 1
    assert state["writes"] == 0
    from heinzel import ChannelApprovalReply

    state["replies"] = [
        ChannelApprovalReply(
            request_id=terms["approval"]["request_id"],
            arguments_sha256=terms["approval"]["arguments_sha256"],
            actor="owner-one",
            decision="approve",
            at=datetime.now(UTC),
        )
    ]
    with current.local("daemon"):
        assert kernel.resolve(ChannelDelivery).apply_channel_replies() == 1
        assert current.context().authentication == "daemon"
        assert kernel.resolve(ChannelDelivery).apply_channel_replies() == 0
    assert state["writes"] == 1
    assert state["settles"] == ["approved"]
    assert client.post("/api/system/runner/chat/confirm", headers=AUTH, params=decision).status_code == 400


@pytest.mark.parametrize("captured_client", [_SOURCE + _CHANNEL], indirect=True)
@pytest.mark.parametrize("change", ["source", "during-reply", "actor", "feature"])
def test_posted_request_never_confirms_after_transport_or_authentication_changes(
    captured_client,
    monkeypatch,
    change,
):
    client, kernel, current, session, proposal, state, source = captured_client
    _enable(kernel, monkeypatch)
    terms, chat, action = _stage(client, session, proposal)
    confirmed = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat["id"], "action": action["id"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    from heinzel import ChannelApprovalReply

    state["replies"] = [
        ChannelApprovalReply(
            request_id=terms["approval"]["request_id"],
            arguments_sha256=terms["approval"]["arguments_sha256"],
            actor="owner-one",
            decision="approve",
            at=datetime.now(UTC),
        )
    ]
    if change == "source":
        source.write_text("# changed source\n")
    elif change == "during-reply":
        state["mutate_reply"] = True
    elif change == "actor":
        state["actor"] = "other-one"
    else:
        monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: Config(files=(), data={})))
    with current.local("daemon"):
        assert kernel.resolve(ChannelDelivery).apply_channel_replies() == 0
    assert state["writes"] == 0
    assert state["settles"] == []
