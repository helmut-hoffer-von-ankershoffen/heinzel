"""A fake newer release for the automatic-update end-to-end test (ADR 0116); nothing is installed, nothing is fetched.

`sitecustomize` imports this file when `python -m heinzel` starts (its folder is on PYTHONPATH), so the fakes hold
in `hz ui` and in the process a restart in place becomes. They are active only when HZ_E2E_FAKE_RELEASE names a
folder. The fakes themselves are `heinzel._testing_fakes.install_fake_release`, promoted from this file.
"""

import os
from pathlib import Path

FOLDER = os.environ.get("HZ_E2E_FAKE_RELEASE")

if FOLDER:
    from heinzel._testing_fakes import install_fake_release

    install_fake_release(Path(FOLDER))
