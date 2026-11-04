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
    import asyncio

    asyncio.run(host.chat.answer(session, "turing"))
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


def add_release(state, identity="release-2", text="A new real provider release"):
    from datetime import UTC, datetime

    from heinzel import ReleaseRecord

    state["releases"].append(
        ReleaseRecord(identity=identity, revision="tag-commit-2", published_at=datetime.now(UTC), text=text)
    )


def run_tick(host, session, request):
    import asyncio
    from datetime import UTC, datetime

    from heinzel.modules.heinzel._draft_workflow_runtime import tick

    with host.current_user.local("daemon"):
        asyncio.run(tick(host, session, request.request_id, datetime.now(UTC)))


def test_registered_cli_one_prompt_daemon_edit_and_trusted_share_with_restart(workflow, monkeypatch):
    from typer.testing import CliRunner

    from heinzel.heads.cli import _command, build
    from heinzel.kernel import CompiledJobs
    from heinzel.modules.heinzel._draft_workflow_journal import matching

    host, _unused_session, _unused_request, state = workflow
    monkeypatch.setattr(_command, "has_terminal", lambda: True)
    prompt = (
        "Create a Heinzel named Turing. Every five minutes watch https://fixture.invalid/CHANGELOG.md "
        "and append a release poem draft to https://fixture.invalid/document; notify me for review, "
        "then share its link only after I edit and approve it."
    )
    runner = CliRunner()
    result = runner.invoke(build(host.kernel), ["heinzel", "start-release-workflow", "cli-one", prompt], input="yes\n")
    assert result.exit_code == 0, result.output + repr(result.exception)
    current = host.current_user
    sessions = host.crud.listing()
    session = next(
        item.session_id for item in sessions.sessions if item.name == "Turing" and item.session_id != _unused_session
    )
    terms = host.published(session)
    assert terms.request.prompt == prompt
    snapshot = host.crud.snapshot(session)
    assert snapshot.messages[0].text == prompt
    assert not state["appends"]
    assert not state["shares"]
    add_release(state)
    with current.local("daemon"):
        jobs = host.kernel.resolve(CompiledJobs).jobs
        job = next(item for item in jobs if item.name == "draft-" + session.lower())
        assert job.schedule == "*/5 * * * *"
        assert job.retries == 0
        from datetime import UTC, datetime

        job.run_for_slot(datetime.now(UTC))
    assert state["appends"] == 1
    from heinzel import Notifications

    assert state["notices"] == []
    assert any(
        item.title == "A release draft is ready for review" for item in host.kernel.resolve(Notifications).recent()
    )
    assert state["shares"] == []
    key = next(iter(state["drafts"]))
    state["drafts"][key] += "."
    state["revision"] += 1
    edited = state["drafts"][key]
    result = runner.invoke(build(host.kernel), ["heinzel", "approve-release-draft", session, key], input="yes\n")
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert state["shares"][0].body == terms.document.reference
    assert state["drafts"][key] == edited
    acknowledgement = matching(host.journal.events(session), "draft.share-acknowledged", key)[0]
    recorded = host.crud.read_body(session, acknowledgement)
    assert recorded["review"]["snapshot"]["text"] == edited
    assert recorded["review"]["snapshot"]["target"]["revision"] == str(state["revision"])
    restarted = DraftWorkflowPublication(host.crud, current, host.kernel, host.chat)
    run_tick(restarted, session, terms.request)
    assert state["appends"] == len(state["shares"]) == 1
    replay = runner.invoke(build(host.kernel), ["heinzel", "approve-release-draft", session, key], input="yes\n")
    assert replay.exit_code == 1
    assert len(state["shares"]) == 1


@pytest.mark.parametrize("failure", ["append_lost_ack", "append_close_failure"])
def test_lost_append_acknowledgement_or_cleanup_fences_restarts_without_duplicate(workflow, failure):
    host, session, request, state = workflow
    publish(workflow)
    add_release(state)
    state[failure] = True
    with pytest.raises(HeinzelError, match="uncertain"):
        run_tick(host, session, request)
    assert state["appends"] == 1
    assert state["notices"] == state["shares"] == []
    state[failure] = False
    restarted = DraftWorkflowPublication(host.crud, host.current_user, host.kernel, host.chat)
    with pytest.raises(HeinzelError, match="uncertain"):
        run_tick(restarted, session, request)
    assert state["appends"] == 1


def share_confirmation(host, session, key, *, drift=False):
    from heinzel.kernel._approval import ApprovalIssuer, arguments_hash
    from heinzel.modules.heinzel._draft_workflow_share import DraftWorkflowShare, share_payload

    service = DraftWorkflowShare(host)
    review = share_payload(service.propose(session, key))
    request = arguments_hash(review)
    issuer = host.kernel.resolve(ApprovalIssuer)
    arguments = {"session_id": session, "request_id": request, "review": review}
    proof = issuer.issue("heinzel", "share-draft-link", arguments, "chat-confirm:fixture:human")
    arguments["approval_proof"] = proof
    marker = host.kernel.resolve(ApprovalConfirmation)
    if drift:
        return service, request, review, proof, marker, arguments
    with marker.using("heinzel", "share-draft-link", arguments):
        return service.execute(session, request, review, proof, marker)


def test_lost_share_acknowledgement_never_reposts_after_restart_or_fresh_human_proof(workflow):
    host, session, request, state = workflow
    publish(workflow)
    add_release(state)
    run_tick(host, session, request)
    key = next(iter(state["drafts"]))
    state["drafts"][key] += "."
    state["revision"] += 1
    state["share_lost_ack"] = True
    with pytest.raises(HeinzelError, match="uncertain"):
        share_confirmation(host, session, key)
    assert len(state["shares"]) == 1
    state["share_lost_ack"] = False
    restarted = DraftWorkflowPublication(host.crud, host.current_user, host.kernel, host.chat)
    run_tick(restarted, session, request)
    with pytest.raises(HeinzelError, match="already attempted"):
        share_confirmation(restarted, session, key)
    assert state["appends"] == len(state["shares"]) == 1


@pytest.mark.parametrize("drift", ["revision", "destination", "account", "notice_actor", "revoked"])
def test_share_refuses_exact_edited_revision_destination_account_or_grant_drift(workflow, drift):
    host, session, request, state = workflow
    publish(workflow)
    add_release(state)
    run_tick(host, session, request)
    key = next(iter(state["drafts"]))
    service, review_id, review, proof, marker, arguments = share_confirmation(host, session, key, drift=True)
    if drift == "revision":
        state["drafts"][key] += "."
        state["revision"] += 1
    elif drift == "revoked":
        host.revoke(session, request.request_id)
    else:
        state[drift] = "different"
    with marker.using("heinzel", "share-draft-link", arguments), pytest.raises(HeinzelError):
        service.execute(session, review_id, review, proof, marker)
    assert state["shares"] == []


def test_owner_declines_initial_publication_and_no_daemon_job_or_external_effect_is_created(workflow, monkeypatch):
    from typer.testing import CliRunner

    from heinzel.heads.cli import _command, build
    from heinzel.kernel import CompiledJobs

    host, _, _, state = workflow
    monkeypatch.setattr(_command, "has_terminal", lambda: True)
    prompt = (
        "Create a Heinzel named Turing for https://fixture.invalid/CHANGELOG.md and https://fixture.invalid/document"
    )
    result = CliRunner().invoke(
        build(host.kernel), ["heinzel", "start-release-workflow", "declined", prompt], input="no\n"
    )
    assert result.exit_code == 1
    with host.current_user.local("daemon"):
        assert not any(job.name.startswith("draft-") for job in host.kernel.resolve(CompiledJobs).jobs)
    assert not state["appends"]
    assert state["shares"] == []
