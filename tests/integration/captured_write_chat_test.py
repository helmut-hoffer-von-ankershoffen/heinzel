"""Exact human chat confirmation of an installed source-bound offline conditional writer."""

import sys
from uuid import uuid4

import pytest

from heinzel import ActionGate, Config, CurrentUser, HeinzelError, ProposedCall
from heinzel.kernel._boot import boot
from heinzel.modules.heinzel._captured_write_binding import SelectedWriteService
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from heinzel.modules.heinzel._session_crud import SessionCrud
from tests._source_distribution_fixture import installed_entry
from tests.integration.chat_test import AUTH, web_client

pytestmark = pytest.mark.integration
_SOURCE = r"""
import hashlib
from pathlib import Path
from heinzel import (
    Module, Document, CapturedWriteAcknowledgement, CapturedWriteLease,
    CapturedWriteReview, CapturedWriteService,
)
_STATE = {"revision": "one", "writes": 0, "opens": 0, "lose_ack": False, "closed": 0, "mutate_open": False}
class FakeDocuments:
    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="Fixture", text="")



class FakeWrites(CapturedWriteService):
    supported_operations = frozenset({"insert-draft"})
    def open_lease(self, draft):
        _STATE["opens"] += 1
        if _STATE["mutate_open"]:
            Path(__file__).write_text("# source changed while opening a lease\n")

        class Lease(CapturedWriteLease):
            def review(self):
                return CapturedWriteReview(
                    owner=draft.owner,
                    session=draft.session,
                    provider=draft.provider,
                    binding_id=draft.binding_id,
                    account=draft.account,
                    source=draft.source,
                    reference=draft.reference,
                    operation=draft.operation,
                    section=draft.section,
                    base_revision=str(_STATE["revision"]),
                    content_sha256=hashlib.sha256(draft.content.get_secret_value().encode()).hexdigest(),
                    write_scope=draft.write_scope,
                    conditional_mutation=True,
                )

            def write(self):
                if _STATE["revision"] != draft.base_revision:
                    msg = "revision conflict"
                    raise ValueError(msg)
                _STATE["writes"] += 1
                _STATE["revision"] = "two"
                if _STATE["lose_ack"]:
                    msg = "synthetic lost acknowledgement"
                    raise OSError(msg)
                return CapturedWriteAcknowledgement(reference=draft.reference, revision="two")

            def close(self):
                _STATE["closed"] += 1

        return Lease()


module = Module("fakewrite", "Offline captured-write fixture")
module.service(FakeWrites)
module.provides("documents", name="fakewrite", modes={"read"}, hosts=["fixture.invalid"])(FakeDocuments)
"""


@pytest.fixture
def captured_client(tmp_path, monkeypatch, settings, request):
    from heinzel.kernel import _discovery

    package = "fixture_conditional_write_" + uuid4().hex
    root = tmp_path / package
    root.mkdir()
    source = root / "__init__.py"
    source.write_text(getattr(request, "param", _SOURCE))
    entry = installed_entry(root, "fakewrite", package, declared=True)
    monkeypatch.setattr(_discovery, "entry_points", lambda **_kwargs: [entry])
    monkeypatch.syspath_prepend(str(tmp_path))
    config = Config(
        files=(), data={"capabilities": {"documents": "fakewrite"}, "hz": {"features": ["captured_writes"]}}
    )
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    kernel = boot(settings.model_copy(update={"features": frozenset({"captured_writes"})}))
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    client = web_client(kernel, tmp_path)
    plugin = sys.modules[package]
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    proposal = {
        "session_id": session,
        "request_id": "draft-one",
        "account": "account-one",
        "source": "source-one",
        "reference": "fixture://document/one",
        "operation": "insert-draft",
        "section": "summary",
        "base_revision": "one",
        "write_scope": "fixture-write",
        "content": "private exact replacement",
    }
    yield client, kernel, current, session, proposal, plugin._STATE, source
    finder = plugin.__spec__.loader
    if finder in sys.meta_path:
        sys.meta_path.remove(finder)
    sys.modules.pop(package, None)


def stage(client, session):
    response = client.post(
        "/api/system/runner/chat/propose-captured-write",
        headers=AUTH,
        params={"durable_session": session, "request_id": "draft-one"},
    )
    assert response.status_code == 200, response.text
    staged = response.json()
    action = next(item for item in staged["actions"] if item["operation"] == "execute-captured-write")
    return staged, action


@pytest.mark.parametrize("lost", [False, True])
def test_exact_owner_confirmation_acknowledges_or_remains_uncertain_without_replay(captured_client, lost):
    client, kernel, current, session, proposal, state, _source = captured_client
    state["lose_ack"] = lost
    drafted = client.post("/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": proposal})
    assert drafted.status_code == 200, drafted.text
    assert state["opens"] == state["writes"] == 0
    staged, action = stage(client, session)
    assert action["arguments"]["review"]["content"] == proposal["content"]
    assert action["arguments"]["review"]["base_revision"] == "one"
    assert state["writes"] == 0
    with current.local("cli"):
        host = CapturedWriteHost(kernel.resolve(SessionCrud), current, kernel.resolve(SelectedWriteService))
        draft = host.current(session, "draft-one")
        with pytest.raises(HeinzelError):
            host.provider.open_lease(draft.model_copy(update={"binding_id": "f" * 64}))
    with current.local("cli"), pytest.raises(HeinzelError, match="human review surface"):
        kernel.resolve(ActionGate).propose(
            ProposedCall(
                session=staged["id"],
                module="heinzel",
                operation="execute-captured-write",
                tool="heinzel_execute_captured_write",
                arguments=action["arguments"],
                command="forged review",
            )
        )
    direct = client.post(
        "/api/heinzel/execute-captured-write", headers=AUTH, json={**action["arguments"], "approval_proof": "fake"}
    )
    assert direct.status_code == 400
    decision = {"session": staged["id"], "action": action["id"]}
    assert client.post("/api/system/runner/chat/confirm", params=decision).status_code == 401
    result = client.post("/api/system/runner/chat/confirm", headers=AUTH, params=decision)
    assert result.status_code == 200, result.text
    found = next(item for item in result.json()["actions"] if item["id"] == action["id"])
    assert found["status"] == ("uncertain" if lost else "done")
    response = client.get(
        "/api/heinzel/captured-write-status", headers=AUTH, params={"session_id": session, "request_id": "draft-one"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == ("uncertain" if lost else "acknowledged")
    if lost:
        assert response.json()["receipt"] is None
    else:
        assert response.json()["receipt"]["revision"] == "two"
    assert client.post("/api/system/runner/chat/confirm", headers=AUTH, params=decision).status_code == 400
    assert state["writes"] == 1


@pytest.mark.parametrize("changed", ["revision", "source", "feature"])
def test_stale_or_disabled_provider_never_writes(captured_client, changed, monkeypatch):
    client, _kernel, _current, session, proposal, state, source = captured_client
    drafted = client.post("/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": proposal})
    assert drafted.status_code == 200, drafted.text
    staged, action = stage(client, session)
    if changed == "revision":
        state["revision"] = "changed"
    elif changed == "source":
        source.write_text("# changed after reviewed source capture\n")
    else:
        disabled = Config(files=(), data={"capabilities": {"documents": "fakewrite"}})
        monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: disabled))
    response = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": staged["id"], "action": action["id"]}
    )
    assert response.status_code == 400, response.text
    assert state["writes"] == 0


def test_default_off_flag_refuses_before_a_provider_lease(captured_client):
    client, kernel, _current, _session, proposal, state, _source = captured_client
    kernel.settings = kernel.settings.model_copy(update={"features": frozenset()})
    response = client.post("/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": proposal})
    assert response.status_code == 400, response.text
    assert state["opens"] == state["writes"] == 0


def test_source_changed_during_open_is_closed_without_a_write(captured_client):
    client, _kernel, _current, session, proposal, state, _source = captured_client
    response = client.post("/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": proposal})
    assert response.status_code == 200, response.text
    state["mutate_open"] = True
    response = client.post(
        "/api/system/runner/chat/propose-captured-write",
        headers=AUTH,
        params={"durable_session": session, "request_id": "draft-one"},
    )
    assert response.status_code == 400, response.text
    assert state["opens"] == state["closed"] == 1
    assert state["writes"] == 0


@pytest.mark.parametrize(
    "captured_client",
    [
        _SOURCE.replace('    supported_operations = frozenset({"insert-draft"})\n', ""),
        _SOURCE.replace('frozenset({"insert-draft"})', "frozenset()"),
        _SOURCE.replace('frozenset({"insert-draft"})', 'frozenset({"replace-section"})'),
        _SOURCE.replace('frozenset({"insert-draft"})', '{"insert-draft"}'),
        _SOURCE.replace(
            "class FakeWrites(CapturedWriteService):",
            "class ForeignDeclaration(CapturedWriteService):\n"
            '    supported_operations = frozenset({"insert-draft"})\n\n'
            "class FakeWrites(ForeignDeclaration):",
        ).replace(
            'class FakeWrites(ForeignDeclaration):\n    supported_operations = frozenset({"insert-draft"})',
            "class FakeWrites(ForeignDeclaration):",
        ),
        _SOURCE.replace("module.service(FakeWrites)", "# no registered captured service"),
    ],
    indirect=True,
    ids=["absent", "empty", "unsupported", "mutable", "inherited", "unregistered"],
)
def test_invalid_captured_declaration_never_opens_a_lease(captured_client):
    client, _kernel, _current, _session, proposal, state, _source = captured_client
    response = client.post("/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": proposal})
    assert response.status_code == 400, response.text
    assert state["opens"] == state["writes"] == 0


@pytest.mark.parametrize("change", ["declaration", "instance-declaration", "foreign-service", "configuration"])
def test_declared_service_continuity_is_rechecked_before_confirmation(captured_client, change, monkeypatch):
    from heinzel import CapturedWriteService

    client, kernel, _current, session, proposal, state, _source = captured_client
    response = client.post("/api/heinzel/propose-captured-write", headers=AUTH, json={"proposal": proposal})
    assert response.status_code == 200, response.text
    staged, action = stage(client, session)
    selected = kernel.resolve(SelectedWriteService)
    service = selected.service
    assert service is not None
    if change == "declaration":
        monkeypatch.setattr(type(service), "supported_operations", frozenset())
    elif change == "instance-declaration":
        monkeypatch.setattr(service, "supported_operations", frozenset({"insert-draft"}))
    elif change == "foreign-service":

        class ForeignWrites(CapturedWriteService):
            supported_operations = frozenset({"insert-draft"})

        kernel.bind(type(service), ForeignWrites())
    else:
        changed = Config(
            files=(), data={"capabilities": {"documents": "another"}, "hz": {"features": ["captured_writes"]}}
        )
        monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: changed))
    response = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": staged["id"], "action": action["id"]}
    )
    assert response.status_code == 400, response.text
    assert state["writes"] == 0


def test_read_only_protocol_registration_and_exact_captured_declaration_are_separate(captured_client):
    _client, kernel, _current, _session, _proposal, state, _source = captured_client
    selected = kernel.resolve(SelectedWriteService)
    selected._resolved()
    assert selected.registration.modes == frozenset({"read"})
    assert not hasattr(selected.registration.factory, "replace_section")
    assert selected.service.supported_operations == frozenset({"insert-draft"})
    assert state["writes"] == 0
