"""A real doctor condition resolves through the mounted web notification stream."""

import json
import sys
import threading
import time
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import Check, Module, NoticeKind, Notifications, Settings, Status
from heinzel.heads import web
from heinzel.modules.system.doctor import doctor
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._watch import doctor_problems, watch
from tests.e2e._browser_result import require_browser, run_browser
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, REPO, web_build

pytestmark = pytest.mark.e2e


@pytest.mark.timeout(300)
def test_doctor_notice_resolves_in_mounted_browser(make_kernel, tmp_path, monkeypatch):  # noqa: PLR0915
    """The actual doctor and store drive a visible notice away after its check recovers."""
    web_build.ensure_built()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    (tmp_path / "home").mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    recovered = threading.Event()
    provider = Module("fixture", "Fictional health provider.")

    @provider.health
    def health() -> list[Check]:
        return [Check(name="network", status=Status.UP if recovered.is_set() else Status.DEGRADED, detail="fixture")]

    isolated_doctor = Module("doctor", "Run the real doctor over isolated fixture checks.")
    isolated_doctor.operation()(doctor)
    doctor_kernel = make_kernel(settings_=settings, system__doctor=isolated_doctor, fixture=provider)
    kernel = make_kernel(builtins=True, settings_=settings)
    service = NotifyService(settings, NotifySettings(working_hours=False), {})
    service.problems = lambda: doctor_problems(doctor_kernel)
    kernel.bind(NotifyService, service)
    kernel.bind(Notifications, service)
    assert watch(doctor_kernel, service, {NoticeKind.DOCTOR}) == 1
    notice = service.listing(50).notices[0]
    assert notice.resolve_when
    assert notice.resolve_when.checks_up == ["fixture: network"]
    assert service.emit(NoticeKind.DRAFT, "A newer notice", route="/", command="hz", key="fixture:newer", urgent=True)

    class RecordedServer(uvicorn.Server):
        instances: ClassVar[list[uvicorn.Server]] = []

        def __init__(self, config):
            super().__init__(config)
            self.instances.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordedServer)
    ready = threading.Event()
    urls: list[str] = []

    def announce(value):
        urls.append(value)
        ready.set()

    server = threading.Thread(target=lambda: web.serve(kernel, port=0, browser=False, announce=announce), daemon=True)
    server.start()
    marker = tmp_path / "notice-observed"
    resolved: list[int] = []

    def recover():
        deadline = time.monotonic() + 90
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if marker.exists():
            recovered.set()
            resolved.append(service.resolve())

    worker = threading.Thread(target=recover, daemon=True)
    worker.start()
    try:
        assert ready.wait(15)
        token = parse_qs(urlsplit(urls[0]).fragment).get("token", [""])[0]
        assert token
        run = require_browser(
            run_browser(
                [
                    sys.executable,
                    str(REPO / "scripts/web_build.py"),
                    "exec",
                    "node",
                    "e2e/doctor-notice-recovery.mjs",
                    urls[0],
                    str(marker),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=120,
            )
        )
        if run.returncode:
            pytest.fail((run.stderr + "\n" + run.stdout).replace(token, "<redacted-session>"))
        assert json.loads(run.stdout.strip().splitlines()[-1])["resolved"] is True
        worker.join(5)
        assert resolved == [1]
        assert [item.title for item in service.listing(50).notices] == ["A newer notice"]
        history = service.listing(50, resolved=True).notices
        assert next(item for item in history if item.id == notice.id).resolved == "fixture: network is up again"
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        server.join(15)
        worker.join(95)
        assert not server.is_alive()
