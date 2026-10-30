"""Opt-in host source discovery with actual private review and fake provider reads."""

import sys
from uuid import uuid4

import pytest

from heinzel import Config, CurrentUser, HeinzelError
from heinzel.kernel import ConnectedReadService
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._source_loading import SourceDiscovery
from heinzel.modules.heinzel._capability_review import describe_capability_read
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from tests._source_distribution_fixture import installed_entry
from tests.integration.chat_test import AUTH, web_client
from tests.integration.scheduled_connected_read_test import _approve_read

pytestmark = pytest.mark.integration


@pytest.fixture
def bound_source(tmp_path, monkeypatch, settings):
    from heinzel.kernel import _discovery

    package = "fixture_captured_provider_" + uuid4().hex
    root = tmp_path / package
    root.mkdir()
    source = root / "__init__.py"
    source.write_text("""from pathlib import Path
from heinzel import Module, ConnectedReadLease, ConnectedReadService
from heinzel import ConnectedReadMethod, ConnectedReadReview
module = Module("fictional", "Offline fixture")
owner = ""
schema = ""
reads = []
constructed = []
mutate_read = False
class Lease(ConnectedReadLease):
    def __init__(self, session, operation, fixed): self.session, self.operation, self.fixed = session, operation, fixed
    def review(self):
        return ConnectedReadReview(owner=owner, session=self.session, operation=self.operation, schema_sha256=schema,
            source_revision="fixture", account_identity="account-one", provider_scopes=("read",),
            fixed_arguments=self.fixed)
    def read(self, arguments):
        reads.append(True)
        if mutate_read: Path(__file__).write_text("# changed during read\\n")
        return {"events": []}
    def close(self): pass
class Provider(ConnectedReadService):
    def __init__(self): constructed.append(True)
    def supported_reads(self): return (ConnectedReadMethod(capability="calendar", method="events"),)
    def open_lease(self, session, operation, fixed_arguments): return Lease(session, operation, dict(fixed_arguments))
module.service(Provider)
class Calendar:
    def events(self, window): return []
    def free_busy(self, people, window): return []
module.provides("calendar", modes={"read"}, name="fictional", hosts=["fixture.invalid"])(Calendar)
""")
    entry = installed_entry(root, "fictional", package, declared=True)
    monkeypatch.setattr(_discovery, "entry_points", lambda **_kwargs: [entry])
    monkeypatch.syspath_prepend(str(tmp_path))
    config = Config(files=(), data={"capabilities": {"calendar": "fictional"}})
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    from heinzel.kernel._boot import boot

    kernel = boot(settings)
    sources = kernel.source_discovery
    assert sources is not None
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    plugin = sys.modules[package]
    with current.local("cli"):
        plugin.owner = current.principal().id
    plugin.schema = arguments_hash(describe_capability_read(kernel, "calendar", "events").schema)
    yield client, kernel, current, session, plugin, source, sources
    finder = plugin.__spec__.loader
    if finder in sys.meta_path:
        sys.meta_path.remove(finder)
    sys.modules.pop(package, None)


def test_private_review_and_revocation_use_host_source_bound_provider(bound_source):
    client, kernel, current, session, plugin, source, _ = bound_source
    state = {"reads": 0}
    proposal = _approve_read(client, kernel, current, session, state, rolling=False)
    assert proposal.json()["review"]["source_revision"].startswith("host-source-v1:")
    grants = kernel.resolve(ConnectedReadGrants)
    with current.local("cli"), grants.crud.repository.main_writer(session) as writer:
        assert grants.read(session, writer) == {"events": []}
    assert plugin.reads == [True]
    revoked = client.post(
        "/api/heinzel/revoke-connected-read", headers=AUTH, json={"session_id": session, "request_id": "grant-one"}
    )
    assert revoked.status_code == 200
    with current.local("cli"), grants.crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert plugin.reads == [True]
    source.write_text("# changed after loading\n")
    with pytest.raises(HeinzelError):
        kernel.source_discovery.binding(kernel, "calendar", ConnectedReadService)


def test_unreviewed_source_cannot_open_a_connected_lease(bound_source):
    client, kernel, _current, session, plugin, _source, _ = bound_source
    kernel.source_discovery = SourceDiscovery({})
    proposed = client.post(
        "/api/heinzel/propose-connected-read",
        headers=AUTH,
        json={
            "session_id": session,
            "request_id": "unreviewed",
            "capability": "calendar",
            "method": "events",
            "fixed_arguments": {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}},
        },
    )
    assert proposed.status_code != 200
    assert plugin.reads == []
    assert plugin.constructed == []


def test_stale_source_is_rejected_before_service_constructor(bound_source):
    client, _kernel, _current, session, plugin, source, _ = bound_source
    source.write_text("# changed before provider construction\n")
    response = client.post(
        "/api/heinzel/propose-connected-read",
        headers=AUTH,
        json={
            "session_id": session,
            "request_id": "stale",
            "capability": "calendar",
            "method": "events",
            "fixed_arguments": {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}},
        },
    )
    assert response.status_code != 200
    assert plugin.constructed == []


def test_default_boot_undeclared_provider_is_rejected_before_construction(
    bound_source, monkeypatch, settings, tmp_path
):
    _client, _kernel, current, _session, plugin, source, _ = bound_source
    from heinzel.kernel import _discovery
    from heinzel.kernel._boot import boot

    entry = installed_entry(source.parent, "fictional", plugin.__name__, declared=False)
    monkeypatch.setattr(_discovery, "entry_points", lambda **_kwargs: [entry])
    kernel = boot(settings)
    kernel.bind(CurrentUser, current)
    (tmp_path / "other").mkdir()
    client = web_client(kernel, tmp_path / "other")
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    response = client.post(
        "/api/heinzel/propose-connected-read",
        headers=AUTH,
        json={
            "session_id": session,
            "request_id": "undeclared",
            "capability": "calendar",
            "method": "events",
            "fixed_arguments": {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}},
        },
    )
    assert response.status_code != 200
    assert plugin.constructed == []


def test_approved_grant_fails_closed_after_source_change(bound_source):
    client, kernel, current, session, plugin, source, _ = bound_source
    _approve_read(client, kernel, current, session, {"reads": 0}, rolling=False)
    source.write_text("# changed before the read\n")
    grants = kernel.resolve(ConnectedReadGrants)
    with current.local("cli"), grants.crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert plugin.reads == []


def test_in_flight_source_change_discards_provider_result(bound_source):
    client, kernel, current, session, plugin, _source, _ = bound_source
    _approve_read(client, kernel, current, session, {"reads": 0}, rolling=False)
    plugin.mutate_read = True
    grants = kernel.resolve(ConnectedReadGrants)
    with current.local("cli"), grants.crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert plugin.reads == [True]


def test_distribution_manifest_change_after_approval_denies_read(bound_source):
    client, kernel, current, session, plugin, source, _ = bound_source
    _approve_read(client, kernel, current, session, {"reads": 0}, rolling=False)
    (source.parent.parent / "fictional-1.0.dist-info" / "RECORD").write_text("")
    grants = kernel.resolve(ConnectedReadGrants)
    with current.local("cli"), grants.crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert plugin.reads == []


def test_declared_source_removal_revokes_bound_read(bound_source):
    client, kernel, current, session, plugin, source, _ = bound_source
    _approve_read(client, kernel, current, session, {"reads": 0}, rolling=False)
    metadata = source.parent.parent / "fictional-1.0.dist-info" / "entry_points.txt"
    metadata.write_text(f"[heinzel.modules]\nfictional = {source.parent.name}\n")
    grants = kernel.resolve(ConnectedReadGrants)
    with current.local("cli"), grants.crud.repository.main_writer(session) as writer, pytest.raises(HeinzelError):
        grants.read(session, writer)
    assert plugin.reads == []
