"""Hypothesis profiles for the property tests: a bounded example count, so each one stays inside the unit budget.

`ci` (chosen when the CI variable is set, as on GitHub Actions) also derandomizes, so every CI run tries the same
examples and takes the same time; `dev` explores new examples on every local run. `HYPOTHESIS_PROFILE` overrides.
"""

import os

from hypothesis import settings

settings.register_profile("dev", max_examples=40, deadline=None, database=None)
settings.register_profile("ci", settings.get_profile("dev"), derandomize=True, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev"))
