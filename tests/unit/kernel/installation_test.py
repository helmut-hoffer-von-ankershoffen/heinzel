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


def test_hz_runs_a_fresh_interpreter(tmp_path):
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.hz("--version").startswith("hz ")
    with pytest.raises(HeinzelError, match="failed after the change"):
        installation.hz("no-such-command")


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


def test_the_hz_probe_never_checks_for_updates(tmp_path, monkeypatch):
    environments = []

    def recording(command, **kwargs):
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "hz 9.9.9", "")

    monkeypatch.setenv("HZ_UPDATE_CHECK", "true")
    monkeypatch.setattr(subprocess, "run", recording)
    Installation(Progress(), prefix=tmp_path, uv="uv").hz("--version")
    assert environments[0]["HZ_UPDATE_CHECK"] == "false"
