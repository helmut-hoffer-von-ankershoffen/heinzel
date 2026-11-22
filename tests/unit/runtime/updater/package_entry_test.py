"""The updater package's public entry: light to import, the schedule's names on first use."""

import subprocess
import sys

import pytest

from heinzel.modules.system import updater
from heinzel.modules.system.updater import _schedule

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", updater.__all__)
def test_the_entry_hands_out_the_schedule_names(name: str) -> None:
    assert getattr(updater, name) is getattr(_schedule, name)


def test_an_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'Missing'"):
        _ = updater.Missing


def test_importing_one_file_of_the_package_leaves_the_schedule_unloaded() -> None:
    """An hz updated in place loads single files of this package next to its old kernel (see the package)."""
    probe = (
        "import sys; import heinzel.modules.system.updater._plugin_standing; "
        "print('heinzel.modules.system.updater._schedule' in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"
