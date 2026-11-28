"""Exercise the real SwiftUI page stack and WebKit sign-in check with ephemeral loopback workspaces."""

import subprocess
import sys
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

import pytest

from heinzel.testing import scaled
from tests.fixtures.local_http import LoopbackThreadingHTTPServer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="Requires native SwiftUI and WebKit"),
]


def _handler(
    status: int, *, lifecycle_statuses: list[int] | None = None, requests: list[str] | None = None
) -> type[BaseHTTPRequestHandler]:
    class Fixture(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if requests is not None:
                requests.append(self.command)
            self.send_error(405)

        def do_GET(self) -> None:
            if requests is not None:
                requests.append(self.command)
            if status == 0:
                self.close_connection = True
                return
            if self.path == "/manifest.webmanifest":
                code = status if status in {403, 503} else 200
                mime, body = "application/manifest+json", rb'{"name":"Heinzelm\u00e4nnchen","short_name":"hz"}'
            elif self.path == "/":
                shell = b"" if status == 299 else b"<main data-testid='app-shell'><textarea></textarea></main>"
                code, mime, body = 200, "text/html", b"<div id='root'>" + shell + b"</div>"
            elif self.path == "/api/lifecycle" and self.headers.get("Authorization") == "Bearer fixture-value":
                code, mime, body = (
                    lifecycle_statuses.pop(0) if lifecycle_statuses else (200 if status == 299 else status),
                    "application/json",
                    b'{"instance":"fixture","version":"1"}',
                )
            else:
                code, mime, body = 401, "application/json", b"{}"
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.end_headers()
            self.wfile.write(body)

    return Fixture


@pytest.fixture(scope="module")
def switch_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    binary = tmp_path_factory.mktemp("workspace-switch") / "probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-parse-as-library",
            "apple/App/Endpoint.swift",
            "apple/App/BrowserNetwork.swift",
            "apple/App/WorkspaceSwitchPages.swift",
            "apple/tests/WorkspaceSwitchPagesTests.swift",
            "-o",
            str(binary),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        timeout=scaled(90),
    )
    return binary


def test_late_lifecycle_results_follow_the_current_document_authority(switch_probe: Path) -> None:
    with LoopbackThreadingHTTPServer(("127.0.0.1", 0), _handler(200)) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            subprocess.run(
                [str(switch_probe), "rotated-lifecycle", f"http://127.0.0.1:{server.server_port}"],
                check=True,
                timeout=scaled(30),
            )
        finally:
            server.shutdown()
            worker.join()


def test_authenticated_recovery_keeps_the_same_draft_route_and_does_not_repeat_writes(switch_probe: Path) -> None:
    outcomes = [200, 503, 503, 200]
    requests: list[str] = []
    with LoopbackThreadingHTTPServer(
        ("127.0.0.1", 0), _handler(200, lifecycle_statuses=outcomes, requests=requests)
    ) as server:
        Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            subprocess.run(
                [str(switch_probe), "recover-draft", f"http://127.0.0.1:{server.server_port}"],
                check=True,
                timeout=scaled(30),
            )
        finally:
            server.shutdown()
    assert not outcomes
    assert set(requests) == {"GET"}


def test_legacy_profile_menu_opens_the_native_chooser_without_duplicating_the_new_menu(switch_probe: Path) -> None:
    subprocess.run([str(switch_probe), "legacy-menu"], check=True, timeout=scaled(15))


def test_remote_monitor_confirms_loss_suspends_and_discards_late_results(switch_probe: Path) -> None:
    subprocess.run([str(switch_probe), "remote-monitor"], check=True, timeout=scaled(15))


@pytest.mark.parametrize("scenario", ["switch", "empty-shell", "first-open"])
def test_failed_switch_preserves_live_draft_and_success_reuses_verified_tab(switch_probe: Path, scenario: str) -> None:
    with ExitStack() as stack:
        origins = []
        for status in (200, 200, 401, 503, 0, 299, 200, 403):
            server = stack.enter_context(LoopbackThreadingHTTPServer(("127.0.0.1", 0), _handler(status)))
            Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
            stack.callback(server.shutdown)
            origins.append(f"http://127.0.0.1:{server.server_port}")
        subprocess.run([str(switch_probe), scenario, *origins], check=True, timeout=scaled(60))


def test_promoted_mac_tab_resends_unchanged_titlebar_geometry() -> None:
    root = Path(__file__).resolve().parents[2] / "apple/App"
    source = (root / "WorkspaceView.swift").read_text()
    drag = source.split('private static let windowDragScript = """', 1)[1].split('"""', 1)[0]
    promoted = (root / "Navigation.swift").read_text().split("await WorkspaceWebSession.verify", 1)[1]
    assert "windowChanged()" in promoted.split("} else if endpoint.", 1)[0]
    harness = """
        const assert = require("node:assert/strict");
        const events = {};
        let frames = [], deliveries = 0, selected = false;
        globalThis.window = globalThis;
        globalThis.webkit = {messageHandlers: {heinzelDrag: {postMessage: () => {
            deliveries++;
            return selected ? Promise.resolve() : Promise.reject(Error("Staged"));
        }}}};
        globalThis.document = {querySelectorAll: () => [],
            documentElement: {clientWidth: 800, hasAttribute: () => false},
            fonts: {ready: Promise.resolve()}};
        globalThis.ResizeObserver = globalThis.MutationObserver = class { observe() {} disconnect() {} };
        globalThis.addEventListener = (name, action) => { events[name] = action; };
        globalThis.requestAnimationFrame = action => frames.push(action);
        const draw = () => { const next = frames; frames = []; next.forEach(action => action()); };
    """
    probe = """
        draw();
        assert.equal(deliveries, 1);
        events.resize();
        draw();
        assert.equal(deliveries, 1);
        selected = true;
        events["heinzel-window-change"]();
        draw();
        assert.equal(deliveries, 2);
    """
    subprocess.run(["node", "-e", harness + drag + probe], check=True, timeout=scaled(10))
