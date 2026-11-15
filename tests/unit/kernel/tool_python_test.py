"""Which Python an update installs onto when the target release needs a newer one (`_tool_python`)."""

import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.kernel import _installation_continuity
from heinzel.kernel._tool_python import managed_python, newer_python

pytestmark = pytest.mark.unit

RUNNING = (3, 14)


@pytest.mark.parametrize("requires", [None, "", "  ", ">=3.12", ">=3.14", ">=3.12,<3.15", "~=3.14.0", ">=3.14.5"])
def test_the_running_python_is_kept_when_the_release_allows_it(requires):
    assert newer_python(requires, RUNNING) is None


@pytest.mark.parametrize(
    ("requires", "needed"),
    [(">=3.15", "3.15"), (">=3.15.2", "3.15"), ("==3.16.*", "3.16"), (">3.14.99,<4", "3.15"), (">=3.17,<3.19", "3.17")],
)
def test_a_release_that_needs_a_newer_python_names_the_lowest_one(requires, needed):
    assert newer_python(requires, RUNNING) == needed


def test_the_default_is_the_running_interpreter():
    major, minor = sys.version_info[:2]
    assert newer_python(f">={major}.{minor}") is None
    assert newer_python(f">={major}.{minor + 1}") == f"{major}.{minor + 1}"


@pytest.mark.parametrize("requires", ["<3.13", ">=4", "not a specifier"])
def test_a_release_no_newer_python_satisfies_is_refused_plainly(requires):
    with pytest.raises(HeinzelError, match="Python") as caught:
        newer_python(requires, RUNNING)
    assert "not started" in str(caught.value) or "keeps working" in str(caught.value)


class FakeUV:
    """uv's `python find` and `python install`, recorded; `has` says whether uv already manages the version."""

    def __init__(self, path: Path, *, has: bool, installs: bool = True) -> None:
        self.path, self.has, self.installs = path, has, installs
        self.commands: list[list[str]] = []

    def __call__(self, command, environment=None):
        self.commands.append(list(command))
        if command[1:3] == ["python", "find"]:
            assert "--managed-python" in command
            assert "--no-python-downloads" in command
            return subprocess.CompletedProcess(command, 0 if self.has else 2, f"{self.path}\n" if self.has else "", "")
        if command[1:3] == ["python", "install"]:
            assert "--managed-python" in command
            self.has = self.installs
            return subprocess.CompletedProcess(command, 0 if self.installs else 1, "", "no download for 3.15")
        raise AssertionError(command)


@pytest.fixture
def interpreter(tmp_path):
    folder = tmp_path / "uv-python" / "cpython-3.15.1" / "bin"
    folder.mkdir(parents=True)
    path = folder / "python3.15"
    path.write_bytes(b"#!/bin/sh\n")
    path.chmod(0o755)
    return path.resolve()


def test_an_installed_managed_python_is_used_without_installing(interpreter):
    uv = FakeUV(interpreter, has=True)
    identity = managed_python(uv, "uv", "3.15")
    assert identity == _installation_continuity.external_python(interpreter)
    assert [command[1:3] for command in uv.commands] == [["python", "find"]]


def test_a_missing_managed_python_is_installed_by_uv_first(interpreter):
    uv = FakeUV(interpreter, has=False)
    assert managed_python(uv, "uv", "3.15")[0] == str(interpreter)
    assert [command[1:3] for command in uv.commands] == [["python", "find"], ["python", "install"], ["python", "find"]]


def test_without_a_managed_python_the_update_stops_and_says_so(interpreter):
    uv = FakeUV(interpreter, has=False, installs=False)
    with pytest.raises(HeinzelError, match=r"could not provide a uv-managed Python 3\.15") as caught:
        managed_python(uv, "uv", "3.15")
    assert "keeps working" in str(caught.value)
    assert "no download for 3.15" in str(caught.value)


def test_an_interpreter_of_another_version_is_refused(tmp_path):
    other = tmp_path / "python3.14"
    other.write_bytes(b"#!/bin/sh\n")
    other.chmod(0o755)
    with pytest.raises(HeinzelError, match="can't be verified"):
        managed_python(FakeUV(other.resolve(), has=True), "uv", "3.15")


def test_a_missing_interpreter_is_refused(tmp_path):
    with pytest.raises(HeinzelError, match="can't be verified"):
        managed_python(FakeUV(tmp_path / "python3.15", has=True), "uv", "3.15")
