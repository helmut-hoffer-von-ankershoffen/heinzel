"""uv is found in its standard install directories when the process PATH is launchd's minimal one (bead cwte)."""

import os
import stat
from pathlib import Path

import pytest

from heinzel.kernel import _uv_locate
from heinzel.kernel._uv_locate import find_uv, missing_uv_message, search_path, standard_directories, with_search_path

pytestmark = pytest.mark.unit

MINIMAL = "/usr/bin:/bin:/usr/sbin:/sbin"


def _fake_uv(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    uv = directory / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    return uv


def test_finds_uv_from_the_standalone_installer_with_a_minimal_path(tmp_path):
    uv = _fake_uv(tmp_path / ".local/bin")
    assert find_uv({"PATH": MINIMAL, "HOME": str(tmp_path)}) == str(uv)


def test_finds_uv_in_a_configured_install_dir(tmp_path):
    uv = _fake_uv(tmp_path / "brew/bin")
    assert find_uv({"PATH": MINIMAL, "HOME": str(tmp_path / "home"), "UV_INSTALL_DIR": str(uv.parent)}) == str(uv)


def test_the_process_path_wins(tmp_path):
    chosen = _fake_uv(tmp_path / "chosen")
    _fake_uv(tmp_path / ".local/bin")
    assert find_uv({"PATH": str(chosen.parent), "HOME": str(tmp_path)}) == str(chosen)


@pytest.fixture
def private_system_dirs(monkeypatch):
    """Treat the system prefixes as not world-writable: hosted runners make /usr/local/bin writable by everyone."""
    real = _uv_locate._trusted
    monkeypatch.setattr(_uv_locate, "_trusted", lambda entry: entry.startswith(("/opt/", "/usr/")) or real(entry))


@pytest.mark.usefixtures("private_system_dirs")
def test_standard_directories_cover_homebrew_cargo_and_the_installer(tmp_path):
    directories = standard_directories({"HOME": str(tmp_path)})
    assert directories == [
        str(tmp_path / ".local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(tmp_path / ".cargo/bin"),
    ]


def test_relative_directories_are_ignored(tmp_path):
    directories = standard_directories({"HOME": str(tmp_path), "UV_INSTALL_DIR": "bin", "XDG_BIN_HOME": "./x"})
    assert all(Path(entry).is_absolute() for entry in directories)


@pytest.mark.usefixtures("private_system_dirs")
def test_search_path_keeps_order_without_duplicates(tmp_path):
    path = search_path({"PATH": f"/opt/homebrew/bin{os.pathsep}{MINIMAL}", "HOME": str(tmp_path)})
    entries = path.split(os.pathsep)
    assert entries[:5] == ["/opt/homebrew/bin", *MINIMAL.split(":")]
    assert len(entries) == len(set(entries))
    assert "/usr/local/bin" in entries


def test_missing_uv_names_every_searched_directory_once(tmp_path):
    message = missing_uv_message({"PATH": MINIMAL, "HOME": str(tmp_path)})
    assert message.count("uv is required") == 1
    for entry in [*MINIMAL.split(":"), "/opt/homebrew/bin", str(tmp_path / ".local/bin"), str(tmp_path / ".cargo/bin")]:
        assert entry in message
    assert "https://docs.astral.sh/uv/" in message


def test_subprocess_environment_gets_the_augmented_path(tmp_path):
    environment = with_search_path({"PATH": MINIMAL, "HOME": str(tmp_path), "KEEP": "1"})
    assert environment["KEEP"] == "1"
    assert environment["PATH"].startswith(MINIMAL)
    assert "/opt/homebrew/bin" in environment["PATH"].split(os.pathsep)


def test_world_writable_directories_are_ignored(tmp_path):
    shared = tmp_path / "shared"
    _fake_uv(shared)
    shared.chmod(0o777)
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path / "home"), "UV_INSTALL_DIR": str(shared)}
    assert str(shared) not in standard_directories(environment)
    assert find_uv(environment) != str(shared / "uv")
