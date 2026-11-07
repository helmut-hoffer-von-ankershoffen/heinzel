"""The desktop seal and quarantine rules, against a scripted codesign/xattr/spctl that never touches a real app."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from heinzel.heads import _desktop_installation as desktop
from heinzel.heads import _desktop_seal as seal

pytestmark = pytest.mark.unit


@dataclass
class FakeMac:
    """Plays codesign, xattr and spctl for one bundle path; records every call."""

    sealed: bool = True
    signable: bool = True
    quarantine: str | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, *command: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        tool, *args = command
        if tool == "/usr/bin/codesign" and args[0] == "--verify":
            ok = self.sealed
            err = "" if ok else "code has no resources but signature indicates they must be present"
            return subprocess.CompletedProcess(command, 0 if ok else 1, "", err)
        if tool == "/usr/bin/codesign":
            self.sealed = self.signable
            return subprocess.CompletedProcess(command, 0, "", "replacing existing signature")
        if tool == "/usr/bin/xattr" and args[0] == "-p":
            found = self.quarantine is not None
            return subprocess.CompletedProcess(command, 0 if found else 1, f"{self.quarantine}\n" if found else "", "")
        if tool == "/usr/bin/xattr" and args[0] == "-w":
            self.quarantine = args[2]
            return subprocess.CompletedProcess(command, 0, "", "")
        if tool == "/usr/bin/xattr" and args[0] == "-d":
            self.quarantine = None
            return subprocess.CompletedProcess(command, 0, "", "")
        if tool == "/usr/sbin/spctl":
            return subprocess.CompletedProcess(command, 3, "", f"{args[-1]}: rejected")
        raise AssertionError(command)


@pytest.fixture
def mac(monkeypatch):
    fake = FakeMac()
    monkeypatch.setattr(seal, "_run", fake.run)
    return fake


BUNDLE = Path("/nonexistent/Heinzelmaennchen.app")


def test_a_valid_seal_is_left_untouched(mac):
    assert not seal.ensure_sealed(BUNDLE)
    assert [call[1] for call in mac.calls] == ["--verify"]


def test_a_broken_seal_is_resealed_ad_hoc_and_verified_again(mac):
    mac.sealed = False
    assert seal.ensure_sealed(BUNDLE)
    assert mac.calls == [
        ("/usr/bin/codesign", "--verify", "--deep", "--strict", str(BUNDLE)),
        ("/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(BUNDLE)),
        ("/usr/bin/codesign", "--verify", "--deep", "--strict", str(BUNDLE)),
    ]


def test_a_seal_that_cannot_be_repaired_fails_closed_and_says_damaged(mac):
    mac.sealed = mac.signable = False
    with pytest.raises(seal.DesktopSealError, match=r"damaged.*no resources"):
        seal.ensure_sealed(BUNDLE)


@pytest.mark.parametrize(
    ("value", "state"),
    [(None, "none"), ("0081;00000000;Heinzel;", "ours"), ("0083;65f0c1a2;Safari;ABC", "other")],
)
def test_only_our_own_quarantine_mark_is_removed(mac, value, state):
    mac.quarantine = value
    assert seal.quarantine_state(BUNDLE) == state
    assert seal.clear_our_quarantine(BUNDLE) is (state == "ours")
    assert mac.quarantine == (None if state == "ours" else value)


def test_keep_quarantine_writes_the_heinzel_mark(mac):
    seal.mark_quarantined(BUNDLE)
    assert seal.quarantine_state(BUNDLE) == "ours"


@pytest.mark.parametrize(
    ("sealed", "quarantine", "opens", "words"),
    [
        (True, None, True, "double-click opens"),
        (True, "0081;00000000;Heinzel;", True, "Open Anyway"),
        (False, "0081;00000000;Heinzel;", False, "damaged"),
    ],
)
def test_the_report_says_in_plain_words_what_macos_will_do(mac, sealed, quarantine, opens, words):
    mac.sealed, mac.quarantine = sealed, quarantine
    report = seal.inspect_bundle(BUNDLE)
    assert report.opens is opens
    assert words in " ".join(report.lines())
    assert "rejected" in report.gatekeeper


@pytest.mark.parametrize("keep", [False, True])
def test_the_installer_seals_before_commit_and_quarantines_only_on_request(mac, keep):
    mac.sealed = False
    desktop._prepare_for_gatekeeper(BUNDLE, keep_quarantine=keep)
    assert mac.sealed
    assert seal.quarantine_state(BUNDLE) == ("ours" if keep else "none")


@pytest.fixture
def owned(monkeypatch, tmp_path):
    """An owned installation whose bundle is never created on disk, so nothing can be launched or registered."""
    monkeypatch.setattr(desktop, "_check_platform", lambda: None)
    monkeypatch.setattr(desktop, "SYSTEM_APPLICATIONS", tmp_path / "no-system-Applications")
    monkeypatch.setattr(desktop, "_owned_installation", lambda *_: object())
    monkeypatch.setattr(desktop, "_in_use", lambda _: False)
    monkeypatch.setattr(desktop, "_sync_tree", lambda _: None)
    return tmp_path


def test_doctor_reports_without_changing_anything(mac, owned):
    mac.sealed, mac.quarantine = False, "0081;00000000;Heinzel;"
    report, repairs = desktop.doctor_desktop(home=owned)
    assert report is not None
    assert not report.opens
    assert repairs == []
    assert report.quarantine == "ours"
    assert not any(call[1] in {"--force", "-d", "-w"} for call in mac.calls)


def test_doctor_fix_reseals_and_clears_only_our_mark(mac, owned):
    mac.sealed, mac.quarantine = False, "0081;00000000;Heinzel;"
    report, repairs = desktop.doctor_desktop(fix=True, home=owned)
    assert report is not None
    assert report.opens
    assert report.quarantine == "none"
    assert len(repairs) == 2


def test_doctor_fix_refuses_while_the_app_runs(mac, owned, monkeypatch):
    monkeypatch.setattr(desktop, "_in_use", lambda _: True)
    mac.sealed = False
    with pytest.raises(desktop.DesktopInstallationError, match="Quit"):
        desktop.doctor_desktop(fix=True, home=owned)
    assert not mac.sealed


def test_doctor_without_an_owned_app_reports_nothing(mac, owned, monkeypatch):
    monkeypatch.setattr(desktop, "_owned_installation", lambda *_: None)
    assert desktop.doctor_desktop(fix=True, home=owned) == (None, [])
    assert mac.calls == []


@pytest.mark.parametrize(("sealed", "code"), [(True, 0), (False, 1)])
def test_hz_app_doctor_prints_the_state_and_fails_when_macos_would_refuse(mac, owned, monkeypatch, sealed, code):
    import io

    import typer
    from rich.console import Console

    from heinzel.heads.cli import _app

    mac.sealed = sealed
    monkeypatch.setenv("HOME", str(owned))  # the command reads Path.home(); never the real profile
    out, err = io.StringIO(), io.StringIO()
    try:
        _app._doctor(Console(file=out, width=200), Console(file=err, width=200), fix=False)
        status = 0
    except typer.Exit as exit_:
        status = exit_.exit_code
    assert status == code
    assert "Signature:" in out.getvalue()
    assert ("doctor --fix" in err.getvalue()) is not sealed


@pytest.mark.parametrize(("sealed", "code"), [(True, 0), (False, 1)])
def test_hz_app_doctor_json_reports_the_same_facts(mac, owned, monkeypatch, sealed, code):
    import io
    import json

    import typer
    from rich.console import Console

    from heinzel.heads.cli import _app

    mac.sealed = sealed
    monkeypatch.setenv("HOME", str(owned))  # the command reads Path.home(); never the real profile
    out, err = io.StringIO(), io.StringIO()
    try:
        _app._doctor(Console(file=out, width=200), Console(file=err, width=200), fix=False, as_json=True)
        status = 0
    except typer.Exit as exit_:
        status = exit_.exit_code
    assert status == code
    state = json.loads(out.getvalue())
    assert state["installed"] is True
    assert state["opens"] is sealed
    assert (state["signature_problem"] is None) is sealed
    assert state["quarantine"] in {"none", "ours", "other"}
    assert state["repairs"] == []


@pytest.mark.parametrize(("keep", "words"), [(False, "double-click"), (True, "Open Anyway")])
def test_the_install_note_is_honest_about_trust(keep, words):
    from heinzel.heads import _desktop_copy

    note = _desktop_copy.trust(keep_quarantine=keep)
    assert "not notarized" in note
    assert "sha256" in note
    assert words in note
