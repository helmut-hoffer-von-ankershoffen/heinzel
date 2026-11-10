"""uv is found in its standard install directories when the process PATH is launchd's minimal one (bead cwte)."""

import os
import stat
from pathlib import Path
from types import SimpleNamespace

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
    (tmp_path / ".local/bin").mkdir(parents=True)
    (tmp_path / ".cargo/bin").mkdir(parents=True)
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


@pytest.mark.usefixtures("private_system_dirs")
def test_missing_uv_names_every_searched_directory_once(tmp_path):
    (tmp_path / ".local/bin").mkdir(parents=True)
    (tmp_path / ".cargo/bin").mkdir(parents=True)
    message = missing_uv_message({"PATH": MINIMAL, "HOME": str(tmp_path)})
    assert message.count("uv is required") == 1
    searched = message.split("it is not in ", 1)[1].split(". Install it:", 1)[0].split(", ")
    for entry in [*MINIMAL.split(":"), "/opt/homebrew/bin", str(tmp_path / ".local/bin"), str(tmp_path / ".cargo/bin")]:
        assert searched.count(entry) == 1
    assert "https://docs.astral.sh/uv/" in message


@pytest.mark.usefixtures("private_system_dirs")
def test_subprocess_environment_gets_the_augmented_path(tmp_path):
    environment = with_search_path({"PATH": MINIMAL, "HOME": str(tmp_path), "KEEP": "1"})
    assert environment["KEEP"] == "1"
    assert environment["PATH"].startswith(MINIMAL)
    assert "/opt/homebrew/bin" in environment["PATH"].split(os.pathsep)


def test_empty_search_uses_the_system_default_path(monkeypatch):
    monkeypatch.setattr(_uv_locate, "standard_directories", lambda _: [])

    assert with_search_path({"PATH": os.pathsep})["PATH"] == os.defpath


def test_world_writable_directories_are_ignored(tmp_path):
    shared = tmp_path / "shared"
    _fake_uv(shared)
    shared.chmod(0o777)
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path / "home"), "UV_INSTALL_DIR": str(shared)}
    assert str(shared) not in standard_directories(environment)
    assert find_uv(environment) != str(shared / "uv")


def test_configured_directory_cannot_inject_relative_path_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    uv = _fake_uv(tmp_path / "bin")
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path), "UV_INSTALL_DIR": f"/missing{os.pathsep}./bin"}

    assert "./bin" not in with_search_path(environment)["PATH"].split(os.pathsep)
    selected = find_uv(environment)
    assert selected is None or Path(selected).resolve() != uv.resolve()


@pytest.mark.parametrize("mode", [0o777, 0o770], ids=["world-writable", "group-writable"])
def test_replaceable_ancestor_is_ignored(tmp_path, mode):
    shared = tmp_path / "shared"
    uv = _fake_uv(shared / "private/bin")
    shared.chmod(mode)
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path), "UV_INSTALL_DIR": str(uv.parent)}

    assert str(uv.parent) not in standard_directories(environment)
    assert find_uv(environment) != str(uv)


def test_sticky_ancestor_protects_user_owned_directory(tmp_path):
    shared = tmp_path / "shared"
    uv = _fake_uv(shared / "private/bin")
    shared.chmod(0o1777)
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path), "UV_INSTALL_DIR": str(uv.parent)}

    assert str(uv.parent) in standard_directories(environment)
    assert str(shared) not in standard_directories({**environment, "UV_INSTALL_DIR": str(shared)})


def test_sticky_ancestor_does_not_protect_a_foreign_symlink(tmp_path, monkeypatch):
    target = _fake_uv(tmp_path / "private/bin").parent
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o1777)
    alias = shared / "bin"
    alias.symlink_to(target)
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path), "UV_INSTALL_DIR": str(alias)}
    lstat = Path.lstat

    def foreign_link(path):
        if path == alias:
            return SimpleNamespace(st_uid=os.geteuid() + 1, st_mode=lstat(path).st_mode)
        return lstat(path)

    monkeypatch.setattr(Path, "lstat", foreign_link)

    assert str(alias) not in standard_directories(environment)


def test_missing_directories_and_files_are_ignored(tmp_path):
    file = tmp_path / "file"
    file.touch()
    missing = tmp_path / "missing"
    directories = standard_directories(
        {"HOME": str(tmp_path), "UV_INSTALL_DIR": str(file), "XDG_BIN_HOME": str(missing)}
    )

    assert str(file) not in directories
    assert str(missing) not in directories


def test_ancestry_checks_follow_relative_directory_symlinks(tmp_path):
    uv = _fake_uv(tmp_path / "private/bin")
    links = tmp_path / "links"
    links.mkdir()
    alias = links / "bin"
    alias.symlink_to("../private/bin")
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path), "UV_INSTALL_DIR": str(alias)}

    assert find_uv(environment) == str(alias / uv.name)
    uv.parent.parent.chmod(0o777)
    assert str(alias) not in standard_directories(environment)


def test_ancestry_checks_follow_intermediate_symlinks(tmp_path):
    target = _fake_uv(tmp_path / "private/bin").parent
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    intermediate = shared / "redirect"
    intermediate.symlink_to(target)
    alias = tmp_path / "bin"
    alias.symlink_to(intermediate)
    environment = {"PATH": MINIMAL, "HOME": str(tmp_path), "UV_INSTALL_DIR": str(alias)}

    assert str(alias) not in standard_directories(environment)
