"""The actual private chat confirmation boundary admits one selected fake conditional write."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from heinzel import Config, CurrentUser, Document, HeinzelError, Kernel, Module, ModuleRecord, Settings
from heinzel.heads import web
from heinzel.kernel import (
    CapturedWriteAcknowledgement,
    CapturedWriteLease,
    CapturedWriteReview,
    CapturedWriteService,
    Origin,
    discover,
)
from heinzel.modules.heinzel._captured_write_binding import SelectedWriteService
from heinzel.modules.heinzel._captured_write_host import CapturedWriteHost
from heinzel.modules.heinzel._session_crud import SessionCrud

pytestmark = pytest.mark.integration
_STATE = {"revision": "one", "writes": 0, "opens": 0, "lose_ack": False}


class FakeDocuments:
    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="Fixture", text="")

    def replace_section(self, ref: str, marker: str, text: str) -> None:
        msg = "The generic legacy write method must not be called"
        raise AssertionError(msg)


class FakeWrites(CapturedWriteService):
    def open_lease(self, draft):
        _STATE["opens"] += 1

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
                pass

        return Lease()


def _kernel_and_client(tmp_path, monkeypatch):
    provider = Module("fakewrite", "Fixture provider")
    provider.service(FakeWrites)
    provider.provides("documents", name="fakewrite", modes={"read", "write"}, hosts=["fixture.invalid"])(FakeDocuments)
    config = Config(files=(), data={"capabilities": {"documents": "fakewrite"}})
    records = discover(plugins=False)[0] + [
        ModuleRecord(
            qualname="fakewrite", module=provider, origin=Origin(distribution="fixture", version="1", builtin=False)
        )
    ]
    kernel = Kernel(Settings(), records, config=config)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    app = web.build(kernel, port=None, token="fixture-token", static=None)
    return kernel, current, app


def _assert_binding_fences(kernel, current, session, monkeypatch):
    with current.local("cli"):
        selected = kernel.resolve(SelectedWriteService)
        draft = CapturedWriteHost(kernel.resolve(SessionCrud), current, selected).current(session, "draft-one")
        with pytest.raises(HeinzelError):
            selected.open_lease(draft.model_copy(update={"binding_id": "f" * 64}))


def _assert_activity(client, auth, session, expected):
    path = "/api/heinzel/captured-write-activity-page"
    before = _STATE["opens"]
    assert client.get(path, params={"session_id": session}).status_code == 401
    response = client.get(path, headers=auth, params={"session_id": session})
    assert response.status_code == 200, response.text
    assert [item["status"] for item in response.json()["items"]] == [expected]
    assert "private exact replacement" not in response.text
    assert _STATE["opens"] == before


@pytest.mark.parametrize("lost", [False, True])
def test_fake_write_requires_exact_owner_chat_confirmation_and_never_replays(tmp_path, monkeypatch, lost):
    _STATE.update(revision="one", writes=0, opens=0, lose_ack=lost)
    kernel, current, app = _kernel_and_client(tmp_path, monkeypatch)
    auth = {"Authorization": "Bearer fixture-token"}
    with TestClient(app, base_url="http://localhost") as client:
        created = client.post("/api/heinzel/new-chat", headers=auth, json={"request_id": "new"})
        assert created.status_code == 200
        session = created.json()["session_id"]
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
        drafted = client.post("/api/heinzel/propose-captured-write", headers=auth, json={"proposal": proposal})
        assert drafted.status_code == 200, drafted.text
        assert _STATE["writes"] == 0
        assert _STATE["opens"] == 0
        _assert_activity(client, auth, session, "draft")
        _assert_binding_fences(kernel, current, session, monkeypatch)
        assert _STATE["opens"] == 0
        stage = client.post(
            "/api/system/runner/chat/propose-captured-write",
            headers=auth,
            params={"durable_session": session, "request_id": "draft-one"},
        )
        assert stage.status_code == 200, stage.text
        action = next(item for item in stage.json()["actions"] if item["operation"] == "execute-captured-write")
        assert action["command"] == "Current section text is not shown; review the full replacement and base revision."
        assert action["arguments"]["review"]["content"] == "private exact replacement"
        assert action["arguments"]["review"]["account"] == "account-one"
        assert action["arguments"]["review"]["base_revision"] == "one"
        assert _STATE["writes"] == 0
        direct = client.post(
            "/api/heinzel/execute-captured-write", headers=auth, json={**action["arguments"], "approval_proof": "fake"}
        )
        assert direct.status_code == 400
        decision = {"session": stage.json()["id"], "action": action["id"]}
        assert client.post("/api/system/runner/chat/confirm", params=decision).status_code == 401
        result = client.post("/api/system/runner/chat/confirm", headers=auth, params=decision)
        assert result.status_code == 200, result.text
        found = next(item for item in result.json()["actions"] if item["id"] == action["id"])
        assert found["status"] == ("uncertain" if lost else "done")
        status = client.get(
            "/api/heinzel/captured-write-status",
            headers=auth,
            params={"session_id": session, "request_id": "draft-one"},
        )
        assert status.json()["status"] == ("uncertain" if lost else "acknowledged")
        _assert_activity(client, auth, session, "uncertain" if lost else "acknowledged")
        if lost:
            assert status.json()["receipt"] is None
        else:
            receipt = status.json()["receipt"]
            assert (receipt["provider"], receipt["account"], receipt["source"], receipt["reference"]) == (
                "fakewrite",
                "account-one",
                "source-one",
                "fixture://document/one",
            )
            assert (receipt["base_revision"], receipt["revision"]) == ("one", "two")
        assert client.post("/api/system/runner/chat/confirm", headers=auth, params=decision).status_code == 400
        assert _STATE["writes"] == 1
