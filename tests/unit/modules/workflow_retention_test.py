"""Which drafts and records expire, and that deleting them never leaves the drafts directory (ADR 0021)."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.workflow._models import DRAFT_NAME
from heinzel.modules.workflow._retention import expired

pytestmark = pytest.mark.unit

CUTOFF = datetime(2026, 7, 28, tzinfo=UTC)
ROOT = os.geteuid() == 0


@pytest.fixture
def data(tmp_path) -> Path:
    (tmp_path / "data" / "workflow" / "drafts").mkdir(parents=True)
    return tmp_path / "data"


def drafts_of(data: Path) -> Path:
    return data / "workflow" / "drafts"


def write(directory: Path, name: str, text: str = "x") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text)
    return path


def paths(data: Path, *, delete: bool) -> list[Path]:
    sweep = expired(data, CUTOFF, delete=delete)
    assert sweep.problems == []
    return [e.path for e in sweep.found]


def test_drafts_and_records_rendered_before_the_cutoff_expire_oldest_first(data):
    contract = drafts_of(data) / "me-weekly-digest"
    old = write(contract, "20260701T070000Z.md", "old draft")
    old_record = write(contract, "20260701T070000Z.record.json", "{}")
    older = write(drafts_of(data) / "team-ours", "20260601T070000Z-2.json", "[]")
    write(contract, "20260728T000000Z.md")  # at the cutoff: kept
    write(contract, "20261020T070000Z.md")
    sweep = expired(data, CUTOFF, delete=False)
    assert [(e.path, e.record) for e in sweep.found] == [(older, False), (old, False), (old_record, True)]
    assert sweep.found[0].created == datetime(2026, 6, 1, 7, tzinfo=UTC)
    assert (sweep.drafts, sweep.records, sweep.bytes) == (2, 1, len("old draft") + len("{}") + len("[]"))


def test_a_dry_run_deletes_nothing_and_a_prune_deletes_only_what_expired(data):
    contract = drafts_of(data) / "me-weekly-digest"
    old = write(contract, "20260701T070000Z.md")
    new = write(contract, "20261020T070000Z.md")
    assert paths(data, delete=False) == [old]
    assert old.exists()
    assert paths(data, delete=True) == [old]
    assert not old.exists()
    assert new.exists()
    assert contract.is_dir()  # directories stay: a render may be writing into one


def test_pruning_again_deletes_nothing_more(data):
    write(drafts_of(data) / "me-x", "20260701T070000Z.md")
    assert len(paths(data, delete=True)) == 1
    assert paths(data, delete=True) == []


def test_files_hz_didnt_write_are_left_alone_however_old(data):
    contract = drafts_of(data) / "me-x"
    kept = [
        write(contract, "notes.txt"),
        write(contract, "draft.md"),
        write(contract, "20260701T070000Z.md.bak"),
        write(contract, "20260701T070000Z-0.md"),
    ]
    os.utime(kept[1], (0, 0))
    write(drafts_of(data), "20260701T070000Z.md")  # not in a contract's directory
    (contract / "20260701T070000Z.json").mkdir()  # a directory with a draft's name
    assert paths(data, delete=True) == []
    assert all(path.exists() for path in kept)


def test_the_names_a_render_gives_are_the_names_retention_knows():
    for name in ("20261026T070000Z.md", "20261026T070000Z-2.json", "20261026T070000Z-999.record.json"):
        assert DRAFT_NAME.fullmatch(name)


def test_no_drafts_directory_yet_is_nothing_to_prune(tmp_path):
    assert paths(tmp_path / "data", delete=True) == []
    (tmp_path / "data").mkdir()
    assert paths(tmp_path / "data", delete=True) == []
    (tmp_path / "data" / "workflow").mkdir()
    assert paths(tmp_path / "data", delete=True) == []


def test_a_linked_contract_directory_is_skipped_and_its_target_untouched(data, tmp_path):
    victim = write(tmp_path / "elsewhere", "20260701T070000Z.md")
    (drafts_of(data) / "me-x").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    assert paths(data, delete=True) == []
    assert victim.exists()


def test_a_linked_draft_is_skipped_and_its_target_untouched(data, tmp_path):
    victim = write(tmp_path / "elsewhere", "precious.md")
    contract = drafts_of(data) / "me-x"
    contract.mkdir()
    (contract / "20260701T070000Z.md").symlink_to(victim)
    assert paths(data, delete=True) == []
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
        expired(data, CUTOFF, delete=True)
    assert victim.exists()


def test_a_linked_data_directory_is_followed(tmp_path):
    draft = write(tmp_path / "real" / "workflow" / "drafts" / "me-x", "20260701T070000Z.md")
    (tmp_path / "data").symlink_to(tmp_path / "real", target_is_directory=True)
    assert [p.name for p in paths(tmp_path / "data", delete=True)] == [draft.name]
    assert not draft.exists()


def test_a_drafts_directory_that_is_a_file_is_refused(tmp_path):
    write(tmp_path / "data" / "workflow", "drafts")
    with pytest.raises(HeinzelError, match="not a directory"):
        expired(tmp_path / "data", CUTOFF, delete=False)


@pytest.mark.skipif(ROOT, reason="root deletes in any directory")
def test_a_file_that_cant_be_deleted_is_a_problem_and_the_rest_are_still_pruned(data):
    stuck_dir = drafts_of(data) / "me-a"
    stuck = write(stuck_dir, "20260701T070000Z.md")
    other = write(drafts_of(data) / "me-b", "20260701T070000Z.md")
    stuck_dir.chmod(0o500)
    try:
        sweep = expired(data, CUTOFF, delete=True)
    finally:
        stuck_dir.chmod(0o700)
    assert [e.path for e in sweep.found] == [other]
    assert len(sweep.problems) == 1
    assert sweep.problems[0].startswith(f"Can't prune {stuck}")
    assert stuck.exists()
    assert not other.exists()


@pytest.mark.skipif(ROOT, reason="root reads any directory")
def test_an_unreadable_contract_directory_is_a_problem_and_the_others_are_still_pruned(data):
    locked = drafts_of(data) / "me-a"
    write(locked, "20260701T070000Z.md")
    other = write(drafts_of(data) / "me-b", "20260701T070000Z.md")
    locked.chmod(0o000)
    try:
        sweep = expired(data, CUTOFF, delete=True)
    finally:
        locked.chmod(0o700)
    assert [e.path for e in sweep.found] == [other]
    assert [p.split(":")[0] for p in sweep.problems] == [f"Can't read {locked}"]


@pytest.mark.skipif(ROOT, reason="root reads any directory")
def test_an_unreadable_drafts_directory_is_an_error(data):
    drafts_of(data).chmod(0o000)
    try:
        with pytest.raises(HeinzelError, match="Can't read"):
            expired(data, CUTOFF, delete=False)
    finally:
        drafts_of(data).chmod(0o700)


@pytest.mark.skipif(ROOT, reason="root lists any directory")
def test_a_contract_directory_that_opens_but_cant_be_listed_is_a_problem(data):
    contract = drafts_of(data) / "me-a"
    write(contract, "20260701T070000Z.md")
    contract.chmod(0o300)  # write and search, no read: open succeeds on some systems, listing does not
    try:
        sweep = expired(data, CUTOFF, delete=True)
    finally:
        contract.chmod(0o700)
    assert sweep.found == []
    assert len(sweep.problems) == 1
