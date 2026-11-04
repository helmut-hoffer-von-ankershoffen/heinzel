"""Installed CLI publication, real daemon registry, edited document and exactly-once approved link with doubles."""

import json
import subprocess
from datetime import UTC, datetime

import pytest

from tests.e2e.draft_workflow_support import provider_wheel, terminal
from tests.e2e.tool_install_test import Sandbox, wheel
from tests.unit.modules.user_chat_test import fake_claude

__all__ = ["wheel"]
pytestmark = pytest.mark.e2e


@pytest.fixture
def sandbox(tmp_path, fixture_managed_python):
    # A per-test tool/cache root prevents historical or concurrent seed-store launcher collisions.
    return Sandbox(tmp_path, *fixture_managed_python)


def start_workflow(sandbox, wheel):
    state_path = sandbox.root / "provider-state.json"
    state_path.write_text(
        json.dumps(
            {
                "releases": [],
                "revision": 1,
                "document": "Earlier poems stay here.\n",
                "drafts": {},
                "appends": 0,
                "notices": [],
                "shares": [],
                "account": "document-account",
                "source": "document-source",
                "feed_account": "feed-account",
                "destination": "chosen-channel",
                "notice_actor": "owner-one",
            }
        )
    )
    fixture = provider_wheel(sandbox.root, state_path)
    sandbox.run("uv", "tool", "install", "--with", fixture, wheel)
    executable = fake_claude(sandbox.root)
    config = sandbox.root / "config/config.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(f"""[hz]
features = []
update_check = false
auto_update = false
[capabilities]
documents = "fixture"
code_host = "fixture"
ai_access = "claude-code"
agent_runner = "claude-code"
[system.runner]
claude_executable = {json.dumps(str(executable))}
[heinzel]
subscription_budget_mode = "plan-unknown"
[system.notify]
native = false
""")
    prompt = (
        "Create a Heinzel named Turing. Every five minutes watch https://fixture.invalid/CHANGELOG.md "
        "and draft a release poem in https://fixture.invalid/document, notify me, and share its link "
        "only after I edit and approve it."
    )
    terminal([str(sandbox.hz_bin), "heinzel", "start-release-workflow", "installed-one", prompt], sandbox.env)
    listing = json.loads(sandbox.hz("heinzel", "sessions", "--json"))
    session = listing["sessions"][0]["session_id"]
    snapshot = json.loads(sandbox.hz("heinzel", "session", session, "--json"))
    assert snapshot["messages"][0]["text"] == prompt
    state = json.loads(state_path.read_text())
    assert state["appends"] == 0
    assert state["shares"] == []
    return session, state_path


def add_installed_release(state_path):
    state = json.loads(state_path.read_text())
    state["releases"].append(
        {
            "identity": "release-one",
            "revision": "tag-sha-one",
            "published_at": datetime.now(UTC).isoformat(),
            "text": "A real provider release observation supplied by an isolated double.",
        }
    )
    state_path.write_text(json.dumps(state))


def test_installed_cli_daemon_release_draft_edit_approval_and_restart(sandbox, wheel):
    session, state_path = start_workflow(sandbox, wheel)
    add_installed_release(state_path)
    # This is the supported CLI tick used by launchd/systemd, in the isolated installation profile.
    report = json.loads(sandbox.hz("system", "daemon", "tick", "--json"))
    job = "heinzel:draft-" + session.lower()
    assert any(run["job"] == job and run["disposition"] == "rendered" for run in report["runs"]), report
    state = json.loads(state_path.read_text())
    assert state["appends"] == 1
    assert state["shares"] == []
    assert state["document"].startswith("Earlier poems stay here.\n")
    key = next(iter(state["drafts"]))
    state["drafts"][key] += "."
    state["document"] += "."
    state["revision"] += 1
    edited = state["drafts"][key]
    state_path.write_text(json.dumps(state))
    command = [str(sandbox.hz_bin), "heinzel", "approve-release-draft", session, key]

    def edit_after_preview():
        state = json.loads(state_path.read_text())
        state["drafts"][key] += "A later edit."
        state["document"] += "A later edit."
        state["revision"] += 1
        state_path.write_text(json.dumps(state))

    drift = terminal(command, sandbox.env, expected=1, before_answer=edit_after_preview)
    assert "changed" in drift
    assert json.loads(state_path.read_text())["shares"] == []
    edited = json.loads(state_path.read_text())["drafts"][key]
    terminal(command, sandbox.env)
    state = json.loads(state_path.read_text())
    assert len(state["shares"]) == 1
    assert state["shares"][0]["body"] == "https://fixture.invalid/document"
    assert state["drafts"][key] == edited
    workflow = json.loads(sandbox.hz("heinzel", "release-workflow", session, "--json"))
    receipt = next(item for item in workflow["events"] if item["kind"] == "draft.share-acknowledged")
    assert receipt["value"]["review"]["snapshot"]["text"] == edited
    assert receipt["value"]["review"]["snapshot"]["target"]["revision"] == str(state["revision"])
    sandbox.hz("system", "daemon", "tick", "--json")
    restarted = json.loads(state_path.read_text())
    assert restarted["appends"] == len(restarted["shares"]) == 1
    replay = terminal(command, sandbox.env, expected=1)
    assert "already attempted" in replay
    assert len(json.loads(state_path.read_text())["shares"]) == 1


@pytest.mark.parametrize("effect", ["append", "share"])
def test_installed_lost_acknowledgement_survives_new_daemon_and_cli_processes(sandbox, wheel, effect):
    session, state_path = start_workflow(sandbox, wheel)
    add_installed_release(state_path)
    state = json.loads(state_path.read_text())
    state[effect + "_lost_ack"] = True
    state_path.write_text(json.dumps(state))
    result = subprocess.run(
        [str(sandbox.hz_bin), "system", "daemon", "tick", "--json"],
        env=sandbox.env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == (1 if effect == "append" else 0), result.stdout + result.stderr
    state = json.loads(state_path.read_text())
    assert state["appends"] == 1
    assert state["shares"] == []
    if effect == "share":
        key = next(iter(state["drafts"]))
        state["drafts"][key] += "."
        state["revision"] += 1
        state_path.write_text(json.dumps(state))
        command = [str(sandbox.hz_bin), "heinzel", "approve-release-draft", session, key]
        failed = terminal(command, sandbox.env, expected=1)
        assert "uncertain" in failed
    state = json.loads(state_path.read_text())
    state[effect + "_lost_ack"] = False
    state_path.write_text(json.dumps(state))
    subprocess.run(
        [str(sandbox.hz_bin), "system", "daemon", "tick", "--json"],
        env=sandbox.env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if effect == "share":
        assert "already attempted" in terminal(command, sandbox.env, expected=1)
    restarted = json.loads(state_path.read_text())
    assert restarted["appends"] == 1
    assert len(restarted["shares"]) == (1 if effect == "share" else 0)
