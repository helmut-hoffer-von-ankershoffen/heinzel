"""Fresh owned process using the same fake provider definition and durable owner store."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

import heinzel.kernel._ai as ai_module
import heinzel.kernel._session_store as store_module
import heinzel.modules.heinzel._autonomous_schedule as schedule_module
import heinzel.modules.heinzel._interactive_turn as interactive_module
import heinzel.modules.heinzel._service as service_module
import heinzel.modules.heinzel._session_crud as crud_module
import heinzel.modules.heinzel._session_repository as repository_module
from heinzel import Config, ConnectedItemIdentity, Kernel, ModuleRecord, Notifications, Settings
from heinzel.kernel import Origin, discover
from heinzel.modules.org import _discovery as org_discovery
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from tests.integration import scheduled_connected_read_test as fixture_module
from tests.unit.modules import user_chat_test

root = Path(sys.argv[1]).resolve()
session = sys.argv[2]
now = datetime.fromisoformat(sys.argv[3])


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return now if tz is not None else now.replace(tzinfo=None)


class Notices(Notifications):
    def emit_once(self, _kind, title, body, *, route, command, key):
        if (root / "block-notice").exists():
            fake_effect = """import pathlib,sys,time
p=pathlib.Path(sys.argv[1])
deadline=time.monotonic()+60
(p/'effect-ready').touch()
while not (p/'release-effect').exists() and time.monotonic()<deadline:
    time.sleep(.02)
if (p/'release-effect').exists():
    (p/'effect-written').touch()
"""
            subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", fake_effect, str(root)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            while not (root / "effect-ready").exists():
                time.sleep(0.02)
            (root / "notice-blocked").touch()
            while True:
                time.sleep(0.1)
        with (root / "notices.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"pid": os.getpid(), "title": title, "body": body, "key": key}) + "\n")
        return True


class ExistingClient:
    def post(self, *_args, **_kwargs):
        return self

    def json(self):
        return {"session_id": session}


for module in (
    ai_module,
    store_module,
    schedule_module,
    interactive_module,
    service_module,
    crud_module,
    repository_module,
):
    module.datetime = Clock
org_discovery.discover = org_discovery.Discovered
org_discovery._run = lambda _args: None
settings = (
    Config.empty()
    .settings(Settings, "hz")
    .model_copy(
        update={
            "data_dir": root / "data",
            "state_dir": root / "state",
            "cache_dir": root / "cache",
            "log_dir": root / "log",
        }
    )
)


def make_kernel(*, builtins, config, fictional):
    records = discover(plugins=False)[0] if builtins else []
    records.append(
        ModuleRecord(
            qualname="fictional",
            module=fictional,
            origin=Origin(distribution="heinzel-test", version="9.9.9", builtin=False),
        )
    )
    return Kernel(settings, records, config=config)


mp = pytest.MonkeyPatch()
mp.setattr(user_chat_test, "fake_claude", lambda *_args, **_kwargs: root / "fake-claude")
mp.setattr(fixture_module, "web_client", lambda *_args: ExistingClient())
_client, kernel, current, found, state = fixture_module._fixture(make_kernel, root, mp)
assert found == session
state["item_identity"] = ConnectedItemIdentity(collection=("events",), identity_field="id", revision_field="rev")
state["events"] = [{"id": "provider-item", "rev": "one"}]
kernel.bind(Notifications, Notices())
with current.local("daemon"):
    daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC)
    registered = [job for job in daemon._jobs() if job.id.startswith("heinzel:agent-" + session.lower())]
    daemon._jobs = lambda: registered
    daemon._service_manager_pid = lambda: None
    daemon._post_notices = lambda _log: None
    report = daemon.tick()
print(  # noqa: T201 - isolated child returns its fixed fake-fixture result
    json.dumps(
        {
            "pid": os.getpid(),
            "jobs": len(registered),
            "reads": state["reads"],
            "runs": [run.disposition for run in report.runs],
        }
    )
)
