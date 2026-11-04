"""Captured SDK doubles exercise the real standing-draft host; no live account or external message is used."""

import sys
from uuid import uuid4

import pytest

from heinzel import Config, CurrentUser, HeinzelError
from heinzel.kernel import boot
from heinzel.kernel._approval import ApprovalConfirmation
from heinzel.modules.heinzel._draft_workflow_journal import grant_payload
from heinzel.modules.heinzel._draft_workflow_models import DraftPublicationRequest
from heinzel.modules.heinzel._draft_workflow_publication import DraftWorkflowPublication
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from tests._source_distribution_fixture import installed_entry
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.integration

SOURCE = r"""
import hashlib
from heinzel import (
    Module, ReleaseFeedService, ReleaseFeedLease, ReleaseFeedIdentity, ReleaseFeedBaseline, ReleasePage,
    DraftAppendService, DraftAppendLease, DraftAppendTarget, DraftAppendReview, DraftAppendReceipt,
    DraftSectionSnapshot, OwnerNoticeChannel, OwnerNoticeTarget, ReviewedLinkChannel, ChannelTarget, ChannelReceipt,
)
_STATE = {"releases": [], "revision": 1, "document": "", "drafts": {}, "appends": 0,
          "notices": [], "shares": [], "account": "document-account", "source": "document-source",
          "feed_account": "feed-account", "destination": "chosen-channel", "notice_actor": "owner-one"}

class Feed(ReleaseFeedService):
    supported_operations = frozenset({"release-feed"})
    def open_lease(self, reference):
        class Lease(ReleaseFeedLease):
            def baseline(self):
                return ReleaseFeedBaseline(identity=ReleaseFeedIdentity(
                    account=_STATE["feed_account"], source="feed-source", reference=reference, read_scope="release-read"
                ), cursor=str(len(_STATE["releases"])))
            def poll(self, cursor):
                return ReleasePage(identity=self.baseline().identity, cursor=str(len(_STATE["releases"])),
                                   releases=tuple(_STATE["releases"][int(cursor):]))
            def close(self):
                if _STATE.get("feed_close_failure"): raise OSError("secret-sentinel-feed-close")
        return Lease()

class Append(DraftAppendService):
    supported_operations = frozenset({"append-marked-draft"})
    def target(self, reference, section):
        return DraftAppendTarget(account=_STATE["account"], source=_STATE["source"], reference=reference,
            resource_id="document-one", section=_STATE.get("section", section), revision=str(_STATE["revision"]),
            write_scope="conditional-draft-append", conditional_append=True)
    def open_lease(self, attempt):
        provider = self
        class Lease(DraftAppendLease):
            def review(self):
                return DraftAppendReview(draft_key=attempt.draft_key,
                    target=provider.target(attempt.target.reference, attempt.target.section),
                    text_sha256=hashlib.sha256(attempt.text.get_secret_value().encode()).hexdigest(), append_only=True)
            def append(self):
                assert self.review().target == attempt.target
                _STATE["appends"] += 1
                _STATE["document"] += attempt.text.get_secret_value()
                _STATE["drafts"][attempt.draft_key] = attempt.text.get_secret_value()
                _STATE["revision"] += 1
                if _STATE.get("append_lost_ack"): raise OSError("secret-sentinel-append")
                return DraftAppendReceipt(draft_key=attempt.draft_key, reference=attempt.target.reference,
                                          revision=str(_STATE["revision"]))
            def close(self):
                if _STATE.get("append_close_failure"): raise OSError("secret-sentinel-append-close")
        return Lease()
    def capture(self, target, draft_key):
        return DraftSectionSnapshot(draft_key=draft_key, target=self.target(target.reference, target.section),
                                    text=_STATE["drafts"][draft_key])

class PrivateNotice(OwnerNoticeChannel):
    name = "fixture-private"
    supported_operations = frozenset({"private-owner-notice"})
    def available(self): return True
    def owner_target(self):
        return OwnerNoticeTarget(account="notice-account", destination="owner-inbox", scope="private-notice",
                                 owner_actor=_STATE["notice_actor"], private_owner=True)
    def deliver_owner_reviewed(self, notice, target):
        assert target == self.owner_target()
        _STATE["notices"].append(notice)
        if _STATE.get("notice_lost_ack"): raise OSError("secret-sentinel-notice")
        return ChannelReceipt(channel=self.name, status="delivered", reference="notice-" + str(len(_STATE["notices"])))

class LinkShare(ReviewedLinkChannel):
    name = "fixture-publication"
    supported_operations = frozenset({"reviewed-document-link"})
    def available(self): return True
    def delivery_target(self):
        return ChannelTarget(account="share-account", destination=_STATE["destination"], scope="post-document-link")
    def deliver_link_reviewed(self, notice, target):
        assert target == self.delivery_target()
        _STATE["shares"].append(notice)
        if _STATE.get("share_lost_ack"): raise OSError("secret-sentinel-share")
        return ChannelReceipt(channel=self.name, status="delivered", reference="share-" + str(len(_STATE["shares"])))

class Documents:
    def read(self, ref): return None
class CodeHost:
    def merged_pull_requests(self, repository, window): return []
module = Module("workflowfixture", "Isolated standing draft SDK fixture")
module.service(Feed)
module.service(Append)
module.service(PrivateNotice)
module.service(LinkShare)
module.provides("documents", name="fixture", modes={"read"})(Documents)
module.provides("code-host", name="fixture", modes={"read"})(CodeHost)
"""


@pytest.fixture
def workflow(tmp_path, monkeypatch, settings):
    from heinzel.kernel import _discovery

    package = "fixture_workflow_" + uuid4().hex
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text(SOURCE)
    monkeypatch.setattr(
        _discovery, "entry_points", lambda **_kwargs: [installed_entry(root, "workflowfixture", package, declared=True)]
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    features = frozenset({"connected_frontdoor", "connected_channels"})
    config = Config(
        files=(),
        data={"capabilities": {"documents": "fixture", "code-host": "fixture"}, "hz": {"features": sorted(features)}},
    )
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    kernel = boot(settings.model_copy(update={"features": features}))
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    chat = kernel.resolve(UserChatService)
    executable = fake_claude(tmp_path)
    monkeypatch.setattr(chat, "_load", lambda: FixtureConfig(executable))
    crud = kernel.resolve(SessionCrud)
    provider = sys.modules[package]
    with current.local("cli"):
        session = crud.new_chat(
            "turing", "Turing", initial_message="Draft each new release's poem for my review."
        ).session_id
        host = DraftWorkflowPublication(crud, current, kernel, chat)
        request = DraftPublicationRequest(
            request_id="publication-one",
            name="Turing",
            prompt="Draft each new release's poem for my review.",
            feed_reference="https://fixture.invalid/changelog",
            document_reference="https://fixture.invalid/document",
            section="poems",
            publication_channel="fixture-publication",
            notice_channel="fixture-private",
        )
        yield host, session, request, provider._STATE
    finder = provider.__spec__.loader
    if finder in sys.meta_path:
        sys.meta_path.remove(finder)
    sys.modules.pop(package, None)


def test_standing_draft_proposal_is_inert_and_binds_only_observed_exact_targets(workflow):
    host, session, request, state = workflow
    terms = host.propose(session, request)
    assert terms.generation == 1
    assert terms.feed_baseline.cursor == "0"
    assert terms.document.reference == request.document_reference
    assert terms.notice_target.owner_actor == "owner-one"
    assert host.propose(session, request) == terms
    assert state["appends"] == 0
    assert state["notices"] == state["shares"] == []


def publish(workflow):
    host, session, request, _state = workflow
    terms = host.propose(session, request)
    proof = host.issue(session, request.request_id, "cli-publish:fixture-human")
    marker = ApprovalConfirmation()
    review = grant_payload(terms)
    args = {"session_id": session, "request_id": request.request_id, "review": review, "approval_proof": proof}
    with marker.using("heinzel", "publish-draft-workflow", args):
        host.publish(session, request.request_id, review, proof, marker)
    return terms


def test_exact_initial_human_publication_is_separate_from_prompt_and_never_reactivated_by_replay(workflow):
    host, session, request, state = workflow
    terms = host.propose(session, request)
    with pytest.raises(HeinzelError, match="No draft workflow"):
        host.published(session)
    with pytest.raises(HeinzelError, match="invalid"):
        host.publish(session, request.request_id, grant_payload(terms), "forged", ApprovalConfirmation())
    assert publish(workflow) == host.published(session)
    host.revoke(session, request.request_id)
    with pytest.raises(HeinzelError, match="revoked"):
        host.published(session)
    assert state["appends"] == 0
    assert state["notices"] == state["shares"] == []


def test_publication_denies_source_account_and_destination_drift_without_effects(workflow):
    host, session, request, state = workflow
    terms = host.propose(session, request)
    for field, changed in [
        ("account", "other-account"),
        ("source", "other-source"),
        ("feed_account", "other-feed"),
        ("destination", "other-channel"),
        ("notice_actor", "other-owner"),
    ]:
        previous = state[field]
        state[field] = changed
        with pytest.raises(HeinzelError, match="changed"):
            host.verify_ports(terms)
        state[field] = previous
    assert state["appends"] == 0
    assert state["notices"] == state["shares"] == []
