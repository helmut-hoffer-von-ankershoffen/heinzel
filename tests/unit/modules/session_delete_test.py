"""Deleting a retired Heinzel for good: what goes, what stays, refusals and resume (ADR 0148)."""

import pathlib
from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError, SessionStore, Settings
from heinzel.kernel import PersonalChatRemoval
from heinzel.modules.heinzel._cross_history import CrossHistoryRequest, search_owner_history
from heinzel.modules.heinzel._heinzel_delete import HeinzelDeletion
from heinzel.modules.heinzel._history_fts import _CACHES, indexed_search  # pyright: ignore[reportPrivateUsage]
from heinzel.modules.heinzel._history_search import HistorySearchRequest
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import SESSION_NAMESPACES, PersonalSessionRepository
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner.chat._models import ChatContext, ChatMessage, ChatSettings
from heinzel.modules.system.runner.chat._store import ChatStore

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 13, tzinfo=UTC)


class _PowerCutError(OSError):
    def __init__(self) -> None:
        super().__init__("power cut")


class _Chats(PersonalChatRemoval):
    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove_personal(self, session_id: str) -> int:
        self.removed.append(session_id)
        return 1


@pytest.fixture
def current(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        yield current


@pytest.fixture
def crud(current):
    return SessionCrud(
        PersonalSessionRepository(current, clock=lambda: AT),
        lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2),
        clock=lambda: AT,
    )


@pytest.fixture
def chats():
    return _Chats()


@pytest.fixture
def deletion(crud, chats):
    return HeinzelDeletion(crud, chats)


def _owner_root(crud: SessionCrud):
    repository = crud.repository
    assert isinstance(repository, PersonalSessionRepository)
    return repository.current.directory.parent / "heinzel-sessions" / repository.principal_id


def _retired(crud: SessionCrud, request: str, name: str) -> str:
    created = crud.new_chat(request, name, initial_message=f"Hello {name}")
    crud.accept(created.session_id, MessageRequest(request_id=f"m-{request}", text="Remember the blue door"))
    crud.retire(created.session_id, f"retire-{request}")
    with crud.repository.channel_writer(created.session_id):
        pass
    return created.session_id


def test_delete_removes_every_per_heinzel_store_and_keeps_only_a_tombstone(crud, deletion, chats):
    kept = crud.new_chat("create-keep", "Keep").session_id
    gone = _retired(crud, "create-ada", "Ada")
    root = _owner_root(crud)
    assert all((root / namespace / gone).is_dir() for namespace in SESSION_NAMESPACES)

    result = deletion.delete(gone, "Ada")

    assert result.session_id == gone
    assert result.state == "deleted"
    assert result.deleted_at == AT
    for namespace in SESSION_NAMESPACES:
        assert not (root / namespace / gone).exists()
        assert not any(path.name.endswith(gone) for path in (root / namespace).iterdir())
        assert all(head.session != gone for head in SessionStore(root / namespace).heads())
    assert chats.removed == [gone]
    assert [item.session_id for item in crud.listing().sessions] == [kept]
    with pytest.raises(HeinzelError, match="No such personal Heinzel"):
        crud.snapshot(gone)
    catalog = SessionStore(root / "catalog").read("creations").events
    mine = [event for event in catalog if event.data.get("session_id") == gone]
    assert [event.kind for event in mine] == ["creation.reserved", "creation.deleting", "creation.deleted"]
    assert mine[-1].data == {"session_id": gone}
    assert b"blue door" not in b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
    repository = crud.repository
    repository.verify_reserved_session_stores(repository.reserved_session_ids())


def test_delete_again_returns_the_same_tombstone(crud, deletion):
    gone = _retired(crud, "create-bo", "Bo")
    first = deletion.delete(gone, "Bo")
    assert deletion.delete(gone, "anything") == first


@pytest.mark.parametrize("name", ["cy", "Cy ", "C"])
def test_delete_needs_the_exact_name(crud, deletion, name):
    session = _retired(crud, "create-cy", "Cy")
    with pytest.raises(HeinzelError, match="does not match"):
        deletion.delete(session, name)
    assert crud.repository.deletion_state(session) == "live"
    assert crud.snapshot(session).state == "retired"


def test_delete_refuses_a_heinzel_that_is_not_retired(crud, deletion):
    session = crud.new_chat("create-di", "Di").session_id
    with pytest.raises(HeinzelError, match="retire it first"):
        deletion.delete(session, "Di")
    crud.retire(session, "retire-1")
    crud.retire(session, "restore-1", retired=False)
    with pytest.raises(HeinzelError, match="retire it first"):
        deletion.delete(session, "Di")
    assert crud.snapshot(session).name == "Di"


def test_delete_refuses_while_a_turn_holds_the_heinzel(crud, deletion):
    session = _retired(crud, "create-ed", "Ed")
    with crud.repository.main_writer(session), pytest.raises(HeinzelError, match="another writer"):
        deletion.delete(session, "Ed")
    assert crud.repository.deletion_state(session) == "live"
    assert deletion.delete(session, "Ed").state == "deleted"


def test_delete_refuses_an_unknown_heinzel(deletion):
    with pytest.raises(HeinzelError, match="No such personal Heinzel"):
        deletion.delete("hz-01JC0000000000000000000000", "Nobody")


@pytest.mark.parametrize("fail_at", range(len(SESSION_NAMESPACES) + 1))
def test_a_crash_in_the_middle_is_finished_by_the_next_list(crud, deletion, monkeypatch, fail_at):
    session = _retired(crud, "create-fy", "Fy")
    repository = crud.repository
    original = repository.remove_session_store
    calls: list[str] = []

    def crashing(namespace: str, session_id: str) -> None:
        if len(calls) == fail_at:
            raise _PowerCutError
        calls.append(namespace)
        original(namespace, session_id)

    monkeypatch.setattr(repository, "remove_session_store", crashing)
    if fail_at == len(SESSION_NAMESPACES):
        monkeypatch.setattr(repository, "mark_deleted", lambda _session: (_ for _ in ()).throw(OSError("power cut")))
    with pytest.raises(OSError, match="power cut"):
        deletion.delete(session, "Fy")
    assert repository.deletion_state(session) == "deleting"
    assert repository.pending_deletions() == (session,)
    assert crud.listing().sessions == []
    repository.verify_reserved_session_stores(repository.reserved_session_ids())

    monkeypatch.undo()
    assert deletion.listing().sessions == []

    assert repository.deletion_state(session) == "deleted"
    assert repository.pending_deletions() == ()
    root = _owner_root(crud)
    assert not any((root / namespace / session).exists() for namespace in SESSION_NAMESPACES)


def test_a_failing_resume_is_logged_and_retried(crud, deletion, monkeypatch, caplog):
    session = _retired(crud, "create-gu", "Gu")
    repository = crud.repository
    monkeypatch.setattr(repository, "mark_deleted", lambda _session: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        deletion.delete(session, "Gu")
    deletion.finish_pending()
    assert "finishing the deletion" in caplog.text
    assert "Gu" not in caplog.text.replace(session, "")  # the session id at most, never its name
    monkeypatch.undo()
    deletion.finish_pending()
    assert repository.deletion_state(session) == "deleted"


def test_finish_refuses_a_heinzel_that_is_not_being_deleted(crud, deletion):
    session = crud.new_chat("create-hy", "Hy").session_id
    with pytest.raises(HeinzelError, match="not being deleted"):
        deletion.finish(session)
    with pytest.raises(HeinzelError, match="marked for deletion"):
        crud.repository.remove_session_store("main", session)
    with pytest.raises(HeinzelError, match="marked for deletion"):
        crud.repository.mark_deleted(session)
    crud.retire(session, "retire-1")
    with pytest.raises(HeinzelError, match="marked for deletion"):
        crud.repository.remove_session_store("catalog", session)


def test_a_writer_that_waited_past_the_deletion_removes_what_it_recreated(crud, deletion, monkeypatch):
    session = _retired(crud, "create-io", "Io")
    deletion.delete(session, "Io")
    repository = crud.repository
    assert isinstance(repository, PersonalSessionRepository)
    # The ownership check passed just before the deletion; the writer then won the lock afterwards.
    monkeypatch.setattr(repository, "_owned", lambda _session: None)
    with pytest.raises(HeinzelError, match="No such personal Heinzel"), repository.control_writer(session):
        pytest.fail("a deleted Heinzel must not be written")
    assert not (_owner_root(crud) / "control" / session).exists()


def test_replaying_the_creation_of_a_deleted_heinzel_is_refused(crud, deletion):
    session = _retired(crud, "create-jo", "Jo")
    deletion.delete(session, "Jo")
    with pytest.raises(HeinzelError, match="was deleted"):
        crud.new_chat("create-jo", "Jo", initial_message="Hello Jo")


def test_owner_wide_ledgers_keep_their_records(crud, deletion):
    kept = crud.new_chat("create-ka", "Ka").session_id
    session = _retired(crud, "create-ku", "Ku")
    with crud.repository.budget_writer(session) as writer:
        writer.append("model.ledger-initialized", key="initialized", data={"owner": "me"})
    deletion.delete(session, "Ku")
    assert [event.kind for event in crud.repository.budget_read(kept).events] == ["model.ledger-initialized"]


def test_search_skips_a_deleted_heinzel_and_forgets_its_index(crud, deletion):
    kept = crud.new_chat("create-la", "La", initial_message="Remember the blue door").session_id
    session = _retired(crud, "create-lu", "Lu")
    indexed_search(crud, HistorySearchRequest(session_id=session, query="blue door"))
    assert any(key[2] == session for key in _CACHES)

    deletion.delete(session, "Lu")

    assert not any(key[2] == session for key in _CACHES)
    seen: list[str | None] = []
    cursor = None
    for _ in range(20):
        page = search_owner_history(crud, CrossHistoryRequest(query="blue door", cursor=cursor))
        seen.append(page.session_id)
        assert page.status != "blocked"
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert kept in seen
    assert session not in seen


def test_the_runner_removes_the_owner_chats_bound_to_the_heinzel(tmp_path, current, crud):
    session = crud.new_chat("create-mo", "Mo").session_id
    other = crud.new_chat("create-mu", "Mu").session_id
    store = ChatStore(Settings(state_dir=tmp_path / "runner"), ChatSettings(), RunnerSettings(), current)
    bound = store.create(ChatContext(kind="text", ref=session), personal_session_id=session)
    legacy = store.create(ChatContext(kind="text", ref=session))
    kept = store.create(ChatContext(kind="text", ref=other), personal_session_id=other)
    plain = store.create(None)

    assert store.remove_personal(session) == 2

    remaining = {path.name for path in store.root.iterdir() if path.is_dir()}
    assert remaining == {kept.id, plain.id}
    assert bound.id not in remaining
    assert legacy.id not in remaining
    assert store.remove_personal(session) == 0


def test_the_runner_skips_an_unreadable_chat(tmp_path, current):
    store = ChatStore(Settings(state_dir=tmp_path / "runner"), ChatSettings(), RunnerSettings(), current)
    broken = store.root / "0123456789abcdef"
    broken.mkdir(parents=True)
    (broken / "session.json").write_text("not json", encoding="utf-8")
    pathlib.Path(broken).chmod(0o700)
    assert store.remove_personal("hz-01JC0000000000000000000000") == 0
    assert broken.is_dir()


def test_the_runner_refuses_while_a_bound_chat_is_busy(tmp_path, current, crud):
    session = crud.new_chat("create-no", "No").session_id
    store = ChatStore(Settings(state_dir=tmp_path / "runner"), ChatSettings(), RunnerSettings(), current)
    bound = store.create(ChatContext(kind="text", ref=session), personal_session_id=session)
    with store.locked(bound.id), pytest.raises(HeinzelError, match="busy"):
        ChatStore(Settings(state_dir=tmp_path / "runner"), ChatSettings(), RunnerSettings(), current).remove_personal(
            session
        )
    assert store.directory(bound.id).is_dir()


def test_delete_is_never_an_mcp_tool_but_the_cli_and_web_have_it(make_kernel):
    from heinzel.heads import mcp
    from heinzel.heads._exposure import exposed

    kernel = make_kernel(builtins=True)
    on_mcp = {item.name for item in exposed(kernel, mcp.HEAD)}
    assert "heinzel_retire" in on_mcp
    assert "heinzel_delete" not in on_mcp
    for head in ("cli", "web", "api-private"):
        assert "heinzel_delete" in {item.name for item in exposed(kernel, head)}


class _Posted:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.forgotten: list[tuple[str, str]] = []

    def check(self, channel: str) -> None:
        if not self.available:
            msg = f"The {channel} channel is unavailable"
            raise HeinzelError(msg)

    def forget(self, channel: str, reference: str) -> None:
        self.forgotten.append((channel, reference))


def _post_approval(  # noqa: PLR0913 - one seeded channel request
    crud: SessionCrud, session: str, request: str, reference: str, *, kind: str = "approval", preview: str = "secret"
) -> None:
    from heinzel.modules.heinzel._session_bodies import immutable_body

    with crud.repository.control_writer(session) as writer:
        body = immutable_body(writer, "channel-proposal", {"kind": kind, "preview": preview}, control=True)
        writer.append("channel.proposed", key=request, data=body.model_dump(mode="json"))
        receipt = {"channel": "chatops", "status": "queued", "reference": reference}
        writer.append("channel.receipt", key=request, data={"attempt_seq": 1, "receipt": receipt})


def test_delete_closes_and_forgets_every_posted_approval(crud, chats):
    session = _retired(crud, "create-pa", "Pa")
    _post_approval(crud, session, "ask-1", "ref-1")
    _post_approval(crud, session, "note-1", "ref-2", kind="notice")
    posted = _Posted()

    HeinzelDeletion(crud, chats, None, posted).delete(session, "Pa")  # type: ignore[arg-type]

    assert posted.forgotten == [("chatops", "ref-1")]
    catalog = SessionStore(_owner_root(crud) / "catalog").read("creations").events
    intent = next(event for event in catalog if event.kind == "creation.deleting")
    assert intent.data == {"session_id": session, "posts": [{"channel": "chatops", "reference": "ref-1"}]}


def test_delete_refuses_before_anything_when_a_channel_holding_posts_is_unavailable(crud, chats):
    session = _retired(crud, "create-pe", "Pe")
    _post_approval(crud, session, "ask-1", "ref-1")
    with pytest.raises(HeinzelError, match="unavailable"):
        HeinzelDeletion(crud, chats, None, _Posted(available=False)).delete(session, "Pe")  # type: ignore[arg-type]
    with pytest.raises(HeinzelError, match="cannot be closed"):
        HeinzelDeletion(crud, chats).delete(session, "Pe")
    assert crud.repository.deletion_state(session) == "live"


def test_delete_refuses_while_a_document_change_has_no_outcome(crud, deletion):
    session = _retired(crud, "create-pi", "Pi")
    target = "a" * 64
    with crud.repository.draft_target_writer(session, target) as writer:
        writer.append("draft.target-intent", key="append-1", data={"session": session})
    with pytest.raises(HeinzelError, match="no recorded outcome"):
        deletion.delete(session, "Pi")
    with crud.repository.draft_target_writer(session, target) as writer:
        writer.append("draft.target-settled", key="append-1", data={})
    assert deletion.delete(session, "Pi").state == "deleted"


def test_the_typed_name_is_compared_after_unicode_normalisation(crud, deletion):
    session = _retired(crud, "create-zoe", "Zoë")
    assert deletion.delete(session, "Zoë").state == "deleted"


def test_shared_ledgers_refuse_a_writer_that_waited_past_the_deletion(crud, deletion, monkeypatch):
    session = _retired(crud, "create-qa", "Qa")
    deletion.delete(session, "Qa")
    repository = crud.repository
    monkeypatch.setattr(repository, "_owned", lambda _session: None)
    for opened in (
        lambda: repository.budget_writer(session),
        lambda: repository.subscription_writer(session),
        lambda: repository.draft_target_writer(session, "b" * 64),
    ):
        with pytest.raises(HeinzelError, match="No such personal Heinzel"), opened():
            pytest.fail("no ledger record may land after the deletion")


def test_remove_drops_quarantined_index_copies(crud, deletion):
    session = _retired(crud, "create-ra", "Ra")
    control = _owner_root(crud) / "control"
    stale = control / ".index-0123456789abcdef01234567.sqlite"
    stale.write_bytes(b"old copy")
    deletion.delete(session, "Ra")
    assert not stale.exists()


def test_notify_forgets_the_notices_of_one_heinzel(settings):
    from heinzel import NoticeKind
    from heinzel.modules.system.notify import NotifyService, NotifySettings

    notify = NotifyService(settings, NotifySettings(), {"working_hours": "09-18", "timezone": "UTC"}, clock=lambda: AT)
    gone, kept = "hz-01JC0000000000000000000001", "hz-01JC0000000000000000000002"
    for index, (session, suffix) in enumerate([(gone, ""), (gone, "&tab=1"), (kept, "")]):
        notify.emit_once(
            NoticeKind.HEINZEL,
            f"Title {index}",
            "Body",
            route=f"/heinzel?chat={session}{suffix}",
            command="hz",
            key=f"k{index}",
        )
    assert notify.forget_heinzel(gone) == 2
    assert [notice.route for notice in notify.recent()] == [f"/heinzel?chat={kept}"]
    assert notify.forget_heinzel(gone) == 0


def test_canary_no_byte_of_a_deleted_heinzel_remains_anywhere(tmp_path, settings, current, crud):
    import json

    from heinzel import NoticeKind
    from heinzel.modules.system.notify import NotifyService, NotifySettings

    ascii_canary = "CANARY-7f3a9c-Quokka"
    wide_canary = "Kan\u00e4rchen-\u03a97-\u00df"  # also searched JSON-escaped, as an ASCII-only writer stores it
    forms = [ascii_canary.encode(), wide_canary.encode(), json.dumps(wide_canary)[1:-1].encode()]
    name = f"{wide_canary} {ascii_canary}"
    text = f"{ascii_canary} {wide_canary}"
    kept = crud.new_chat("create-keep", "Keeper", initial_message="Nothing to see").session_id
    session = crud.new_chat("create-canary", name, initial_message=f"{text} in the first message").session_id
    crud.accept(session, MessageRequest(request_id="m-canary", text=f"{text} in a later message"))
    _post_approval(crud, session, "ask-1", "ref-1", preview=f"{text} in a channel preview")
    with crud.repository.main_writer(session) as writer:  # what a turn writes: a body and an inline checkpoint
        writer.append_body("turn-1", {"text": f"{text} in a turn body"})
        writer.append("turn.note", turn="turn-1", data={"note": f"{text} inline"})
    with crud.repository.control_writer(session) as writer:  # a control line that carries its text inline
        writer.append("note.inline", key="w-1", data={"text": f"{text} inline in control"})
    store = ChatStore(Settings(state_dir=tmp_path / "runner"), ChatSettings(), RunnerSettings(), current)
    chat = store.create(ChatContext(kind="text", ref=session), personal_session_id=session)
    with store.locked(chat.id) as held:
        held.messages.append(ChatMessage(role="user", text=f"{text} in the runner chat", at=AT))
        store.save(held)
    notify = NotifyService(settings, NotifySettings(), {"working_hours": "09-18", "timezone": "UTC"}, clock=lambda: AT)
    notify.emit_once(
        NoticeKind.HEINZEL, f"{text} notice", f"{text} body", route=f"/heinzel?chat={session}", command="hz", key="n1"
    )
    archive = notify.store.directory.parent / "notify-recovery" / "20261101T000000000000Z-abcd"
    archive.mkdir(parents=True)
    (archive / "notices.json").write_text(json.dumps([{"route": f"/heinzel?chat={session}", "title": text}]))
    crud.listing()  # indexes every store
    indexed_search(crud, HistorySearchRequest(session_id=session, query=ascii_canary))
    crud.retire(session, "retire-canary")
    roots = [tmp_path, settings.state_dir]

    def leaks() -> dict[str, set[bytes]]:
        found: dict[str, set[bytes]] = {}
        for root in roots:
            for path in root.rglob("*"):  # dotfiles, -journal, -wal and -shm files included
                if path.is_file() and not path.is_symlink():
                    content = path.read_bytes()
                    if hits := {form for form in forms if form in content}:
                        found[str(path.relative_to(root))] = hits
        return found

    before = leaks()
    # Positive control: the scan really sees the canary in every kind of store it is meant to cover.
    covered = {
        "main spine": "/session.jsonl",
        "main turn bodies": "/turns/",
        "main index": "main/index.sqlite",
        "control index": "control/index.sqlite",
        "control bodies": "control/hz-",
        "notices": "notify/notices.json",
        "recovery archive": "notify-recovery/",
        "runner chat": "runner/chat/",
    }
    for what, part in covered.items():
        assert any(part in path for path in before), f"the canary was not planted in the {what}"
    assert any(wide_canary.encode() in hits for hits in before.values())

    HeinzelDeletion(crud, store, notify, _Posted()).delete(session, name)  # type: ignore[arg-type]

    assert leaks() == {}
    assert [item.session_id for item in crud.listing().sessions] == [kept]


class _FlakyPosted(_Posted):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def forget(self, channel: str, reference: str) -> None:
        if self.fail:
            msg = "plugin busy"
            raise HeinzelError(msg)
        super().forget(channel, reference)


def test_a_plugin_that_cannot_forget_yet_never_holds_back_hz_removal(crud, chats):
    session = _retired(crud, "create-ve", "Ve")
    _post_approval(crud, session, "ask-1", "ref-1")
    posted = _FlakyPosted()
    deletion = HeinzelDeletion(crud, chats, None, posted)  # type: ignore[arg-type]

    waiting = deletion.delete(session, "Ve")

    assert (waiting.state, waiting.deleted_at, waiting.waiting_for) == ("forgetting", None, ["chatops"])
    root = _owner_root(crud)
    assert not any((root / namespace / session).exists() for namespace in SESSION_NAMESPACES)
    assert crud.repository.deletion_posts(session) == [{"channel": "chatops", "reference": "ref-1"}]
    posted.fail = False
    assert deletion.listing().sessions == []
    assert crud.repository.deletion_state(session) == "deleted"
    assert crud.repository.deletion_posts(session) == []
    assert posted.forgotten == [("chatops", "ref-1")]
    assert HeinzelDeletion(crud, chats).finish(session).state == "deleted"


def test_without_a_channel_service_the_posts_wait(crud, chats):
    session = _retired(crud, "create-vi", "Vi")
    _post_approval(crud, session, "ask-1", "ref-1")
    crud.repository.mark_deleting(session, [{"channel": "chatops", "reference": "ref-1"}])
    assert HeinzelDeletion(crud, chats).finish(session).waiting_for == ["chatops"]


class _Service:
    def __init__(self, *, offline: bool) -> None:
        self.offline = offline
        self.calls: list[str] = []

    def settle(self, reference: str, outcome: str) -> None:
        self.calls.append(f"settle:{reference}:{outcome}")
        if self.offline:
            msg = "no network"
            raise OSError(msg)

    def forget(self, reference: str) -> None:
        self.calls.append(f"forget:{reference}")


@pytest.mark.parametrize("offline", [False, True])
def test_settling_is_best_effort_and_forgetting_is_local(monkeypatch, offline):
    from heinzel.modules.heinzel import _heinzel_delete

    service = _Service(offline=offline)

    class _Binding:
        def __init__(self, _kernel, name, kind) -> None:
            assert (name, kind) == ("chatops", "approval")

        def call(self, action):
            return action(service)

    monkeypatch.setattr(_heinzel_delete, "ChannelBinding", _Binding)
    posted = _heinzel_delete.PostedApprovals(None)  # type: ignore[arg-type]
    posted.check("chatops")
    posted.forget("chatops", "ref-1")
    assert service.calls == ["settle:ref-1:superseded", "forget:ref-1"]


def test_a_plugin_that_is_not_loaded_is_named_in_the_refusal(monkeypatch):
    from heinzel.modules.heinzel import _heinzel_delete

    def unavailable(*_args):
        msg = "Choose one available source-bound channel"
        raise HeinzelError(msg)

    monkeypatch.setattr(_heinzel_delete, "ChannelBinding", unavailable)
    with pytest.raises(HeinzelError, match=r"chatops plugin .* not loaded or is turned off"):
        _heinzel_delete.PostedApprovals(None).check("chatops")  # type: ignore[arg-type]


def test_the_cli_reads_the_name_only_from_an_interactive_terminal(monkeypatch):
    from heinzel.modules.heinzel import _heinzel_delete

    monkeypatch.setattr(_heinzel_delete.sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(HeinzelError, match="interactive terminal"):
        _heinzel_delete.typed_name("Name: ")
    monkeypatch.setattr(_heinzel_delete.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: f"typed after {prompt}")
    assert _heinzel_delete.typed_name("Name: ") == "typed after Name: "


def test_notify_removes_the_heinzel_from_recovery_archives_and_rebinds_the_receipt(settings):
    import hashlib
    import json

    from heinzel.modules.system.notify import NotifyService, NotifySettings

    notify = NotifyService(settings, NotifySettings(), {"working_hours": "09-18", "timezone": "UTC"}, clock=lambda: AT)
    gone, kept = "hz-01JC0000000000000000000001", "hz-01JC0000000000000000000002"
    archive = notify.store.directory.parent / "notify-recovery" / "20261101T000000000000Z-abcd"
    archive.mkdir(parents=True)
    notices = [{"route": f"/heinzel?chat={gone}", "title": "secret"}, {"route": f"/heinzel?chat={kept}", "title": "ok"}]
    (archive / "notices.json").write_text(json.dumps(notices))
    (archive / "receipt.json").write_text(
        json.dumps({"originals": [{"name": "notices.json", "sha256": "x", "size": 1}]})
    )
    (archive / "looked.json").write_text("{}")
    (archive.parent / "stray-file").write_text("not an archive")
    other = archive.parent / "20261102T000000000000Z-ef01"
    other.mkdir()
    (other / "notices.json").write_text(json.dumps({"notices": [{"route": f"/heinzel?chat={gone}&x=1"}]}))
    nothing = archive.parent / "20261103T000000000000Z-ef02"
    nothing.mkdir()
    (nothing / "notices.json").write_text(json.dumps({"other": 1}))

    assert notify.forget_heinzel(gone) == 2

    text = (archive / "notices.json").read_text()
    assert "secret" not in text
    receipt = json.loads((archive / "receipt.json").read_text())
    assert receipt["originals"][0]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert receipt["redactions"][0]["notices_removed"] == 1
    assert json.loads((other / "notices.json").read_text()) == {"notices": []}
    assert notify.forget_heinzel(gone) == 0
