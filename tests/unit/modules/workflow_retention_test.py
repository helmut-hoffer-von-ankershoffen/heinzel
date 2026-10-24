"""Which drafts and records expire, and that deleting them never leaves the drafts directory (ADR 0021)."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.workflow._retention import expired

pytestmark = pytest.mark.unit

CUTOFF = datetime(2026, 7, 28, tzinfo=UTC)


@pytest.fixture
def drafts(tmp_path) -> Path:
    directory = tmp_path / "data" / "workflow" / "drafts"
    directory.mkdir(parents=True)
    return directory


def write(directory: Path, name: str, text: str = "x") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text)
    return path


def test_drafts_and_records_rendered_before_the_cutoff_expire_oldest_first(drafts):
    contract = drafts / "me-weekly-digest"
    old = write(contract, "20260701T070000Z.md", "old draft")
    old_record = write(contract, "20260701T070000Z.record.json", "{}")
    older = write(drafts / "team-ours", "20260601T070000Z-2.json", "[]")
    write(contract, "20260728T000000Z.md")  # at the cutoff: kept
    write(contract, "20261020T070000Z.md")
    found = expired(drafts, CUTOFF, delete=False)
    assert [(e.path, e.record) for e in found] == [(older, False), (old, False), (old_record, True)]
    assert found[1].bytes == len("old draft")
    assert found[0].created == datetime(2026, 6, 1, 7, tzinfo=UTC)


def test_a_dry_run_deletes_nothing_and_a_prune_deletes_only_what_expired(drafts):
    contract = drafts / "me-weekly-digest"
    old = write(contract, "20260701T070000Z.md")
    new = write(contract, "20261020T070000Z.md")
    expired(drafts, CUTOFF, delete=False)
    assert old.exists()
    assert [e.path for e in expired(drafts, CUTOFF, delete=True)] == [old]
    assert not old.exists()
    assert new.exists()
    assert contract.is_dir()  # directories stay: a render may be writing into one


def test_pruning_again_deletes_nothing_more(drafts):
    write(drafts / "me-x", "20260701T070000Z.md")
    assert len(expired(drafts, CUTOFF, delete=True)) == 1
    assert expired(drafts, CUTOFF, delete=True) == []


def test_files_hz_didnt_write_are_left_alone_however_old(drafts):
    contract = drafts / "me-x"
    notes = write(contract, "notes.txt")
    unstamped = write(contract, "draft.md")
    lookalike = write(contract, "20260701T070000Z.md.bak")
    os.utime(unstamped, (0, 0))
    write(drafts, "20260701T070000Z.md")  # not in a contract's directory
    (contract / "20260701T070000Z.md").mkdir()  # a directory with a draft's name
    assert expired(drafts, CUTOFF, delete=True) == []
    assert notes.exists()
    assert unstamped.exists()
    assert lookalike.exists()


def test_no_drafts_directory_yet_is_nothing_to_prune(tmp_path):
    assert expired(tmp_path / "data" / "workflow" / "drafts", CUTOFF, delete=True) == []
    (tmp_path / "data").mkdir()
    assert expired(tmp_path / "data" / "workflow" / "drafts", CUTOFF, delete=True) == []


def test_a_linked_contract_directory_is_skipped_and_its_target_untouched(drafts, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    victim = write(elsewhere, "20260701T070000Z.md")
    (drafts / "me-x").symlink_to(elsewhere, target_is_directory=True)
    assert expired(drafts, CUTOFF, delete=True) == []
    assert victim.exists()


def test_a_linked_draft_is_skipped_and_its_target_untouched(drafts, tmp_path):
    victim = write(tmp_path / "elsewhere", "precious.md")
    contract = drafts / "me-x"
    contract.mkdir()
    (contract / "20260701T070000Z.md").symlink_to(victim)
    assert expired(drafts, CUTOFF, delete=True) == []
    assert victim.exists()
    assert (contract / "20260701T070000Z.md").is_symlink()


@pytest.mark.parametrize("linked", ["workflow", "drafts"])
def test_a_link_below_the_data_directory_is_refused(tmp_path, linked):
    data = tmp_path / "data"
    elsewhere = tmp_path / "elsewhere"
    victim = write(elsewhere / "drafts" / "me-x" if linked == "workflow" else elsewhere / "me-x", "20260701T070000Z.md")
    if linked == "workflow":
        data.mkdir()
        (data / "workflow").symlink_to(elsewhere, target_is_directory=True)
    else:
        (data / "workflow").mkdir(parents=True)
        (data / "workflow" / "drafts").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(HeinzelError, match="symbolic link or not a directory"):
        expired(data / "workflow" / "drafts", CUTOFF, delete=True)
    assert victim.exists()


def test_a_linked_data_directory_is_followed(tmp_path):
    real = tmp_path / "real"
    draft = write(real / "workflow" / "drafts" / "me-x", "20260701T070000Z.md")
    (tmp_path / "data").symlink_to(real, target_is_directory=True)
    found = expired(tmp_path / "data" / "workflow" / "drafts", CUTOFF, delete=True)
    assert [e.path.name for e in found] == [draft.name]
    assert not draft.exists()


def test_a_drafts_directory_that_is_a_file_is_refused(tmp_path):
    write(tmp_path / "data" / "workflow", "drafts")
    with pytest.raises(HeinzelError, match="not a directory"):
        expired(tmp_path / "data" / "workflow" / "drafts", CUTOFF, delete=False)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads and writes any directory")
def test_a_file_that_cant_be_deleted_is_an_error_that_names_it(drafts):
    contract = drafts / "me-x"
    draft = write(contract, "20260701T070000Z.md")
    contract.chmod(0o500)
    try:
        with pytest.raises(HeinzelError, match=f"Can't prune {draft}"):
            expired(drafts, CUTOFF, delete=True)
    finally:
        contract.chmod(0o700)
    assert draft.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_an_unreadable_drafts_directory_is_an_error(drafts):
    drafts.chmod(0o000)
    try:
        with pytest.raises(HeinzelError, match="Can't read"):
            expired(drafts, CUTOFF, delete=False)
    finally:
        drafts.chmod(0o700)
