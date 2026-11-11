"""Check every labelled APT access predicate without invoking privileged tools."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[2] / "diagnostics/h17g-python/access.sh"
CHECKS = [
    ("TMP_TRAVERSE", "-x", "/tmp"),  # noqa: S108 - fixed hosted Linux traversal predicate
    ("SCRATCH_TRAVERSE", "-x", "/scratch"),
    ("ROUTE_TRAVERSE", "-x", "/scratch/alternate"),
    ("CONFIG_TRAVERSE", "-x", "/config"),
    ("SOURCE_READ", "-r", "/ubuntu.sources"),
    ("MIRRORS_READ", "-r", "/apt-mirrors.txt"),
    ("LISTS_TRAVERSE", "-x", "/lists"),
    ("LISTS_PARTIAL_TRAVERSE", "-x", "/lists/partial"),
    ("LISTS_PARTIAL_WRITE", "-w", "/lists/partial"),
    ("ARCHIVES_TRAVERSE", "-x", "/archives"),
    ("ARCHIVES_PARTIAL_TRAVERSE", "-x", "/archives/partial"),
    ("ARCHIVES_PARTIAL_WRITE", "-w", "/archives/partial"),
    ("DOWNLOAD_TRAVERSE", "-x", "/download"),
    ("DOWNLOAD_WRITE", "-w", "/download"),
    ("KEYRING_READ", "-r", "/ubuntu-archive-keyring.gpg"),
]


@pytest.fixture
def access_fixture(tmp_path):
    """Build ordinary files and a fake sudo that never changes user or privileges."""
    state = tmp_path / "scratch"
    route = state / "alternate"
    for directory in (route / "config", route / "lists/partial", route / "archives/partial", route / "download"):
        directory.mkdir(parents=True)
        directory.chmod(0o755)
    state.chmod(0o755)
    for name in ("ubuntu.sources", "apt-mirrors.txt"):
        path = route / "config" / name
        path.write_text("fixture\n")
        path.chmod(0o644)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sudo = fake_bin / "sudo"
    sudo.write_text(
        '#!/bin/bash\nset -eu\n[[ "$1" == -u && "$2" == _apt && "$3" == test ]] || exit 2\n'
        'if [[ "${DENY_MODE:-}" == "$4" && -n "${DENY_SUFFIX:-}" && "$5" == *"$DENY_SUFFIX" ]]; then exit 1; fi\n'
        'if [[ "$5" == /usr/share/keyrings/ubuntu-archive-keyring.gpg ]]; then exit 0; fi\n'
        '/bin/test "$4" "$5"\n'
    )
    sudo.chmod(0o755)
    return state, fake_bin


def run_access(state: Path, fake_bin: Path, *, mode: str = "", suffix: str = "") -> subprocess.CompletedProcess:
    """Run the real access reporter with an unprivileged fixture subprocess."""
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "DENY_MODE": mode, "DENY_SUFFIX": suffix}
    return subprocess.run(
        ["bash", str(SCRIPT), str(state), "alternate"], env=env, text=True, capture_output=True, check=False
    )


def test_all_access_predicates_pass_in_fixture(access_fixture):
    state, fake_bin = access_fixture
    result = run_access(state, fake_bin)
    assert result.returncode == 0
    assert {line.split("=")[0].removeprefix("APT_ACCESS_") for line in result.stdout.splitlines()} == {
        label for label, _, _ in CHECKS
    }
    assert all(line.endswith("=PASS") for line in result.stdout.splitlines())
    assert state.stat().st_mode & 0o777 == 0o755
    assert (state / "alternate/config/ubuntu.sources").stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(("label", "mode", "suffix"), CHECKS)
def test_each_denied_predicate_is_named_and_fails_closed(access_fixture, label, mode, suffix):
    state, fake_bin = access_fixture
    result = run_access(state, fake_bin, mode=mode, suffix=suffix)
    assert result.returncode != 0
    assert f"APT_ACCESS_{label}=FAIL" in result.stdout.splitlines()
    assert len(result.stdout.splitlines()) == len(CHECKS)
