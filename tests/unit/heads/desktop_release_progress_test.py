"""The desktop installer never goes quiet: progress lines, stalled servers, missing releases and the real path."""

import fcntl
import io
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

import pytest
from rich.console import Console

from heinzel.heads import _desktop_installation as installer
from heinzel.heads import _desktop_release as release
from heinzel.heads.cli._progress import PhaseReporter

pytestmark = pytest.mark.unit

TAG = "v1.2.3"


class _Notes:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def phase(self, description, total=None):
        self.lines.append(f"phase: {description}")
        return _NoAdvance()

    def note(self, text):
        self.lines.append(f"note: {text}")


class _NoAdvance:
    def __enter__(self):
        return lambda _amount: None

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def server(monkeypatch):
    """A local stand-in for api.github.com and github.com; `behaviour` decides each path's answer."""
    behaviour: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            answer = behaviour.get(self.path.split("?")[0], 404)
            if answer == "stall":
                time.sleep(2)
                return
            if isinstance(answer, int):
                self.send_response(answer)
                self.end_headers()
                return
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    class Opener:
        def open(self, request, timeout=None):
            url = request if isinstance(request, str) else request.full_url
            return urlopen(re.sub(r"^https://[^/]+", base, url), timeout=timeout)  # noqa: S310 - local test server

    monkeypatch.setattr(release, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(release.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(release, "RETRY_PAUSE", 0)
    yield behaviour
    httpd.shutdown()


RELEASE_PATH = f"/repos/{release.REPOSITORY}/releases/tags/{TAG}"


@pytest.mark.filterwarnings("ignore::ResourceWarning", "ignore::pytest.PytestUnraisableExceptionWarning")
def test_stalled_server_reports_progress_at_once_then_a_clear_error(server, tmp_path, monkeypatch):
    server[RELEASE_PATH] = "stall"
    monkeypatch.setattr(release, "API_TIMEOUT", 0.3)
    monkeypatch.setattr(release, "ATTEMPTS", 2)
    stream = io.StringIO()
    reporter = PhaseReporter(Console(file=stream, force_terminal=False, width=100))
    started = time.monotonic()
    with pytest.raises(release.HeinzelError, match=r"failed: no answer in time\. Check your network"):
        release.fetch_archive("1.2.3", tmp_path, reporter)
    assert time.monotonic() - started < 3
    lines = stream.getvalue().splitlines()
    assert lines[0] == f"Resolving release {TAG}..."
    assert any("retrying, attempt 2 of 2" in line for line in lines)
    assert not any("done" in line for line in lines)


@pytest.mark.filterwarnings("ignore::ResourceWarning", "ignore::pytest.PytestUnraisableExceptionWarning")
def test_missing_release_says_no_desktop_app_is_published(server, tmp_path):
    with pytest.raises(release.HeinzelError, match=r"No desktop app is published for hz 1\.2\.3 yet.*hz system update"):
        release.fetch_archive("1.2.3", tmp_path)


@pytest.mark.filterwarnings("ignore::ResourceWarning", "ignore::pytest.PytestUnraisableExceptionWarning")
def test_release_without_the_macos_asset_says_so(server, tmp_path):
    server[RELEASE_PATH] = {"tag_name": TAG, "draft": False, "prerelease": False, "assets": []}
    with pytest.raises(release.HeinzelError, match=r"No desktop app is published for hz 1\.2\.3 yet.*no macOS app"):
        release.fetch_archive("1.2.3", tmp_path)


@pytest.mark.filterwarnings("ignore::ResourceWarning", "ignore::pytest.PytestUnraisableExceptionWarning")
def test_rate_limit_is_explained_not_retried(server, tmp_path):
    server[RELEASE_PATH] = 403
    notes = _Notes()
    with pytest.raises(release.HeinzelError, match="limit for requests without a login"):
        release.fetch_archive("1.2.3", tmp_path, notes)
    assert not [line for line in notes.lines if line.startswith("note:")]


def _normalised(text: str) -> str:
    return re.sub(r"\(\d+\.\ds\)|: done \(\d+\.\ds\)", lambda m: "(T)" if m[0].startswith("(") else ": done (T)", text)


def test_plain_output_when_redirected_has_no_escape_codes():
    stream = io.StringIO()
    reporter = PhaseReporter(Console(file=stream, force_terminal=False, width=80))
    with reporter.phase("Verifying checksum"):
        pass
    with reporter.phase("Downloading the desktop app (2 MB)", 2_000_000) as advance:
        advance(2_000_000)
    reporter.note("Verified: /home/x/Applications/Heinzelmaennchen.app exists.")
    assert "\x1b" not in stream.getvalue()
    assert _normalised(stream.getvalue()).splitlines() == [
        "Verifying checksum...",
        "Verifying checksum: done (T)",
        "Downloading the desktop app (2 MB)...",
        "Downloading the desktop app (2 MB): done (T)",
        "Verified: /home/x/Applications/Heinzelmaennchen.app exists.",
    ]


def test_terminal_output_ends_each_phase_with_a_check_mark():
    stream = io.StringIO()
    reporter = PhaseReporter(Console(file=stream, force_terminal=True, width=100, color_system=None))
    with reporter.phase("Extracting the app"):
        pass
    assert (
        _normalised(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", stream.getvalue()))
        .rstrip()
        .endswith("✓ Extracting the app (T)")
    )


def test_download_progress_lines_are_throttled_when_redirected(monkeypatch):
    monkeypatch.setattr("heinzel.heads.cli._progress.PLAIN_INTERVAL", 0)
    stream = io.StringIO()
    reporter = PhaseReporter(Console(file=stream, force_terminal=False, width=80))
    with reporter.phase("Downloading", 4_000_000) as advance:
        advance(1_000_000)
    assert "  1.0 of 4.0 MB (25%)" in stream.getvalue()


def test_waiting_for_the_installation_lock_is_announced_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "LOCK_WAIT", 0.5)
    monkeypatch.setattr(installer, "_LOCK_NOTE_AFTER", 0.1)
    config = tmp_path / ".config" / "heinzel"
    config.mkdir(parents=True)
    notes = _Notes()
    with installer._open_lock(config / "desktop-installation.lock") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with (
            pytest.raises(installer.DesktopInstallationError, match="held the desktop lock"),
            installer._locked_config(tmp_path, notes),
        ):
            pass
    assert any("Waiting for another" in line for line in notes.lines)


def test_confirm_never_prompts_without_a_terminal():
    from heinzel import HeinzelError
    from heinzel.heads.cli._progress import confirm

    console = Console(file=io.StringIO(), force_terminal=False)
    assert confirm(console, "Remove it?", yes=True)
    with pytest.raises(HeinzelError, match="pass --yes to agree"):
        confirm(console, "Remove it?", yes=False)
