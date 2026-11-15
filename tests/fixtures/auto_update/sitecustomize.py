"""Load the fake newer release (`fake_release.py`) for the automatic-update end-to-end test, once Python is running.

Python 3.15 runs this file before the interpreter counts as initialized, and pydantic's Rust core panics when heinzel
(which builds pydantic models) is imported that early. So the fakes load when `python -m heinzel` starts its main
module. They are active only when HZ_E2E_FAKE_RELEASE names a folder.
"""

import importlib.abc
import os
import sys
from typing import Any


class _AfterStartup(importlib.abc.MetaPathFinder):
    def find_spec(self, name: str, path: Any = None, target: Any = None) -> None:
        if name == "heinzel.__main__":
            sys.meta_path.remove(self)
            import fake_release  # noqa: F401


if os.environ.get("HZ_E2E_FAKE_RELEASE"):
    sys.meta_path.insert(0, _AfterStartup())
