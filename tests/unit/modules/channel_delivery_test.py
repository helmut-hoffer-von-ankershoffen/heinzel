"""Offline channel decisions require separate posting consent and genuine pinned owner replies."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from heinzel import ChannelApprovalReply, ChannelNotice, ChannelReceipt, ChannelTarget, HeinzelError
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel import _channel_delivery
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from heinzel.modules.heinzel._channel_delivery import ChannelDelivery
from tests.unit.modules.captured_write_host_test import _setup

pytestmark = pytest.mark.unit


class FakeChannel:
    def __init__(self):
        self.name = "fixture"
        self.target = ChannelTarget(account="own-account", destination="own-inbox", scope="post-own")
        self.actor = "owner-actor"
        self.source = "a" * 64
        self.posted = []
        self.observed = []
        self.settlements = []
        self.lose_ack = False
        self.settle_error = False

    def post(self, request):
        self.posted.append(request)
        if self.lose_ack:
            msg = "may have sent; private provider details"
            raise OSError(msg)
        return ChannelReceipt(channel=self.name, status="delivered", reference="posted-one")

    def post_reviewed(self, request, target):
        assert target == self.target
        return self.post(request)

    def deliver_reviewed(self, notice, target):
        assert target == self.target
        return self.post(notice)

    def replies(self, references):
        assert references == ["posted-one"]
        return self.observed

    def settle(self, reference, outcome):
        assert reference == "posted-one"
        self.settlements.append(outcome)
        if self.settle_error:
            msg = "settle failed"
            raise OSError(msg)


@pytest.fixture
def flow(tmp_path, monkeypatch):
    context, current, crud, draft, provider, host = _setup(tmp_path)
    now = datetime(2030, 1, 1, tzinfo=UTC)
    crud.clock = lambda: now
    crud.repository.clock = crud.clock
    channel = FakeChannel()

    class Binding:
        def __init__(self, kernel, name, kind):
            assert name == channel.name
            self.binding_id = channel.source

        def target(self):
            return channel.target

        def actor(self):
            return channel.actor

        def call(self, action):
            return action(channel)

    monkeypatch.setattr(_channel_delivery, "ChannelBinding", Binding)
    service = ChannelDelivery(crud, current, SimpleNamespace(settings=SimpleNamespace(features={"connected_channels"})))
    monkeypatch.setattr(
        service,
        "_write_host",
        lambda marker=None: CapturedWriteHost(crud, current, provider, channel_confirmation=marker),
    )
    host.draft(draft)
    yield SimpleNamespace(
        service=service, current=current, crud=crud, draft=draft, host=host, channel=channel, provider=provider, now=now
    )
    context.__exit__(None, None, None)


def _propose(flow):
    return flow.service.propose(flow.draft.session, "post-one", "fixture", write_request=flow.draft.request_id)


def _post(flow, terms):
    marker = ApprovalConfirmation()
    review = terms.model_dump(mode="json")
    proof = flow.service.issue(terms.session_id, terms.request_id, "offline-human-posting-consent")
    args = {"session_id": terms.session_id, "request_id": terms.request_id, "review": review, "approval_proof": proof}
    with marker.using("heinzel", "deliver-channel-item", args):
        return flow.service.deliver(terms.session_id, terms.request_id, review, proof, marker)


def _reply(flow, terms, **updates):
    request = terms.approval
    data = {
        "request_id": request.request_id,
        "arguments_sha256": request.arguments_sha256,
        "decision": "approve",
        "actor": "owner-actor",
        "at": flow.now,
        "reason": "untrusted words never grant scope",
    }
    flow.channel.observed = [ChannelApprovalReply.model_validate(data | updates)]


def test_proposal_and_unconfirmed_post_never_contact_transport_or_write(flow):
    terms = _propose(flow)
    assert _propose(flow) == terms
    assert flow.channel.posted == []
    with pytest.raises(HeinzelError, match="trusted human"):
        flow.service.deliver(
            terms.session_id, terms.request_id, terms.model_dump(mode="json"), "forged", ApprovalConfirmation()
        )
    assert flow.channel.posted == []
    assert flow.provider.writes == 0


def test_posting_consent_only_posts_and_genuine_owner_approval_writes_once_under_daemon(flow):
    terms = _propose(flow)
    assert _post(flow, terms) == "delivered"
    assert flow.provider.writes == 0
    assert flow.channel.posted == [terms.approval]
    _reply(flow, terms)
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 1
        assert flow.current.context().authentication == "daemon"
        assert flow.service.apply_channel_replies() == 0
    assert flow.provider.writes == 1
    assert flow.channel.settlements == ["approved"]
    assert flow.host.status(flow.draft.session, flow.draft.request_id) == "acknowledged"


@pytest.mark.parametrize(
    "updates",
    [
        {"actor": "outsider"},
        {"request_id": "different"},
        {"arguments_sha256": "0" * 64},
        {"at": datetime(2029, 1, 1, tzinfo=UTC)},
        {"at": datetime(2031, 1, 1, tzinfo=UTC)},
    ],
)
def test_wrong_actor_digest_request_or_reply_time_never_issues_write_approval(flow, updates):
    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms, **updates)
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 0
    assert flow.provider.writes == 0
    assert flow.host.status(flow.draft.session, flow.draft.request_id) == "draft"
    assert flow.service._events(terms.session_id, "channel.rejected", terms.request_id)


@pytest.mark.parametrize("change", ["actor", "source", "target", "revision", "account", "scope"])
def test_changed_channel_or_captured_write_identity_fails_closed(flow, change):
    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms)
    if change == "target":
        flow.channel.target = ChannelTarget(account="other", destination="other", scope="other")
    elif change in {"actor", "source"}:
        setattr(flow.channel, change, "b" * 64)
    else:
        setattr(flow.provider, change, "changed")
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 0
    assert flow.provider.writes == 0


def test_unknown_post_after_transport_exception_never_replays_or_consumes_reply(flow):
    terms = _propose(flow)
    flow.channel.lose_ack = True
    assert _post(flow, terms) == "unknown"
    assert flow.service.status(terms.session_id, terms.request_id) == "unknown"
    with pytest.raises(HeinzelError, match="already attempted"):
        _post(flow, terms)
    _reply(flow, terms)
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 0
    assert len(flow.channel.posted) == 1
    assert flow.provider.writes == 0


def test_unknown_provider_effect_and_settlement_failure_never_replay_the_write(flow):
    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms)
    flow.provider.lose_ack = True
    with flow.current.local("daemon"):
        flow.service.apply_channel_replies()
        assert flow.service.apply_channel_replies() == 0
    assert flow.provider.writes == 1
    assert flow.host.status(flow.draft.session, flow.draft.request_id) == "uncertain"


def test_decline_records_no_write_and_disposes_draft(flow):
    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms, decision="decline")
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 1
    assert flow.provider.writes == 0
    assert flow.host.status(flow.draft.session, flow.draft.request_id) == "discarded"
    assert flow.channel.settlements == ["declined"]


def test_expiry_and_model_context_never_issue_approval(flow):
    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms)
    with flow.current.local("mcp-stdio"), pytest.raises(HeinzelError, match="authenticated owner"):
        flow.service.apply_channel_replies()
    flow.crud.clock = lambda: flow.now + timedelta(days=1)
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 0
    assert flow.provider.writes == 0


def test_notice_requires_its_own_exact_posting_consent_and_has_host_selected_helper_and_key(flow):
    notice = ChannelNotice(
        key="model-chosen", helper="model-chosen", title="Review ready", body="Draft", route="/heinzel"
    )
    terms = flow.service.propose(flow.draft.session, "notice-one", "fixture", notice=notice)
    assert terms.notice.key != notice.key
    assert terms.notice.helper != "model-chosen"
    assert _post(flow, terms) == "delivered"
    assert flow.channel.posted == [terms.notice]
    assert flow.provider.writes == 0


def test_lost_settlement_retries_only_closing_the_same_message(flow):
    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms)
    flow.channel.settle_error = True
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 0
    assert flow.provider.writes == 1
    flow.channel.settle_error = False
    with flow.current.local("daemon"):
        assert flow.service.apply_channel_replies() == 0
        assert flow.service.apply_channel_replies() == 0
    assert flow.channel.settlements == ["approved", "approved"]
    assert len(flow.channel.posted) == flow.provider.writes == 1


def test_interrupted_claim_reopens_as_uncertain_without_issuing_a_write(flow, monkeypatch):
    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms)
    close = flow.service._close

    def crash(*_args):
        raise KeyboardInterrupt

    def no_issue(*_args):
        msg = "simulated failure before issue"
        raise HeinzelError(msg)

    monkeypatch.setattr(flow.service, "_close", crash)
    monkeypatch.setattr(CapturedWriteHost, "issue", no_issue)
    with flow.current.local("daemon"), pytest.raises(KeyboardInterrupt):
        flow.service.apply_channel_replies()
    monkeypatch.setattr(flow.service, "_close", close)
    from heinzel import CurrentUser
    from heinzel.modules.heinzel._session_crud import SessionCrud
    from heinzel.modules.heinzel._session_models import SessionCosts
    from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

    reopened_owner = CurrentUser(flow.current.directory)
    reopened_crud = SessionCrud(
        PersonalSessionRepository(reopened_owner), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
    )
    reopened_crud.clock = lambda: flow.now
    reopened = ChannelDelivery(reopened_crud, reopened_owner, flow.service.kernel)
    with reopened_owner.local("daemon"):
        assert reopened.apply_channel_replies() == 0
    assert flow.provider.writes == 0
    closed = flow.service._events(terms.session_id, "channel.closed", terms.request_id)
    assert closed[0].data["outcome"] == "uncertain"
    assert flow.channel.settlements == ["superseded"]


def test_a_second_consumer_does_not_recover_an_active_claim(flow):
    import threading

    terms = _propose(flow)
    _post(flow, terms)
    _reply(flow, terms)
    flow.provider.block = True
    counts = []
    errors = []
    finished = threading.Event()

    def consume():
        try:
            with flow.current.local("daemon"):
                counts.append(flow.service.apply_channel_replies())
        except BaseException as error:  # noqa: BLE001 - propagate the worker failure on the test thread
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=consume)

    def while_claim_active():
        # Start the competitor at the real write boundary, after durable authorization rather than on a startup timer.
        assert len(flow.service._events(terms.session_id, "channel.claimed", terms.request_id)) == 1
        events = flow.crud.repository.control_read(terms.session_id).events
        assert len([event for event in events if event.kind == "write.attempted"]) == 1
        assert flow.service._events(terms.session_id, "channel.closed", terms.request_id) == []
        thread.start()
        try:
            assert finished.wait(1)
            assert counts == [0]
            assert flow.provider.writes == 0
            assert flow.channel.settlements == []
        finally:
            flow.provider.release.set()
            thread.join(1)

    flow.provider.before_write = while_claim_active
    with flow.current.local("daemon"):
        count = flow.service.apply_channel_replies()
    assert not thread.is_alive()
    if errors:
        raise errors[0]
    assert count == 1
    assert counts == [0]
    assert flow.provider.writes == 1
    assert flow.channel.settlements == ["approved"]


def test_the_full_review_includes_target_and_revision_and_is_never_truncated(flow):
    import json

    terms = _propose(flow)
    reviewed = json.loads(terms.approval.preview)
    assert reviewed["account"] == flow.draft.account
    assert reviewed["reference"] == flow.draft.reference
    assert reviewed["base_revision"] == flow.draft.base_revision
    assert reviewed["write_scope"] == flow.draft.write_scope
    assert reviewed["content"] == flow.draft.content.get_secret_value()


def test_request_identity_never_reuses_consent_for_a_changed_notice(flow):
    notice = ChannelNotice(key="one", helper="fixture", title="Review", body="First", route="/heinzel")
    flow.service.propose(flow.draft.session, "notice-one", "fixture", notice=notice)
    with pytest.raises(HeinzelError, match="different terms"):
        flow.service.propose(
            flow.draft.session, "notice-one", "fixture", notice=notice.model_copy(update={"body": "Other"})
        )
    assert flow.channel.posted == []


def test_legacy_transport_never_falls_back_to_unguarded_posting():
    from heinzel import ApprovalChannel

    class Legacy(ApprovalChannel):
        name = "legacy"

        def owner_actor(self):
            return "owner"

        def post(self, request):
            pytest.fail("unguarded post must not be called")

        def replies(self, references):
            return []

        def settle(self, reference, outcome):
            pass

    legacy = Legacy()
    assert legacy.delivery_target() is None
    request = _channel_delivery.ChannelApprovalRequest(
        request_id="one",
        helper="Fixture",
        action="Insert",
        preview="Exact",
        arguments_sha256="a" * 64,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    target = ChannelTarget(account="account", destination="destination", scope="scope")
    assert legacy.post_reviewed(request, target).status == "refused"


def test_oversized_full_review_is_refused_without_sending_a_truncated_preview(flow):
    from pydantic import SecretStr

    draft = flow.draft.model_copy(update={"request_id": "long-one", "content": SecretStr("x" * 4001)})
    flow.host.draft(draft)
    with pytest.raises(HeinzelError, match="full draft"):
        flow.service.propose(draft.session, "long-post", "fixture", write_request=draft.request_id)
    assert flow.channel.posted == []


def test_disabled_consumer_performs_no_owner_or_transport_work(flow):
    flow.service.kernel.settings.features = frozenset()
    with flow.current.using(None):
        assert flow.service.apply_channel_replies() == 0
    assert flow.channel.posted == []
