"""Reviewed writes go to the documents connector that holds the document, with two on at once (ADR 0161). FAKE ones."""

import sys
from uuid import uuid4

import pytest

from heinzel import Config, CurrentUser, HeinzelError
from heinzel.kernel._boot import boot
from heinzel.modules.heinzel._captured_write_binding import SelectedWriteService
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from heinzel.modules.heinzel._captured_write_tools import DocumentDraftTools
from heinzel.modules.heinzel._session_crud import SessionCrud
from tests._source_distribution_fixture import installed_entry
from tests.integration.captured_write_chat_test import _SOURCE, stage
from tests.integration.chat_test import AUTH, web_client

pytestmark = pytest.mark.integration

_TARGET = """    def target(self, reference):
        from heinzel.kernel import CapturedWriteTarget
        return CapturedWriteTarget(
            account="a", source="s", reference=reference, base_revision="one", write_scope="w"
        )

"""
DRIVE = "https://fixture.invalid/doc/one"
WIKI = "https://wiki.invalid/doc/one"


def _plugin(name: str, host: str, *, writes: bool = True) -> str:
    source = (
        _SOURCE.replace('"fakewrite"', f'"{name}"')
        .replace('hosts=["fixture.invalid"]', f'links=["{host}"]')
        .replace("    def open_lease(self, draft):", _TARGET + "    def open_lease(self, draft):")
    )
    return source if writes else source.replace("module.service(FakeWrites)", "# reads only")


def _install(tmp_path, monkeypatch, names: list[str], plugins: dict[str, tuple[str, bool]]):
    from heinzel.kernel import _discovery

    entries, packages = [], {}
    for name, (host, writes) in plugins.items():
        package = f"fixture_owner_write_{name}_" + uuid4().hex
        root = tmp_path / package
        root.mkdir()
        (root / "__init__.py").write_text(_plugin(name, host, writes=writes))
        entries.append(installed_entry(root, name, package, declared=True))
        packages[name] = package
    monkeypatch.setattr(_discovery, "entry_points", lambda **_kwargs: entries)
    monkeypatch.syspath_prepend(str(tmp_path))
    config = Config(files=(), data={"capabilities": {"documents": names}, "hz": {"features": ["captured_writes"]}})
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    return packages


def _cleanup(packages: dict[str, str]) -> None:
    for package in packages.values():
        plugin = sys.modules.pop(package, None)
        finder = plugin.__spec__.loader if plugin is not None else None
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)


@pytest.fixture
def two(tmp_path, monkeypatch, settings, request):
    """Two documents connectors on, each with its own reviewed writer: `drive` the default, `wiki` the second."""
    plugins = getattr(request, "param", {"drive": ("fixture.invalid", True), "wiki": ("wiki.invalid", True)})
    packages = _install(tmp_path, monkeypatch, ["drive", "wiki"], plugins)
    kernel = boot(settings.model_copy(update={"features": frozenset({"captured_writes"})}))
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    states = {name: sys.modules[package]._STATE for name, package in packages.items()}
    yield client, kernel, current, session, states
    _cleanup(packages)


def _proposal(session: str, reference: str) -> dict[str, object]:
    return {
        "session_id": session,
        "request_id": "draft-one",
        "account": "account-one",
        "source": "source-one",
        "reference": reference,
        "operation": "insert-draft",
        "section": "summary",
        "base_revision": "one",
        "write_scope": "fixture-write",
        "content": "private exact replacement",
    }


def _confirm(client, session):
    staged, action = stage(client, session)
    return client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": staged["id"], "action": action["id"]}
    )


@pytest.mark.parametrize(("reference", "owner"), [(WIKI, "wiki"), ("wiki:doc-7", "wiki"), (DRIVE, "drive")])
def test_a_write_goes_to_the_connector_that_holds_the_document(two, reference, owner):
    client, kernel, current, session, states = two
    drafted = client.post(
        "/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": _proposal(session, reference)}
    )
    assert drafted.status_code == 200, drafted.text
    with current.local("cli"):
        draft = CapturedWriteHost(kernel.resolve(SessionCrud), current, kernel.resolve(SelectedWriteService)).current(
            session, "draft-one"
        )
    expected = SelectedWriteService(kernel, owner)
    assert (draft.provider, draft.binding_id) == (expected.record.qualname, expected.binding_id)
    result = _confirm(client, session)
    assert result.status_code == 200, result.text
    other = next(name for name in states if name != owner)
    assert (states[owner]["writes"], states[other]["opens"], states[other]["writes"]) == (1, 0, 0)


def test_the_binding_holds_the_connector_and_the_bound_list(two):
    _client, kernel, _current, _session, _states = two
    default, wiki = kernel.resolve(SelectedWriteService), SelectedWriteService(kernel, "wiki")
    assert default.owning(WIKI).binding_id == wiki.binding_id != default.binding_id
    assert default.owning(DRIVE) is default
    assert default.owning("new:document:1") is default
    kernel.capability_settings = kernel.capability_settings.model_copy(update={"documents": ["drive", "wiki", "x"]})
    assert SelectedWriteService(kernel, "wiki").binding_id != wiki.binding_id


def test_an_ambiguous_reference_is_refused_and_drafts_nothing(two):
    client, kernel, _current, session, states = two
    with pytest.raises(HeinzelError, match="could be in drive or wiki"):
        kernel.resolve(SelectedWriteService).owning("doc-7")
    drafted = client.post(
        "/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": _proposal(session, "doc-7")}
    )
    assert drafted.status_code == 400, drafted.text
    assert all(state["opens"] == state["writes"] == 0 for state in states.values())


def test_a_change_of_owner_invalidates_the_approval(two, monkeypatch):
    client, kernel, _current, session, states = two
    drafted = client.post(
        "/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": _proposal(session, WIKI)}
    )
    assert drafted.status_code == 200, drafted.text
    staged, action = stage(client, session)
    route = kernel.capability_route
    # The document's link now belongs to the default connector: the approved wiki binding no longer matches it.
    monkeypatch.setattr(kernel, "capability_route", lambda c, m, a: "drive" if a.get("ref") == WIKI else route(c, m, a))
    response = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": staged["id"], "action": action["id"]}
    )
    assert response.status_code == 400, response.text
    assert all(state["writes"] == 0 for state in states.values())


def test_a_forged_draft_for_the_default_never_reaches_the_owner(two):
    client, kernel, current, session, states = two
    drafted = client.post(
        "/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": _proposal(session, WIKI)}
    )
    assert drafted.status_code == 200, drafted.text
    default = kernel.resolve(SelectedWriteService)
    with current.local("cli"):
        draft = CapturedWriteHost(kernel.resolve(SessionCrud), current, default).current(session, "draft-one")
    forged = draft.model_copy(update={"provider": default.record.qualname, "binding_id": default.binding_id})
    with pytest.raises(HeinzelError, match="changed"):
        default.open_lease(forged)
    assert all(state["opens"] == 0 for state in states.values())


@pytest.mark.parametrize(
    "two", [{"drive": ("fixture.invalid", True), "wiki": ("wiki.invalid", False)}], indirect=True, ids=["read-only"]
)
def test_a_connector_that_cannot_write_is_named_and_offered_no_draft(two):
    client, kernel, _current, session, states = two
    with pytest.raises(HeinzelError, match="The wiki connector holds this document and can't take reviewed writes"):
        kernel.resolve(SelectedWriteService).owning(WIKI)
    drafted = client.post(
        "/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": _proposal(session, WIKI)}
    )
    assert drafted.status_code == 400, drafted.text
    assert "wiki connector" in drafted.text
    assert states["drive"]["opens"] == 0


def test_a_chat_draft_records_the_owning_connector(two, monkeypatch):
    _client, kernel, current, session, _states = two
    recorded = []

    class Host:
        def __init__(self, _crud, _current, selected):
            self.selected = selected

        def draft_in_turn(self, draft, turn, writer):
            recorded.append((draft, self.selected))

    monkeypatch.setattr("heinzel.modules.heinzel._captured_write_tools.CapturedWriteHost", Host)

    class Crud:
        class repository:  # noqa: N801 - mirrors the attribute the tools read
            @staticmethod
            def is_main_writer(_session, writer):
                return writer is True

    default = kernel.resolve(SelectedWriteService)
    tools = DocumentDraftTools(
        Crud(), current, default, session=session, turn="t", writer=True, approved_reference=lambda: WIKI
    )
    with current.local("daemon"):
        tools.dispatch({"section": "end", "content": "x"}, 1)
        tools.create({"kind": "document", "title": "New", "content": "x"}, 2)
    (draft, selected), (created, creator) = recorded
    assert (selected.registration.adapter, draft.binding_id) == (
        "wiki",
        SelectedWriteService(kernel, "wiki").binding_id,
    )
    assert (creator, created.binding_id) == (default, default.binding_id)


@pytest.mark.parametrize(
    "two",
    [
        {"drive": ("fixture.invalid", True), "wiki": ("wiki.invalid", True)},
        {"drive": ("fixture.invalid", True), "wiki": ("wiki.invalid", False)},
    ],
    indirect=True,
    ids=["both-write", "wiki-reads-only"],
)
@pytest.mark.parametrize("ref", [WIKI, DRIVE, "doc-7"])
def test_chat_offers_a_draft_only_where_the_owning_connector_writes(two, ref, request):
    from types import SimpleNamespace

    from heinzel.modules.heinzel._user_chat import UserChatService

    _client, kernel, current, session, _states = two
    grant = SimpleNamespace(capability="documents", method="read", review=SimpleNamespace(fixed_arguments={"ref": ref}))
    connected = SimpleNamespace(approved=lambda _session: grant, verify_approved=lambda _session, _grant: None)
    chat = SimpleNamespace(_playbook_kernel=kernel, _crud=None, current=current)
    tools = UserChatService._document_drafts(chat, session, "t", connected, writer=True)
    assert tools is not None
    offered = {DRIVE: True, WIKI: request.node.callspec.params["two"]["wiki"][1], "doc-7": False}[ref]
    assert tools.has_document is offered
