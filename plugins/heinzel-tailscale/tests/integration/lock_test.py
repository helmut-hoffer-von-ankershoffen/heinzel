"""Sharing actions use one private cross-process lock without following unowned links."""

import os

import pytest
from heinzel_tailscale._lock import SharingLock

from heinzel import HeinzelError, Settings

pytestmark = pytest.mark.integration


def test_lock_does_not_relabel_an_action_io_failure(tmp_path):
    lock = SharingLock(Settings(state_dir=tmp_path))
    msg = "action failed"
    with pytest.raises(OSError, match="action failed"), lock.hold():
        raise OSError(msg)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public"])
def test_unowned_lock_is_refused_and_never_changes_the_link_target(tmp_path, kind):
    lock = SharingLock(Settings(state_dir=tmp_path))
    lock.directory.mkdir(mode=0o700)
    victim = tmp_path / "untouched"
    victim.write_text("untouched")
    victim.chmod(0o600)
    path = lock.directory / "sharing.lock"
    if kind == "symlink":
        path.symlink_to(victim)
    elif kind == "hardlink":
        path.hardlink_to(victim)
    else:
        path.write_text("")
        path.chmod(0o644)
    with pytest.raises(HeinzelError, match=r"lock|private"), lock.hold():
        pytest.fail("unsafe lock was acquired")
    assert victim.read_text() == "untouched"


def test_new_lock_is_private_even_with_permissive_umask(tmp_path):
    lock = SharingLock(Settings(state_dir=tmp_path))
    previous = os.umask(0)
    try:
        with lock.hold():
            assert lock.directory.stat().st_mode & 0o777 == 0o700
            assert (lock.directory / "sharing.lock").stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(previous)
