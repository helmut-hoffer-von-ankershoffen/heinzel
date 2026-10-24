import threading
import time

import pytest
from rich.console import Console

from heinzel import Kernel, Settings, __version__
from heinzel.heads.cli import _update
from heinzel.kernel import ReleaseCache, ReleaseSource, discover

pytestmark = pytest.mark.unit


class Source(ReleaseSource):
    def __init__(self, answer="9.0.0", delay=0.0):
        self.answer, self.delay = answer, delay
        self.asked = threading.Event()

    def latest_version(self, timeout):
        self.asked.set()
        time.sleep(self.delay)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture(autouse=True)
def _no_ci(monkeypatch):
    for variable in _update.CI_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def kernel(source=None, *, update_check=True):
    kernel = Kernel(Settings(update_check=update_check))
    if source is not None:
        kernel.bind(ReleaseSource, source)
    return kernel


def cache_of(kernel):
    return ReleaseCache(kernel.settings.cache_dir)


def test_a_check_asks_and_caches():
    hz = kernel(source := Source())
    check = _update.start_check(hz)
    assert check is not None
    check.thread.join(5)
    assert source.asked.is_set()
    assert cache_of(hz).latest == "9.0.0"


def test_a_fresh_cache_is_not_asked_again():
    hz = kernel(source := Source())
    cache_of(hz).remember("8.0.0")
    assert _update.start_check(hz) is None
    assert not source.asked.is_set()


def test_a_recent_failure_is_not_asked_again():
    hz = kernel(source := Source())
    cache_of(hz).stamp(error="OSError: offline")
    assert _update.start_check(hz) is None
    assert not source.asked.is_set()


def test_the_cache_is_stamped_before_asking_so_a_check_cut_short_still_waits():
    hz = kernel(Source(delay=0.5))
    check = _update.start_check(hz)
    assert check is not None
    assert cache_of(hz).is_fresh(_update.MAX_AGE)  # stamped while the source is still thinking
    assert _update.start_check(hz) is None


def test_a_slow_source_never_holds_the_command_longer_than_the_budget(monkeypatch):
    monkeypatch.setattr(_update, "BUDGET", 0.1)
    hz = kernel(Source(delay=2))
    check = _update.start_check(hz)
    assert check is not None
    started = time.monotonic()
    check.wait()
    assert time.monotonic() - started < 1
    assert check.thread.is_alive()  # abandoned: a daemon thread dies with the command


def test_a_source_that_fails_records_why():
    hz = kernel(Source(OSError("no route to host")))
    check = _update.start_check(hz)
    assert check is not None
    check.thread.join(5)
    assert cache_of(hz).error == "OSError: no route to host"


@pytest.mark.parametrize("ci", ["CI", "GITHUB_ACTIONS", "TF_BUILD"])
def test_no_check_in_ci(monkeypatch, ci):
    monkeypatch.setenv(ci, "true")
    assert _update.start_check(kernel(Source())) is None


def test_no_check_when_the_setting_is_off():
    assert _update.start_check(kernel(Source(), update_check=False)) is None


def test_no_check_without_a_release_source():
    hz = kernel()
    assert _update.start_check(hz) is None
    assert cache_of(hz).checked_at is None


@pytest.mark.parametrize(("latest", "expected"), [("9.0.0", True), (__version__, False), (None, False)])
def test_the_notice_comes_from_the_cache(latest, expected):
    hz = kernel()
    if latest is not None:
        cache_of(hz).remember(latest)
    text = _update.notice(hz)
    assert (
        text == f"hz 9.0.0 is available (installed {__version__}): hz system update\n{_update.WHATS_NEW}"
    ) is expected
    assert (text is None) is not expected


def test_the_notice_is_printed_only_to_a_terminal(monkeypatch):
    hz = kernel()
    cache_of(hz).remember("9.0.0")
    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        _update.print_notice(hz, console)
    assert capture.get() == ""
    monkeypatch.setattr(_update, "is_terminal", lambda console: True)
    with console.capture() as capture:
        _update.print_notice(hz, console)
    assert (
        capture.get().strip()
        == f"hz 9.0.0 is available (installed {__version__}): hz system update\n{_update.WHATS_NEW}"
    )


def test_is_terminal_asks_the_console():
    assert _update.is_terminal(Console(force_terminal=True))
    assert not _update.is_terminal(Console(force_terminal=False))


def test_the_changelog_header_names_both_versions_and_what_shows_whats_new(monkeypatch):
    hz = Kernel(Settings(update_check=True), discover(plugins=False)[0])
    assert _update.changelog_header(hz) is None
    cache_of(hz).remember("9.0.0")
    assert (
        _update.changelog_header(hz)
        == f"You're on hz {__version__}; hz 9.0.0 is available: hz system update. {_update.WHATS_NEW}"
    )
    console = Console(width=300, force_terminal=False)
    with console.capture() as capture:
        _update.print_changelog_header(hz, console, "# Changelog")
    assert capture.get() == ""
    monkeypatch.setattr(_update, "is_terminal", lambda console: True)
    with console.capture() as capture:
        _update.print_changelog_header(hz, console, [])  # `--newer` shows the newer releases themselves
    assert capture.get() == ""
    with console.capture() as capture:
        _update.print_changelog_header(hz, console, "# Changelog")
    assert capture.get().startswith("You're on hz")
