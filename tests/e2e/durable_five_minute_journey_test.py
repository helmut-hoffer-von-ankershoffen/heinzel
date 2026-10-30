"""Mounted refusal of an unverified runner, explicit fixed check-in and isolated daemon restart."""

import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import AgentResult, Budget, CurrentUser, Module, Settings, ToolProfile
from heinzel.heads import web
from heinzel.kernel import Config, Kernel
from heinzel.modules.system.daemon._models import DaemonSettings, Disposition
from heinzel.modules.system.daemon._service import DaemonService
from heinzel.modules.system.notify._models import NotifySettings
from heinzel.modules.system.notify._native import Native
from heinzel.modules.system.notify._service import NotifyService

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
HOME = Path.home()
BROWSER_CACHE = HOME / ("Library/Caches/ms-playwright" if sys.platform == "darwin" else ".cache/ms-playwright")
spec = importlib.util.spec_from_file_location("web_build", REPO / "scripts" / "web_build.py")
assert spec
assert spec.loader
web_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_build)


def wake_after_now() -> datetime:
    """Select the minute just after the next UTC five-minute boundary, after real schedule activation time."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now + timedelta(minutes=5 - now.minute % 5 + 1)


class _Script:
    calls = 0


def _fake_runner(script: _Script) -> Module:
    module = Module("journeyfake", "Unverified runner that must not receive the mounted personal chat.")

    class FakeRunner:
        def run(
            self,
            prompt: str,
            *,
            contract: str,
            tools: ToolProfile,
            timeout: timedelta,
            budget: Budget,
            **choice: object,
        ) -> AgentResult:
            del prompt, contract, tools, timeout, budget, choice
            script.calls += 1
            msg = "An unverified runner must not receive personal chat content"
            raise AssertionError(msg)

    module.provides("agent-runner", modes={"read"}, name="journey-fake")(FakeRunner)
    return module


def _kernel(
    make_kernel: Callable[..., Kernel],
    tmp_path: Path,
    clock: list[datetime],
    native_calls: list[list[str]],
    script: _Script,
) -> tuple[Kernel, CurrentUser, NotifyService, DaemonService, Settings]:
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    config = Config(files=(), data={"capabilities": {"agent_runner": "journey-fake"}})
    kernel = make_kernel(
        builtins=True,
        settings_=settings,
        config=config,
        journeyfake=_fake_runner(script),
    )
    current = CurrentUser(tmp_path / "state" / "identity")
    kernel.bind(CurrentUser, current)
    notify_settings = NotifySettings(working_hours=False, native=True, repeat_hours=0)

    def native_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        native_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    notices = NotifyService(
        settings,
        notify_settings,
        {},
        clock=lambda: clock[0],
        notifier=Native(name="notify-send", executable="/isolated/fake-notify-send", runner=native_runner),
    )
    kernel.bind(NotifyService, notices)
    from heinzel import Notifications

    kernel.bind(Notifications, notices)
    daemon = DaemonService(kernel, settings, DaemonSettings(), platform="isolated", clock=lambda: clock[0], zone=UTC)
    kernel.bind(DaemonService, daemon)
    return kernel, current, notices, daemon, settings


@pytest.mark.timeout(600)
def test_mounted_browser_approval_wake_and_restart_deduplicate(  # noqa: PLR0915
    make_kernel: Callable[..., Kernel], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web_build.ensure_built()
    subprocess.run(
        [sys.executable, str(REPO / "scripts/web_build.py"), "exec", "playwright-core", "install", "chromium"],
        cwd=REPO,
        check=True,
        timeout=600,
        capture_output=True,
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    home = tmp_path / "home"
    home.mkdir()
    clock = [datetime.now(UTC)]
    native_calls: list[list[str]] = []
    script = _Script()
    kernel, current, notices, daemon, settings = _kernel(make_kernel, tmp_path, clock, native_calls, script)

    class RecordedServer(uvicorn.Server):
        instances: ClassVar[list[uvicorn.Server]] = []

        def __init__(self, config: uvicorn.Config) -> None:
            super().__init__(config)
            self.instances.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordedServer)
    ready = threading.Event()
    urls: list[str] = []

    def announce(value: str) -> None:
        urls.append(value)
        ready.set()

    def serve() -> None:
        web.serve(kernel, port=0, browser=False, announce=announce)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert ready.wait(15), "isolated mounted web head did not start"
        url = urls[0]
        parsed = urlsplit(url)
        token = parse_qs(parsed.fragment).get("token", [""])[0]
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port
        assert token
        driven = subprocess.run(
            [sys.executable, str(REPO / "scripts/web_build.py"), "exec", "node", "e2e/durable-five-minute.mjs", url],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=240,
        )
        if driven.returncode:
            pytest.fail((driven.stderr + "\n" + driven.stdout).replace(token, "<redacted-session>"))
        result = json.loads(driven.stdout.strip().splitlines()[-1])
        assert result == {"browser": "approved", "session": result["session"]}
        session = result["session"]
        assert notices.recent() == []
        assert native_calls == []
        assert script.calls == 0
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        assert not thread.is_alive(), "isolated web head did not stop"

    # Browser setup and approval can cross the initially chosen slot; wake after the actual activation.
    clock[0] = wake_after_now()
    job_id = f"heinzel:check-in-{session.lower()}"
    with current.local("daemon"):
        first = daemon.tick()
    run = next(item for item in first.runs if item.job == job_id)
    assert run.disposition is Disposition.RENDERED, run
    visible = notices.recent()
    assert len(visible) == 1
    notice_key = visible[0].key
    expected_slot = clock[0].replace(minute=(clock[0].minute // 5) * 5, second=0, microsecond=0)
    assert notice_key.endswith(expected_slot.isoformat())
    assert len(native_calls) == 1

    # A fresh kernel and NotifyService simulate a daemon process restart over the same owner journal and notice store.
    restarted_script = _Script()
    restarted, restarted_current, restarted_notices, restarted_daemon, restarted_settings = _kernel(
        make_kernel, tmp_path, clock, native_calls, restarted_script
    )
    assert restarted is not kernel
    assert restarted_settings.state_dir == settings.state_dir
    with restarted_current.local("daemon"):
        replay = restarted_daemon.tick()
    assert all(item.job != job_id for item in replay.runs)
    assert [notice.key for notice in restarted_notices.recent()] == [notice_key]
    assert len(native_calls) == 1
