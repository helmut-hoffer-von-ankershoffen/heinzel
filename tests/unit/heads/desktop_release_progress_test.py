"""The desktop installer never goes quiet: progress lines, stalled servers, missing releases and the real path."""

import fcntl
import io
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.request import urlopen

import pytest
from rich.console import Console

from heinzel.heads import _desktop_installation as installer
from heinzel.heads import _desktop_messages as copy
from heinzel.heads import _desktop_release as release
from heinzel.heads.cli._progress import StepReporter
from tests.fixtures.local_http import LoopbackThreadingHTTPServer

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

    httpd = LoopbackThreadingHTTPServer(("127.0.0.1", 0), Handler)
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
    httpd.server_close()  # an open listening socket warns when collected, inside whichever test runs then


RELEASE_PATH = f"/repos/{release.REPOSITORY}/releases/tags/{TAG}"


@pytest.mark.filterwarnings("ignore::ResourceWarning", "ignore::pytest.PytestUnraisableExceptionWarning")
def test_stalled_server_reports_progress_at_once_then_a_clear_error(server, tmp_path, monkeypatch):
    server[RELEASE_PATH] = "stall"
    monkeypatch.setattr(release, "API_TIMEOUT", 0.3)
    monkeypatch.setattr(release, "ATTEMPTS", 2)
    stream = io.StringIO()
    reporter = _steps(Console(file=stream, force_terminal=False, width=100))
    started = time.monotonic()
    with pytest.raises(release.HeinzelError, match=r"failed: no answer in time\. Check your network"):
        release.fetch_archive("1.2.3", tmp_path, reporter)
    assert time.monotonic() - started < 3
    lines = stream.getvalue().splitlines()
    assert lines[0] == "  Downloading"
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


def _steps(console: Console, header: str | None = None) -> StepReporter:
    return StepReporter(console, header=header, rows=copy.install_rows(keep_quarantine=False))


def _install_steps(reporter: StepReporter, clock: list[float] | None = None) -> None:
    for step in (copy.RESOLVE.format(tag=TAG), copy.FETCH_MANIFEST):
        with reporter.phase(step):
            pass
    with reporter.phase(copy.DOWNLOAD.format(megabytes=2.0), 2_000_000) as advance:
        if clock is not None:
            clock[0] += 3
        advance(2_000_000)
    for step in (copy.VERIFY_CHECKSUM, copy.EXTRACT, copy.CHECK_SEAL, copy.WRITE_RECEIPT):
        with reporter.phase(step):
            pass
    with reporter.phase(copy.install_into(installer.SYSTEM_APPLICATIONS)):
        pass
    with reporter.phase(copy.VERIFY_INSTALLED):
        pass
    reporter.close(complete=True)


ROWS = [
    "  ok Verified   checksum and build provenance match the GitHub release",
    "  ok Sealed     local code seal; no quarantine, so it opens with a double-click",
    "  ok Installed  /Applications/Heinzelmaennchen.app",
    "  ok Recorded   hz owns this copy; hz app doctor checks it, hz app uninstall removes it",
]


def test_plain_output_when_redirected_is_one_line_per_row_without_escape_codes():
    stream = io.StringIO()
    clock = [0.0]
    reporter = _steps(Console(file=stream, force_terminal=False, width=200, get_time=lambda: clock[0]), "Installing")
    _install_steps(reporter, clock)
    assert "\x1b" not in stream.getvalue()
    assert stream.getvalue().splitlines() == ["Installing", "  Downloading 2.0 MB ... done (3 s)", *ROWS]


def test_no_color_or_ci_gives_the_plain_rows_on_a_terminal(monkeypatch):
    monkeypatch.setenv("CI", "true")
    stream = io.StringIO()
    reporter = _steps(Console(file=stream, force_terminal=True, width=200, color_system=None), "Installing")
    _install_steps(reporter)
    assert stream.getvalue().splitlines()[2:] == ROWS


def test_terminal_output_has_the_hat_a_bar_and_check_marks(monkeypatch):
    for name in ("CI", "NO_COLOR"):
        monkeypatch.delenv(name, raising=False)
    stream = io.StringIO()
    clock = [0.0]
    console = Console(
        file=stream,
        force_terminal=True,
        force_interactive=False,
        width=200,
        color_system=None,
        get_time=lambda: clock[0],
    )
    _install_steps(_steps(console, "Installing"), clock)
    lines = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", stream.getvalue()).splitlines()
    assert lines[0] == "▀ Installing"
    assert lines[1] == f"  Downloading  {'━' * 40}  2.0/2.0 MB  0:00:03"
    assert lines[2:] == [row.replace(" ok ", " ✓ ") for row in ROWS]


def test_a_live_terminal_ends_with_the_same_rows(monkeypatch):
    for name in ("CI", "NO_COLOR"):
        monkeypatch.delenv(name, raising=False)
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, force_interactive=True, width=200, color_system=None)
    _install_steps(_steps(console, "Installing"))
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", stream.getvalue())
    assert text.rstrip().endswith(ROWS[-1].replace(" ok ", " ✓ "))
    assert "2.0/2.0 MB" in text


def test_a_note_during_the_download_keeps_the_download_line_whole():
    stream = io.StringIO()
    reporter = _steps(Console(file=stream, force_terminal=False, width=200))
    with reporter.phase(copy.RESOLVE.format(tag=TAG)):
        reporter.note("Asking GitHub failed; retrying, attempt 2 of 3.")
    with reporter.phase(copy.DOWNLOAD.format(megabytes=2.0), 2_000_000) as advance:
        advance(2_000_000)
    reporter.close(complete=False)
    assert stream.getvalue().splitlines() == ["  Downloading", "Asking GitHub failed; retrying, attempt 2 of 3."]
    with reporter.phase(copy.CHECK_SEAL):
        pass
    reporter.close(complete=True)
    assert stream.getvalue().splitlines()[-1] == ROWS[1]


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


def test_transient_failures_are_retried_then_succeed():
    from urllib.error import HTTPError, URLError

    outcomes = iter([URLError(ConnectionResetError("reset")), HTTPError("u", 503, "busy", None, None), "ok"])  # pyright: ignore[reportArgumentType]

    def action():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    notes = _Notes()
    assert release._retrying(action, "Asking GitHub", notes) == "ok"
    assert [line.count("retrying") for line in notes.lines] == [1, 1]


def test_verification_failure_and_timeout_stay_fatal(tmp_path, monkeypatch):
    import subprocess

    core = release._CoreManifest.model_construct(
        version="1.2.3",
        commit="a" * 40,
        producer=release._Producer.model_construct(run_id=1, attempt=1, source_ref="refs/heads/main", workflow="w"),
    )
    failed = subprocess.CompletedProcess([], 1, b"", b"HTTP 401: not logged in\n")
    monkeypatch.setattr(release, "_run_gh", lambda *_args: failed)
    with pytest.raises(release.HeinzelError, match=r"could not be verified\. GitHub CLI said: HTTP 401"):
        release._verify("gh", tmp_path / "a", tmp_path / "p", core)

    def stalled(*_args):
        command = "gh"
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr(release, "_run_gh", stalled)
    with pytest.raises(release.HeinzelError, match="did not finish verifying"):
        release._verify("gh", tmp_path / "a", tmp_path / "p", core)


def test_install_names_the_folder_the_installer_reports_not_a_fixed_one(monkeypatch, tmp_path):
    from pathlib import Path

    from heinzel.heads import _desktop_installation as installation
    from heinzel.heads.cli import _app

    system = Path("/Applications/Heinzelmaennchen.app")
    monkeypatch.setattr(installation, "desktop_installed", lambda home=None: None)
    monkeypatch.setattr(installation, "desktop_location", lambda home=None: system)
    monkeypatch.setattr(installation, "system_applications_usable", lambda: True)
    monkeypatch.setattr(installation, "install_desktop", lambda *_args, **_options: "installed")
    monkeypatch.setattr(_app, "desktop_executable", lambda _kernel: tmp_path / "hz")
    out, err = io.StringIO(), io.StringIO()
    _app._install_app(None, Console(file=out, width=200), Console(file=err, width=200))
    assert f"Done. Open it from Launchpad, or run: open {system}" in out.getvalue()
    assert "It stayed in" not in err.getvalue()


def test_install_that_could_not_move_the_app_says_where_it_stayed_and_why(monkeypatch, tmp_path):
    from heinzel.heads import _desktop_installation as installation
    from heinzel.heads.cli import _app

    own = tmp_path / "Applications" / "Heinzelmaennchen.app"
    monkeypatch.setattr(installation, "desktop_installed", lambda home=None: None)
    monkeypatch.setattr(installation, "desktop_location", lambda home=None: own)
    monkeypatch.setattr(installation, "system_applications_usable", lambda: True)
    monkeypatch.setattr(installation, "install_desktop", lambda *_args, **_options: "installed")
    monkeypatch.setattr(_app, "desktop_executable", lambda _kernel: tmp_path / "hz")
    out, err = io.StringIO(), io.StringIO()
    _app._install_app(None, Console(file=out, width=400), Console(file=err, width=400))
    assert f"It stayed in {own.parent} (your Applications folder)" in err.getvalue()
    assert f"Done. Open it from Launchpad, or run: open {own}" in out.getvalue()
