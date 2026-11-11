"""Hypothesis profiles for the property tests: a bounded example count, so each one stays inside the unit budget.

`ci` (chosen when the CI variable is set, as on GitHub Actions) also derandomizes, so every CI run tries the same
examples and takes the same time; `dev` explores new examples on every local run. `HYPOTHESIS_PROFILE` overrides.
"""

import os
from importlib import import_module

import pytest
from hypothesis import settings
from hypothesis import strategies as st

settings.register_profile("dev", max_examples=40, deadline=None, database=None)
settings.register_profile("ci", settings.get_profile("dev"), derandomize=True, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev"))


def pytest_collection_finish() -> None:
    """Prepare static dependency data before any test's clock starts.

    Cold Unicode tables and bundled model prices have each exhausted the first unit test's 5 s budget.
    Collection still counts toward the session cap; model construction and tool execution stay inside each test.
    """
    st.text().validate()
    import_module("genai_prices.data")


@pytest.fixture(autouse=True)
def no_paid_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests can use FunctionModel/TestModel, never a real paid provider by accident."""
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)
