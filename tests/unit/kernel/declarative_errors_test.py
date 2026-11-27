"""The exceptions keep one identity and their published serialization path after the spec move."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

OLD_PATH = "heinzel.kernel._errors"
NEW_PATH = "heinzel.spec._errors"
PROBE = """\
import importlib
import pickle
import sys

first, second = map(importlib.import_module, sys.argv[1:])
import heinzel
import heinzel.kernel

legacy = "heinzel.kernel._errors"
for name in ("HeinzelError", "NotSupportedError", "SubscriptionUsageLimitError"):
    error = getattr(first, name)
    assert error is getattr(second, name) is getattr(heinzel.kernel, name)
    assert (error.__module__, error.__qualname__) == (legacy, name)
    if name == "SubscriptionUsageLimitError":
        assert not hasattr(heinzel, name)
    else:
        assert getattr(heinzel, name) is error

    # These are the global names shipped by 0.9.55, regardless of pickle protocol.
    published = f"c{legacy}\\n{name}\\np0\\n.".encode("ascii")
    assert pickle.loads(published) is error
    assert pickle.dumps(error, protocol=0) == published

assert issubclass(first.NotSupportedError, first.HeinzelError)
assert issubclass(first.SubscriptionUsageLimitError, first.HeinzelError)
"""


@pytest.mark.parametrize(("first", "second"), [(OLD_PATH, NEW_PATH), (NEW_PATH, OLD_PATH)])
def test_fresh_process_import_order_and_published_pickle_names(first: str, second: str) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE, first, second], capture_output=True, text=True, check=False, timeout=15
    )
    assert result.returncode == 0, result.stderr
