import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from heinzel import Module, __version__
from heinzel.kernel import CorePin, DaemonAttestation, HeinzelError, Installation, Progress
from heinzel.modules.system import _about, render_about
from heinzel.modules.system._about import (
    about_report,
    installed_desktop,
    pinned_label,
    served_web_build,
    web_build_of,
)

pytestmark = pytest.mark.unit

# The same page and id as web/src/updates/webBuild.test.ts: the page and `hz system about` agree.
PAGE = """<!doctype html><html><head>
  <script>paintSplash()</script>
  <script type="module" src="/assets/index-abc.js"></script>
  <link rel="modulepreload" href="/assets/vendor-123.js">
  <link rel="stylesheet" href="/assets/index-abc.css">
  <link rel="stylesheet" href="https://elsewhere.example/assets/x.css">
  <script src="/plugin-sdk/v1/react.js"></script>
</head><body></body></html>"""
BUILD = "d24e6866a6e1"


def test_the_web_build_is_the_hash_of_the_pages_hashed_entrypoints():
    assert web_build_of(PAGE) == BUILD
    assert web_build_of(PAGE.replace("index-abc.js", "index-def.js")) != BUILD
    assert web_build_of("<p>no app</p>") is None


def test_the_served_web_build_is_read_from_the_installed_index(tmp_path):
    index = tmp_path / "index.html"
    assert served_web_build(index) is None  # never built
    index.write_text(PAGE)
    assert served_web_build(index) == BUILD
    index.write_bytes(b"\xff\xfe")
    assert served_web_build(index) is None
    link = tmp_path / "link.html"
    link.symlink_to(tmp_path / "elsewhere.html")
    (tmp_path / "elsewhere.html").write_text(PAGE)
    assert served_web_build(link) is None  # never follows a link out of the installation
    index.write_text(" " * (_about.MAX_INDEX_BYTES + 1))
    assert served_web_build(index) is None


def test_the_desktop_app_is_named_by_its_receipt_on_a_mac_only(tmp_path, monkeypatch):
    receipt = tmp_path / _about.DESKTOP_RECEIPT
    monkeypatch.setattr(_about.platform, "system", lambda: "Darwin")
    assert installed_desktop(tmp_path) is None
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"release": "0.9.41", "executable": "/x"}))
    assert installed_desktop(tmp_path) == "0.9.41"
    for broken in ("[]", "not json", json.dumps({"release": 7}), json.dumps({"release": ""})):
        receipt.write_text(broken)
        assert installed_desktop(tmp_path) is None
    receipt.write_text(" " * (_about.MAX_RECEIPT_BYTES + 1))
    assert installed_desktop(tmp_path) is None
    receipt.write_text(json.dumps({"release": "0.9.41"}))
    monkeypatch.setattr(_about.platform, "system", lambda: "Linux")
    assert installed_desktop(tmp_path) is None


@pytest.mark.parametrize(
    ("kind", "ref", "label"),
    [
        ("release", "v1.2.0", "v1.2.0"),
        ("commit", "0123456789abcdef0123", "commit 0123456789ab"),
        ("local", "/home/someone/src/plugin", "local folder"),
        ("url", "https://example.org/x.whl", "file or URL"),
        ("index", "==1.2.0", "==1.2.0"),
        ("index", ">=1.0", None),
        ("index", None, None),
        ("ref", "main", None),
        ("default", None, None),
    ],
)
def test_a_pin_is_named_in_a_few_words_without_a_local_path(kind, ref, label):
    assert pinned_label(CorePin(kind, "heinzel-x", ref, "heinzel-x")) == label


class FakeInstallation(Installation):
    def __init__(self, pins: list[CorePin] | Exception, *, tool: bool = True) -> None:
        super().__init__(Progress())
        self._pins, self._tool = pins, tool

    @property
    def is_tool(self) -> bool:
        return self._tool

    def plugin_pins(self) -> list[CorePin]:
        if isinstance(self._pins, Exception):
            raise self._pins
        return self._pins


def _status(**fields):
    defaults = {"installed": True, "loaded": True, "paused": False, "version": None, "installed_version": None}
    return SimpleNamespace(**{**defaults, "version_mismatch": False, **fields})


def _kernel(make_kernel, status=None):
    kernel = make_kernel(good=Module(name="good", summary="Good."), other=Module(name="other", summary="Other."))
    if status is not None:
        kernel.bind(DaemonAttestation, DaemonAttestation(status=status))
    return kernel


@pytest.mark.parametrize(
    ("fields", "state"),
    [
        ({"installed": False, "loaded": False}, "not installed"),
        ({"loaded": False}, "stopped"),
        ({"paused": True}, "paused"),
        ({}, "running"),
    ],
)
def test_the_daemon_state_follows_its_status(make_kernel, tmp_path, fields, state):
    report = about_report(_kernel(make_kernel, lambda: _status(**fields)), FakeInstallation([]), tmp_path)
    assert report.daemon.state == state
    assert f"daemon {state}" in report.diagnostics


def test_the_daemon_names_the_hz_it_ran_and_whether_it_waits_for_a_restart(make_kernel, tmp_path):
    status = _status(version="0.9.1", installed_version="0.9.2", version_mismatch=True)
    report = about_report(_kernel(make_kernel, lambda: status), FakeInstallation([]), tmp_path)
    assert (report.daemon.version, report.daemon.outdated) == ("0.9.1", True)
    assert "daemon running hz 0.9.1 (needs a restart)" in report.diagnostics
    status = _status(installed_version="0.9.2")
    report = about_report(_kernel(make_kernel, lambda: status), FakeInstallation([]), tmp_path)
    assert (report.daemon.version, report.daemon.outdated) == ("0.9.2", False)


def test_an_unknown_daemon_never_fails_the_report(make_kernel, tmp_path, caplog):
    assert about_report(_kernel(make_kernel), FakeInstallation([]), tmp_path).daemon.state == "unknown"

    def broken():
        msg = "launchctl went away"
        raise HeinzelError(msg)

    with caplog.at_level("INFO", logger="heinzel.modules.system._about"):
        report = about_report(_kernel(make_kernel, broken), FakeInstallation([]), tmp_path)
    assert report.daemon.state == "unknown"
    assert any(getattr(record, "event", "") == "system.about.daemon" for record in caplog.records)
    assert "launchctl" not in caplog.text


def test_the_report_lists_each_plugin_once_with_its_pin_and_prints_the_same_lines(make_kernel, tmp_path):
    pins = [CorePin("release", "heinzel-test @ git+https://x/y@v1.0.0", "v1.0.0", "Heinzel_Test[extra]")]
    report = about_report(_kernel(make_kernel), FakeInstallation(pins, tool=False), tmp_path)
    assert report.version == __version__
    assert report.installation == "environment"
    assert report.channel == "stable"
    assert [(plugin.name, plugin.pinned) for plugin in report.plugins] == [("heinzel-test", "v1.0.0")]
    plugin = report.plugins[0]
    assert report.diagnostics[-1] == f"plugin heinzel-test {plugin.version} from {plugin.source}, pinned to v1.0.0"
    assert report.diagnostics[3] == "desktop app not installed"
    assert str(render_about(report)) == "\n".join(report.diagnostics)


def test_an_unreadable_receipt_pins_nothing_and_a_followed_plugin_says_so(make_kernel, tmp_path):
    report = about_report(_kernel(make_kernel), FakeInstallation(ValueError("bad receipt")), tmp_path)
    assert report.plugins[0].pinned is None
    assert report.diagnostics[-1].endswith(", follows updates")


def test_the_cli_shows_terminal_controls_in_a_plugin_name_escaped(make_kernel, tmp_path):
    report = about_report(_kernel(make_kernel), FakeInstallation([]), tmp_path)
    report.diagnostics = ["plugin evil\x1b[2J 1.0", "second"]
    assert str(render_about(report)) == "plugin evil\\x1b[2J 1.0\nsecond"


def test_the_first_line_dates_the_install_when_known(make_kernel, tmp_path, monkeypatch):
    day = datetime(2026, 11, 8, tzinfo=UTC).date()
    monkeypatch.setattr(_about, "_installed_on", lambda name: day)
    monkeypatch.setattr(_about, "served_web_build", lambda: BUILD)
    report = about_report(_kernel(make_kernel), FakeInstallation([]), tmp_path)
    assert report.diagnostics[:3] == [
        f"hz {__version__} (installed 2026-11-08)",
        f"install uv tool, from {report.source}, channel stable",
        f"web build {BUILD}",
    ]
