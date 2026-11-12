"""Recovery files are read only when owned, private and stable, and moved only without replacing anything."""

import hashlib
import os
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.daemon import _recovery_files as files
from heinzel.modules.system.daemon._recovery_files import path_absent, recovery_file, rename_exclusive

pytestmark = pytest.mark.unit


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    directory = tmp_path.resolve() / "recovery"
    directory.mkdir(mode=0o700)
    return directory


def _write(path: Path, content: bytes = b"marker", mode: int = 0o600) -> Path:
    path.write_bytes(content)
    path.chmod(mode)
    return path


def test_an_owned_private_file_is_captured_with_a_hash_but_without_its_contents(folder):
    path = _write(folder / "marker.json", b'{"secret": 1}')

    captured = recovery_file(path)

    assert captured.content == b'{"secret": 1}'
    facts = captured.facts()
    assert facts["sha256"] == hashlib.sha256(b'{"secret": 1}').hexdigest()
    assert facts["path"] == str(path)
    assert "secret" not in str(facts)
    assert captured.modified_ns == path.stat().st_mtime_ns


def test_a_file_others_can_read_is_refused_unless_it_is_allowed_to_be_shared(folder):
    path = _write(folder / "shared.json", mode=0o644)

    with pytest.raises(HeinzelError, match="could not be verified"):
        recovery_file(path)

    assert recovery_file(path, private=False).content == b"marker"
    path.chmod(0o666)
    with pytest.raises(HeinzelError, match="could not be verified"):
        recovery_file(path, private=False)


def test_an_oversized_or_hard_linked_file_is_refused(folder):
    big = _write(folder / "big", b"x" * (files.LIMIT + 1))
    with pytest.raises(HeinzelError, match="could not be verified"):
        recovery_file(big)
    assert len(recovery_file(_write(folder / "edge", b"x" * files.LIMIT)).content) == files.LIMIT

    original = _write(folder / "original")
    os.link(original, folder / "linked")
    with pytest.raises(HeinzelError, match="could not be verified"):
        recovery_file(original)


def test_a_missing_entry_is_absent_but_a_dangling_link_is_not(folder):
    assert path_absent(folder / "nothing")
    (folder / "dangling").symlink_to(folder / "nothing")
    assert not path_absent(folder / "dangling")


def test_a_move_never_replaces_an_existing_destination(folder):
    source = _write(folder / "source", b"new")
    destination = _write(folder / "destination", b"old")

    with pytest.raises(OSError, match="refused"):
        rename_exclusive(source, destination)

    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"


def test_a_move_goes_through_when_the_source_is_the_file_that_was_captured(folder):
    source = _write(folder / "source", b"marker")
    captured = recovery_file(source)

    rename_exclusive(source, folder / "archived", expected_source=captured)

    assert not source.exists()
    assert (folder / "archived").read_bytes() == b"marker"


def test_a_move_stops_when_the_source_changed_after_it_was_captured(folder):
    source = _write(folder / "source", b"marker")
    captured = recovery_file(source)
    source.write_bytes(b"changed afterwards")

    with pytest.raises(HeinzelError, match="changed immediately"):
        rename_exclusive(source, folder / "archived", expected_source=captured)

    assert source.read_bytes() == b"changed afterwards"
    assert not (folder / "archived").exists()
