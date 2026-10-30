"""Private human review of an owner-bound connected read with an inert fake provider."""

import pytest

from heinzel import (
    Config,
    ConnectedReadLease,
    ConnectedReadMethod,
    ConnectedReadReview,
    ConnectedReadService,
    CurrentUser,
    HeinzelError,
    Module,
)
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._capability_review import describe_capability_read
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from tests.integration.chat_test import AUTH, web_client

pytestmark = pytest.mark.integration


def _fail_if(state, where):
    if state.get("failure") == where:
        msg = f"secret-sentinel-{where}"
        raise HeinzelError(msg)


def _mutate_if_requested(state, fixed_arguments):
    if state.get("failure") == "mutate":
        fixed_arguments["window"]["since"] = "2025-01-01T00:00:00Z"


def _fixture(make_kernel, tmp_path, monkeypatch):
    state = {"opens": 0, "reads": 0, "account": "account-one"}
    holder = {}

    class Lease(ConnectedReadLease):
        def __init__(self, session, operation, fixed):
            self.session, self.operation, self.fixed = session, operation, fixed

        def review(self):
            _fail_if(state, "review")
            descriptor = describe_capability_read(holder["kernel"], "calendar", "events")
            return ConnectedReadReview(
                owner=holder["current"].principal().id,
                session=self.session,
                operation=self.operation,
                schema_sha256=arguments_hash(descriptor.schema),
                source_revision="source-one",
                account_identity=state["account"],
                provider_scopes=("read",),
                fixed_arguments=self.fixed,
                item_identity=state.get("item_identity"),
            )

        def read(self, _arguments):
            state["reads"] += 1
            return {"events": []}

        def close(self):
            _fail_if(state, "close")

    class Provider(ConnectedReadService):
        def supported_reads(self):
            return (ConnectedReadMethod(capability="calendar", method="events"),)

        def open_lease(self, session, operation, fixed_arguments):
            state["opens"] += 1
            _fail_if(state, "open")
            _mutate_if_requested(state, fixed_arguments)
            return Lease(session, operation, dict(fixed_arguments))

    class Calendar:
        def events(self, _window):
            return []

        def free_busy(self, _people, _window):
            return []

    module = Module("fictional", "Offline fixture")
    module.service(Provider)
    module.provides("calendar", modes={"read"}, name="fictional", hosts=["fixture.invalid"])(Calendar)
    from tests.unit.modules.user_chat_test import fake_claude

    executable = fake_claude(tmp_path, tool="connected_read", arguments={})
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(executable))
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "claude-code")
    monkeypatch.setenv("HZ_CAPABILITIES__AGENT_RUNNER", "claude-code")
    config = Config(files=(), data={"capabilities": {"calendar": "fictional"}})
    kernel = make_kernel(builtins=True, config=config, fictional=module)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    holder.update(kernel=kernel, current=current)
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    return client, kernel, current, session, state


def _approve_read(client, kernel, current, session, state, rolling):  # noqa: PLR0913, PLR0917
    body = {
        "session_id": session,
        "request_id": "grant-one",
        "capability": "calendar",
        "method": "events",
        "fixed_arguments": {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}},
    }
    endpoint = "/api/heinzel/propose-connected-read"
    if rolling:
        endpoint = "/api/heinzel/propose-rolling-connected-read"
        body["fixed_arguments"] = {}
        body["window_policy"] = {
            "parameter": "window",
            "lookback_seconds": 60,
            "lookahead_seconds": 600,
            "timezone": "Europe/Berlin",
        }
    proposed = client.post(endpoint, headers=AUTH, json=body)
    assert proposed.status_code == 200, proposed.text
    assert state["reads"] == 0
    assert (
        client.get("/api/heinzel/connected-read-state", headers=AUTH, params={"session_id": session}).json()["status"]
        == "proposed"
    )
    params = {"durable_session": session, "request_id": "grant-one"}
    stage = client.post("/api/system/runner/chat/propose-connected-read-review", headers=AUTH, params=params)
    assert stage.status_code == 200, stage.text
    chat = stage.json()
    action = next(item for item in chat["actions"] if item["operation"] == "approve-connected-read")
    assert action["arguments"]["review"] == proposed.json()
    assert "approval_proof" not in action["arguments"]
    result = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat["id"], "action": action["id"]}
    )
    assert result.status_code == 200, result.text
    assert next(item for item in result.json()["actions"] if item["id"] == action["id"])["status"] == "done"
    assert state["reads"] == 0
    again = client.post("/api/system/runner/chat/propose-connected-read-review", headers=AUTH, params=params)
    assert again.json()["actions"] == result.json()["actions"]
    with current.local("cli"):
        assert kernel.resolve(ConnectedReadGrants).approved(session).review.account_identity == "account-one"
    return proposed


def _invalidate_grant(client, session, change):
    if change == "revoked":
        result = client.post(
            "/api/heinzel/revoke-connected-read", headers=AUTH, json={"session_id": session, "request_id": "grant-one"}
        )
    else:
        result = client.post(
            "/api/heinzel/propose-connected-read",
            headers=AUTH,
            json={
                "session_id": session,
                "request_id": "grant-two",
                "capability": "calendar",
                "method": "events",
                "fixed_arguments": {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}},
            },
        )
    assert result.status_code == 200


@pytest.mark.parametrize("change", ["revoked", "replaced"])
@pytest.mark.parametrize("rolling", [False, True])
def test_scheduled_read_requires_exact_separate_consent_and_survives_restart(  # noqa: PLR0913, PLR0917
    make_kernel, tmp_path, monkeypatch, settings, change, rolling
):
    client, kernel, current, session, state = _fixture(make_kernel, tmp_path, monkeypatch)
    proposed = _approve_read(client, kernel, current, session, state, rolling)
    with current.local("cli"):
        crud = kernel.resolve(ConnectedReadGrants).crud
        crud.propose_scheduled_agent(session, "contract", "Read the approved source", None, 30)
    card = client.post(
        "/api/system/runner/chat/propose-agent-contract",
        headers=AUTH,
        params={"durable_session": session, "request_id": "contract"},
    ).json()
    action = next(a for a in card["actions"] if a["operation"] == "accept-agent-contract")
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": card["id"], "action": action["id"]}
        ).status_code
        == 200
    )
    args = {
        "session_id": session,
        "request_id": "recurring",
        "contract_request_id": "contract",
        "cadence_seconds": 300,
        "max_requests": 4,
        "timeout_seconds": 30,
        "connected_request_id": "grant-one",
    }
    scheduled = client.post("/api/heinzel/propose-agent-schedule", headers=AUTH, json=args)
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["connected_grant"] == proposed.json()
    staged = client.post(
        "/api/system/runner/chat/propose-agent-schedule-review",
        headers=AUTH,
        params={"durable_session": session, "request_id": "recurring"},
    ).json()
    action = next(a for a in staged["actions"] if a["operation"] == "approve-agent-schedule")
    assert action["arguments"]["review"] == scheduled.json()
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": staged["id"], "action": action["id"]}
        ).status_code
        == 200
    )
    from datetime import UTC, datetime

    from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules

    state["account"] = "different-account"
    with current.local("daemon"), pytest.raises(HeinzelError):
        kernel.resolve(AutonomousSchedules).current(session, "recurring")
    assert state["reads"] == 0
    state["account"] = "account-one"

    from heinzel.kernel import CompiledJobs
    from heinzel.modules.system.daemon import DaemonService, DaemonSettings

    with current.local("daemon"):
        jobs = kernel.resolve(CompiledJobs).jobs
        job = next(job for job in jobs if job.name.startswith("agent-" + session.lower()))
        now = datetime.now(UTC)
        daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC)
        registered = [item for item in daemon._jobs() if item.id == "heinzel:" + job.name]
        monkeypatch.setattr(daemon, "_jobs", lambda: registered)
        monkeypatch.setattr(daemon, "_post_notices", lambda _log: None)
        report = daemon.tick()
        assert any(item.job == "heinzel:" + job.name and item.disposition == "rendered" for item in report.runs)
        assert state["reads"] == 1
        calls = (tmp_path / "model-calls").read_text()
        restarted = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC)
        monkeypatch.setattr(restarted, "_jobs", lambda: registered)
        monkeypatch.setattr(restarted, "_post_notices", lambda _log: None)
        restarted.tick()
        assert (tmp_path / "model-calls").read_text() == calls
        assert state["reads"] == 1
    _invalidate_grant(client, session, change)
    with current.local("daemon"), pytest.raises(HeinzelError):
        kernel.resolve(AutonomousSchedules).current(session, "recurring")
    assert state["reads"] == 1


def test_compiled_daemon_notification_requires_exact_read_and_schedule_consents(
    make_kernel, tmp_path, monkeypatch, settings
):
    import asyncio
    from datetime import UTC, datetime
    from typing import override

    from heinzel import Notifications
    from heinzel.kernel import CompiledJobs
    from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules
    from heinzel.modules.system.daemon import DaemonService, DaemonSettings
    from tests.unit.modules.user_chat_test import fake_claude

    class Notices(Notifications):
        def __init__(self):
            self.calls = []

        @override
        def emit_once(self, _kind, title, body, *, route, command, key):
            self.calls.append((title, body, key))
            return True

    client, kernel, current, session, state = _fixture(make_kernel, tmp_path, monkeypatch)
    provider = Notices()
    kernel.bind(Notifications, provider)
    _approve_read(client, kernel, current, session, state, rolling=False)
    fake_claude(tmp_path, tool="notification:send", arguments={"topic": "example", "title": "Title", "body": "Private"})
    with current.local("cli"):
        crud = kernel.resolve(ConnectedReadGrants).crud
        crud.propose_scheduled_agent(session, "contract", "Inspect the source and report", None, 30)
    card = client.post(
        "/api/system/runner/chat/propose-agent-contract",
        headers=AUTH,
        params={"durable_session": session, "request_id": "contract"},
    ).json()
    action = next(a for a in card["actions"] if a["operation"] == "accept-agent-contract")
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": card["id"], "action": action["id"]}
        ).status_code
        == 200
    )
    proposed = client.post(
        "/api/heinzel/propose-agent-schedule",
        headers=AUTH,
        json={
            "session_id": session,
            "request_id": "recurring",
            "contract_request_id": "contract",
            "cadence_seconds": 3600,
            "max_requests": 4,
            "timeout_seconds": 30,
            "connected_request_id": "grant-one",
            "notification_policy": {"max_per_24h": 2, "topic_cooldown_seconds": 300},
        },
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["notification_policy"]["per_turn"] == 1
    with current.local("daemon"), pytest.raises(HeinzelError):
        asyncio.run(kernel.resolve(AutonomousSchedules).tick(session, "recurring", datetime.now(UTC)))
    assert provider.calls == []
    staged = client.post(
        "/api/system/runner/chat/propose-agent-schedule-review",
        headers=AUTH,
        params={"durable_session": session, "request_id": "recurring"},
    ).json()
    action = next(a for a in staged["actions"] if a["operation"] == "approve-agent-schedule")
    assert action["arguments"]["review"] == proposed.json()
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": staged["id"], "action": action["id"]}
        ).status_code
        == 200
    )
    with current.local("daemon"):
        job = next(job for job in kernel.resolve(CompiledJobs).jobs if job.name.startswith("agent-" + session.lower()))
        now = datetime.now(UTC)
        daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC)
        registered = [item for item in daemon._jobs() if item.id == "heinzel:" + job.name]
        monkeypatch.setattr(daemon, "_jobs", lambda: registered)
        monkeypatch.setattr(daemon, "_post_notices", lambda _log: None)
        daemon.tick()
        assert len(provider.calls) == 1
        restarted = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC)
        monkeypatch.setattr(restarted, "_jobs", lambda: registered)
        monkeypatch.setattr(restarted, "_post_notices", lambda _log: None)
        restarted.tick()
        assert len(provider.calls) == 1
    _invalidate_grant(client, session, "revoked")
    with current.local("daemon"), pytest.raises(HeinzelError):
        kernel.resolve(AutonomousSchedules).current(session, "recurring")
    assert len(provider.calls) == 1


def test_item_notification_policy_needs_exact_separate_schedule_confirmation(make_kernel, tmp_path, monkeypatch):
    import asyncio
    from datetime import UTC, datetime

    from heinzel import ConnectedItemIdentity
    from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules

    client, kernel, current, session, state = _fixture(make_kernel, tmp_path, monkeypatch)
    state["item_identity"] = ConnectedItemIdentity(collection=("events",), identity_field="id", revision_field="rev")
    _approve_read(client, kernel, current, session, state, rolling=False)
    with current.local("cli"):
        crud = kernel.resolve(ConnectedReadGrants).crud
        crud.propose_scheduled_agent(session, "contract", "Inspect the approved source", None, 30)
    card = client.post(
        "/api/system/runner/chat/propose-agent-contract",
        headers=AUTH,
        params={"durable_session": session, "request_id": "contract"},
    ).json()
    action = next(item for item in card["actions"] if item["operation"] == "accept-agent-contract")
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": card["id"], "action": action["id"]}
        ).status_code
        == 200
    )
    policy = {
        "max_per_24h": 2,
        "topic_cooldown_seconds": 60,
        "item_evidence": {"deduplicate_by": "revision"},
        "disclosure": (
            "Content may appear on this device. Item identities are provider-reported and not completeness guarantees."
        ),
    }
    body = {
        "session_id": session,
        "request_id": "schedule",
        "contract_request_id": "contract",
        "cadence_seconds": 60,
        "max_requests": 2,
        "timeout_seconds": 30,
        "connected_request_id": "grant-one",
        "notification_policy": policy,
    }
    proposed = client.post("/api/heinzel/propose-agent-schedule", headers=AUTH, json=body)
    assert proposed.status_code == 200, proposed.text
    with current.local("daemon"), pytest.raises(HeinzelError):
        asyncio.run(kernel.resolve(AutonomousSchedules).tick(session, "schedule", datetime.now(UTC)))
    assert state["reads"] == 0
    staged = client.post(
        "/api/system/runner/chat/propose-agent-schedule-review",
        headers=AUTH,
        params={"durable_session": session, "request_id": "schedule"},
    ).json()
    action = next(item for item in staged["actions"] if item["operation"] == "approve-agent-schedule")
    assert action["arguments"]["review"] == proposed.json()
    assert (
        client.post(
            "/api/system/runner/chat/confirm", headers=AUTH, params={"session": staged["id"], "action": action["id"]}
        ).status_code
        == 200
    )
    with current.local("cli"):
        approved = kernel.resolve(AutonomousSchedules).approved(session, "schedule")
    assert approved.notification_policy.item_evidence.deduplicate_by == "revision"
    assert state["reads"] == 0
