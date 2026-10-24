import os
import subprocess
import sys

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, _installation, requirement_from_receipt

pytestmark = pytest.mark.unit

PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
RECEIPT = """
[tool]
requirements = [
    { name = "heinzel", git = "https://github.com/org/heinzel?tag=v0.1.0" },
    { name = "heinzel-old", git = "https://github.com/org/heinzel-old" },
]
"""


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"name": "heinzel"}, "heinzel"),
        ({"name": "heinzel", "specifier": ">=0.1"}, "heinzel>=0.1"),
        ({"name": "heinzel", "extras": ["a", "b"]}, "heinzel[a,b]"),
        ({"name": "heinzel", "git": "https://github.com/org/heinzel"}, "heinzel @ git+https://github.com/org/heinzel"),
        (
            {"name": "heinzel", "git": "https://github.com/org/heinzel?rev=v1"},
            "heinzel @ git+https://github.com/org/heinzel@v1",
        ),
        (
            {"name": "heinzel", "git": "https://github.com/org/heinzel?tag=v1"},
            "heinzel @ git+https://github.com/org/heinzel@v1",
        ),
        (
            {"name": "heinzel", "git": "https://github.com/org/mono?branch=main&subdirectory=heinzel"},
            "heinzel @ git+https://github.com/org/mono@main#subdirectory=heinzel",
        ),
        ({"name": "heinzel", "directory": "/src/heinzel"}, "heinzel @ file:///src/heinzel"),
        ({"name": "heinzel", "url": "https://example.com/heinzel.whl"}, "heinzel @ https://example.com/heinzel.whl"),
    ],
)
def test_requirement_from_receipt(entry, expected):
    assert requirement_from_receipt(entry) == expected


def test_tool_environment(tmp_path):
    (tmp_path / "uv-receipt.toml").write_text(RECEIPT, encoding="utf-8")
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.is_tool
    assert installation.requirements() == [
        "heinzel @ git+https://github.com/org/heinzel@v0.1.0",
        "heinzel-old @ git+https://github.com/org/heinzel-old",
    ]
    assert installation.tool_install_command("heinzel", ["a", "b"]) == [
        "uv", "tool", "install", "--force", "--python", PYTHON, "heinzel", "--with", "a", "--with", "b",
    ]  # fmt: skip
    assert installation.upgrade_command() == ["uv", "tool", "upgrade", "heinzel"]


def test_other_environments(tmp_path):
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert not installation.is_tool
    assert installation.requirements() is None
    assert installation.pip_command("install", "x") == ["uv", "pip", "install", "--python", sys.executable, "x"]


def test_requires_uv(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(HeinzelError, match="uv is required"):
        _ = Installation(Progress(), prefix=tmp_path).uv


def test_run_reports_failures_with_output(tmp_path):
    def failing(command):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="uv says no")

    installation = Installation(Progress(), prefix=tmp_path, uv="uv", runner=failing)
    with pytest.raises(HeinzelError, match=r"Broke\.\nuv says no"):
        installation.run(["uv"], "Broke.", "Doing")


def test_hz_runs_a_fresh_interpreter(tmp_path, monkeypatch):
    # The real interpreter start is in tests/integration/installation_test.py; here only what is started and how a
    # failure is reported.
    commands = []

    def answering(command, **kwargs):
        commands.append(command)
        ok = command[-1] == "--version"
        return subprocess.CompletedProcess(command, 0 if ok else 2, "hz 9.9.9\n" if ok else "", "" if ok else "boom")

    monkeypatch.setattr(subprocess, "run", answering)
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.hz("--version") == "hz 9.9.9\n"
    with pytest.raises(HeinzelError, match="failed after the change:\nboom"):
        installation.hz("no-such-command")
    assert commands == [
        [sys.executable, "-m", "heinzel", "--version"],
        [sys.executable, "-m", "heinzel", "no-such-command"],
    ]


def test_commands_that_hang_time_out(tmp_path, monkeypatch):
    def hanging(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hanging)
    result = _installation._run(["uv", "tool", "install", "x"])
    assert (result.returncode, result.stderr) == (124, f"uv timed out after {_installation.COMMAND_TIMEOUT}s")
    with pytest.raises(HeinzelError, match="did not finish within"):
        Installation(Progress(), prefix=tmp_path, uv="uv").hz("--version")


def test_uv_and_hz_run_without_credential_variables(tmp_path, monkeypatch):
    environments = []

    def recording(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "hz 9.9.9", "")

    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", "not-for-uv")
    monkeypatch.setattr(subprocess, "run", recording)
    _installation._run(["uv", "tool", "list"])
    Installation(Progress(), prefix=tmp_path, uv="uv").hz("--version")
    assert len(environments) == 2
    assert all("HZ_CREDENTIAL_EXAMPLE_API_TOKEN" not in env and "PATH" in env for env in environments)


def test_run_adds_variables_to_the_environment_when_asked(tmp_path, monkeypatch):
    environments = []

    def recording(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", recording)
    Installation(Progress(), prefix=tmp_path, uv="uv").run(["uv"], "Broke.", "Doing", {"GIT_CONFIG_COUNT": "1"})
    Installation(Progress(), prefix=tmp_path, uv="uv").run(["uv"], "Broke.", "Doing")
    assert environments[0]["GIT_CONFIG_COUNT"] == "1"
    assert "PATH" in environments[0]
    assert environments[1].get("GIT_CONFIG_COUNT") == os.environ.get("GIT_CONFIG_COUNT")


def test_the_hz_probe_never_checks_for_updates(tmp_path, monkeypatch):
    environments = []

    def recording(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "hz 9.9.9", "")

    monkeypatch.setenv("HZ_UPDATE_CHECK", "true")
    monkeypatch.setattr(subprocess, "run", recording)
    Installation(Progress(), prefix=tmp_path, uv="uv").hz("--version")
    assert environments[0]["HZ_UPDATE_CHECK"] == "false"


REPO = "https://github.com/example-org/heinzel"


@pytest.mark.parametrize(
    ("entry", "kind", "ref"),
    [
        # What the README's `uv tool install git+…@vX.Y.Z` leaves in the receipt.
        ({"name": "heinzel", "git": f"{REPO}?rev=v0.2.0"}, "release", "v0.2.0"),
        ({"name": "heinzel", "git": f"{REPO}?tag=v0.2.0"}, "release", "v0.2.0"),
        ({"name": "heinzel", "git": f"{REPO}?rev=v0.5.0rc1"}, "release", "v0.5.0rc1"),
        ({"name": "heinzel", "git": f"{REPO}?rev=main"}, "ref", "main"),
        ({"name": "heinzel", "git": f"{REPO}?branch=v1.2.3"}, "ref", "v1.2.3"),  # a branch named like a release
        ({"name": "heinzel", "git": f"{REPO}?tag=stable"}, "ref", "stable"),
        ({"name": "heinzel", "git": f"{REPO}?rev=v1.2"}, "ref", "v1.2"),
        ({"name": "heinzel", "git": f"{REPO}?rev=1a2b3c4"}, "commit", "1a2b3c4"),
        ({"name": "heinzel", "git": f"{REPO}?rev={'a' * 40}"}, "commit", "a" * 40),
        ({"name": "heinzel", "git": f"{REPO}?tag=1a2b3c4"}, "ref", "1a2b3c4"),
        ({"name": "heinzel", "git": REPO}, "default", None),
        ({"name": "heinzel", "directory": "/src/heinzel"}, "local", "/src/heinzel"),
        ({"name": "heinzel", "editable": "/src/heinzel"}, "local", "/src/heinzel"),
        ({"name": "heinzel", "url": "https://example.com/heinzel.whl"}, "url", "https://example.com/heinzel.whl"),
        ({"name": "heinzel", "specifier": ">=0.1"}, "index", ">=0.1"),
        ({"name": "heinzel"}, "index", None),
    ],
)
def test_pin_from_receipt_says_what_the_core_is_pinned_to(entry, kind, ref):
    pin = _installation.pin_from_receipt(entry)
    assert (pin.kind, pin.ref) == (kind, ref)
    assert pin.requirement == requirement_from_receipt(entry)


def test_a_pin_moves_to_another_ref_keeping_extras_and_subdirectory():
    pin = _installation.pin_from_receipt(
        {"name": "heinzel", "extras": ["web"], "git": f"{REPO}.git?rev=v0.2.0&subdirectory=core"}
    )
    assert (pin.version, pin.git) == ("0.2.0", f"{REPO}.git")
    assert pin.source_at("v0.4.0") == f"git+{REPO}.git@v0.4.0#subdirectory=core"
    assert pin.at("v0.4.0") == f"heinzel[web] @ git+{REPO}.git@v0.4.0#subdirectory=core"


def test_only_a_release_tag_has_a_version_and_only_git_has_refs():
    assert _installation.pin_from_receipt({"name": "heinzel", "git": f"{REPO}?rev=main"}).version is None
    pin = _installation.pin_from_receipt({"name": "heinzel", "directory": "/src/heinzel"})
    with pytest.raises(HeinzelError, match="isn't installed from git"):
        pin.source_at("v1.0.0")


def test_core_pin_reads_the_first_requirement_of_the_receipt(tmp_path):
    assert Installation(Progress(), prefix=tmp_path, uv="uv").core_pin() is None
    (tmp_path / "uv-receipt.toml").write_text(RECEIPT, encoding="utf-8")
    pin = Installation(Progress(), prefix=tmp_path, uv="uv").core_pin()
    assert pin is not None
    assert (pin.kind, pin.ref, pin.git) == ("release", "v0.1.0", "https://github.com/org/heinzel")


def test_attempt_returns_how_the_command_ended(tmp_path):
    def failing(command):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="uv says no")

    result = Installation(Progress(), prefix=tmp_path, uv="uv", runner=failing).attempt(["uv"], "Doing")
    assert (result.returncode, result.stderr) == (1, "uv says no")
