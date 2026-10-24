"""`Installation.hz` starts a real, fresh interpreter, as `hz system update` and the plugin commands do."""

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation

pytestmark = pytest.mark.integration


def test_hz_runs_a_fresh_interpreter(tmp_path):
    installation = Installation(Progress(), prefix=tmp_path, uv="uv")
    assert installation.hz("--version").startswith("hz ")
    with pytest.raises(HeinzelError, match="failed after the change"):
        installation.hz("no-such-command")
