"""Hypothesis profiles for the property tests: a bounded example count, so each one stays inside the unit budget.

`ci` (chosen when the CI variable is set, as on GitHub Actions) also derandomizes, so every CI run tries the same
examples and takes the same time; `dev` explores new examples on every local run. `HYPOTHESIS_PROFILE` overrides.
"""

import os

import pytest
from hypothesis import settings
from hypothesis import strategies as st

settings.register_profile("dev", max_examples=40, deadline=None, database=None)
settings.register_profile("ci", settings.get_profile("dev"), derandomize=True, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev"))


def pytest_collection_finish() -> None:
    """Build Hypothesis's Unicode tables before any test's clock starts.

    A worker builds them on its first text strategy; on a fresh runner that took all 5 s of the test that came first.
    """
    st.text().validate()


@pytest.fixture(autouse=True)
def no_paid_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests can use FunctionModel/TestModel, never a real paid provider by accident."""
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)
