"""The manual desktop head accepts only an exact stable generation and reports what is on disk."""

import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError
from heinzel.heads._desktop_installation import DesktopBindingMismatchError
from heinzel.heads.cli import _desktop_update as desktop
from heinzel.kernel import CorePin, DaemonInstallationLifecycle, HeadRefresh, Installation, ProcessOutcome

pytestmark = pytest.mark.unit
TARGET = "0.9.37"
GENERATION = "a" * 64
COMMIT = "b" * 40
OFFICIAL = f"https://github.com/{desktop.REPOSITORY}"


@pytest.mark.parametrize(
    ("pin", "target", "allowed"),
    [
        (CorePin("index", "heinzel"), TARGET, False),
        (CorePin("index", "heinzel"), "0.9.37.dev1", False),
        (CorePin("release", "heinzel", f"v{TARGET}", git=OFFICIAL), TARGET, True),
        (CorePin("release", "heinzel", f"v{TARGET}", git=OFFICIAL + ".git"), TARGET, True),
        (CorePin("release", "heinzel", "v0.9.36", git=OFFICIAL), TARGET, False),
        (CorePin("release", "heinzel", f"v{TARGET}", git=OFFICIAL, subdirectory="core"), TARGET, False),
        (CorePin("release", "heinzel", f"v{TARGET}", git="https://example.org/heinzel"), TARGET, False),
        (CorePin("release", "heinzel", f"v{TARGET}", git="https://github.com/example/heinzel"), TARGET, False),
        (CorePin("ref", "heinzel", "main", git=OFFICIAL), TARGET, False),
        (CorePin("default", "heinzel", git=OFFICIAL), TARGET, False),
        (CorePin("local", "heinzel", "/example/heinzel"), TARGET, False),
        (CorePin("url", "heinzel", "https://example.org/heinzel.whl"), TARGET, False),
    ],
)
def test_only_an_exact_official_stable_git_tag_is_eligible(pin, target, allowed):
    installation = SimpleNamespace(core_pin=lambda: pin)
    assert (desktop._eligible_pin(installation, target) is pin) == allowed


def test_new_child_receives_the_receipt_owned_entrypoint_and_exact_generation(monkeypatch, tmp_path):
    executable = tmp_path / "hz"
    installation = SimpleNamespace(startup_fingerprint=lambda: GENERATION, tool_entrypoints=lambda: (executable,))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "untrusted"))

    def run(command, **options):
        assert command == [str(executable), "--_refresh-managed-desktop", TARGET, GENERATION]
        assert options["input"] is None
        assert options["timeout"] == desktop.DESKTOP_UPDATE_SECONDS
        assert not {"PYTHONPATH", "PYTHONHOME"} & options["env"].keys()
        result = HeadRefresh(head="desktop", status="on-disk", detail="Updated on disk.")
        return ProcessOutcome(0, result.model_dump_json(), "child diagnostic")

    monkeypatch.setattr(desktop, "run_bounded_process", run)
    assert desktop._launch(installation, TARGET, GENERATION).status == "on-disk"
    with pytest.raises(HeinzelError, match="Core installation or its `hz` entry point changed"):
        desktop._launch(installation, TARGET, "f" * 64)


def test_a_timed_out_child_reports_a_desktop_failure_with_retry(monkeypatch, tmp_path):
    executable = tmp_path / "hz"
    installation = SimpleNamespace(startup_fingerprint=lambda: GENERATION, tool_entrypoints=lambda: (executable,))
    monkeypatch.setattr(desktop, "run_bounded_process", lambda *_args, **_kwargs: ProcessOutcome(None, "", ""))
    result = desktop._launch(installation, TARGET, GENERATION)
    assert result.status == "failed"
    assert result.version is None
    assert "timed out" in result.detail
    assert "hz system update" in result.detail


def test_child_verifies_running_target_metadata_generation_and_commit_before_the_installer(monkeypatch, tmp_path):
    executable = tmp_path / "hz"
    pin = CorePin("release", f"heinzel @ git+{OFFICIAL}@v{TARGET}", f"v{TARGET}", git=OFFICIAL)
    events = []

    @contextmanager
    def admission():
        events.append("lock")
        yield
        events.append("unlock")

    def verify(requirements, *, expected_version):
        assert requirements == [pin.requirement]
        assert expected_version == TARGET
        events.append("verify")

    def install(version, entrypoint, **kwargs):
        assert events == ["lock", "verify"]
        assert (version, entrypoint) == (TARGET, executable)
        assert kwargs == {"update_only": True, "explicit_update": True, "expected_commit": COMMIT}
        events.append("install")
        return "updated"

    installation = SimpleNamespace(
        core_pin=lambda: pin,
        verify_installed_requirements=verify,
        installed_core_git_commit=lambda: COMMIT,
        startup_fingerprint=lambda: GENERATION,
        tool_entrypoints=lambda: (executable,),
        tool_transaction=nullcontext,
    )
    services = {Installation: installation, DaemonInstallationLifecycle: SimpleNamespace(admission=admission)}
    monkeypatch.setattr(desktop, "boot", lambda *, builtin_only: SimpleNamespace(resolve=services.__getitem__))
    monkeypatch.setattr(desktop, "__version__", TARGET)
    monkeypatch.setattr(desktop, "desktop_installation_record_exists", lambda: True)
    monkeypatch.setattr(desktop, "install_desktop", install)
    with pytest.raises(HeinzelError, match="different Core release"):
        desktop._refresh("0.9.36", GENERATION)
    assert events == []
    result = desktop._refresh(TARGET, GENERATION)
    assert events == ["lock", "verify", "install", "unlock"]
    assert (result.status, result.version, result.detail) == (
        "on-disk",
        None,
        f"Updated the bundle on disk to {TARGET}.",
    )


@pytest.mark.parametrize(
    ("native", "status", "words"),
    [
        ("deferred", "pending", "Quit it, then run `hz system update` again"),
        ("absent", "not-installed", "No managed desktop app"),
        ("current", "on-disk", f"already at {TARGET}"),
        ("newer", "on-disk", "newer than Core"),
        ("installed", "failed", "could not be verified"),
    ],
)
def test_desktop_outcome_keeps_bundle_version_out_of_the_live_version_field(native, status, words):
    result = desktop._desktop_result(TARGET, native)
    assert result.status == status
    assert result.version is None
    assert words in result.detail


def test_child_reports_refused_ownership_as_a_failure_with_action(monkeypatch, capsys):
    def fail(*_args):
        msg = "The desktop receipt no longer owns this bundle."
        raise desktop.DesktopInstallationError(msg)

    monkeypatch.setattr(desktop, "_refresh", fail)
    assert desktop.run_desktop_update([TARGET, GENERATION]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert result["version"] is None
    assert "Run `hz app doctor`" in result["detail"]


def test_child_reports_same_version_binding_conflict_with_the_explicit_install_action(monkeypatch, capsys):
    def fail(*_args):
        old = "/old/bin/hz"
        raise DesktopBindingMismatchError(old, Path("/new/bin/hz"))

    monkeypatch.setattr(desktop, "_refresh", fail)
    assert desktop.run_desktop_update([TARGET, GENERATION]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert result["version"] is None
    assert "different installation of hz" in result["detail"]
    assert "Run hz app install from this CLI installation" in result["detail"]
