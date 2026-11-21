"""The state folder an update leaves is the one hz 0.9.53 left: same names, same bytes, and it reads it back.

Moving the update behind `InstallationState` changed who names the folder, not what is in it. The layout asserted here
is the 0.9.53 one: `before-update/` (the tool folder as it was) and, while a put-back runs, `after-update/` with its
`MOVED_ASIDE` marker; nothing else, no lock, no journal.
"""

from pathlib import Path

import pytest

from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._announced import MOVED_ASIDE, ToolSnapshot

pytestmark = pytest.mark.unit


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def prefix(tmp_path):
    tool = tmp_path / "tool"
    (tool / "lib/site").mkdir(parents=True)
    (tool / "lib/site/heinzel.py").write_bytes(b"version = '0.9.53'\n")
    (tool / "pyvenv.cfg").write_bytes(b"home = /usr/bin\n")
    (tool / "bin").mkdir()
    (tool / "bin/hz").symlink_to("../lib/site/heinzel.py")
    return tool


def test_a_snapshot_into_the_state_port_is_byte_identical_to_one_into_a_plain_folder(prefix, tmp_path):
    plain = tmp_path / "plain"
    ToolSnapshot.take(prefix, plain)
    state = FileInstallationState(tmp_path / "state")
    ToolSnapshot.take(prefix, state.folder)
    assert tree(state.folder) == tree(plain)
    assert sorted(p.name for p in state.folder.iterdir()) == ["before-update"]
    assert (state.folder / "before-update/bin/hz").is_symlink()


def test_a_state_folder_hz_0_9_53_left_after_a_dead_update_is_read_and_put_back(prefix, tmp_path):
    folder = tmp_path / "state"
    ToolSnapshot.take(prefix, folder)  # what 0.9.53 wrote before it died during the install
    (prefix / "lib/site/heinzel.py").write_bytes(b"version = '0.9.54'\n")  # the half-finished install
    (prefix / "lib/site/new.py").write_bytes(b"new\n")
    state = FileInstallationState(folder)
    ToolSnapshot.take(prefix, state.folder)  # the next update recovers first, through the port's folder
    assert (prefix / "lib/site/heinzel.py").read_bytes() == b"version = '0.9.53'\n"
    assert not (prefix / "lib/site/new.py").exists()


def test_a_put_back_that_stopped_half_way_finishes_from_what_is_left(prefix, tmp_path):
    folder = tmp_path / "state"
    snapshot = ToolSnapshot.take(prefix, folder)
    (prefix / "lib/site/heinzel.py").write_bytes(b"version = '0.9.54'\n")
    snapshot.aside.mkdir()
    (snapshot.aside / MOVED_ASIDE).touch()  # 0.9.53's marker: everything the update left is aside already
    for entry in list(prefix.iterdir()):
        entry.rename(snapshot.aside / entry.name)
    FileInstallationState(folder)  # the folder is only named by the port
    snapshot.recover()
    assert (prefix / "lib/site/heinzel.py").read_bytes() == b"version = '0.9.53'\n"
    assert not snapshot.copy.exists()
    assert not snapshot.aside.exists()
