"""Hypothesis profiles for the property tests: a bounded example count, so each one stays inside the unit budget.

`ci` (chosen when the CI variable is set, as on GitHub Actions) also derandomizes, so every CI run tries the same
examples and takes the same time; `dev` explores new examples on every local run. `HYPOTHESIS_PROFILE` overrides.
"""

import os

import pytest
from hypothesis import settings

settings.register_profile("dev", max_examples=40, deadline=None, database=None)
settings.register_profile("ci", settings.get_profile("dev"), derandomize=True, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev"))


@pytest.fixture(autouse=True)
def no_paid_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests can use FunctionModel/TestModel, never a real paid provider by accident."""
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(autouse=True)
def no_catalog_feed_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never read the published plugin catalog from the network; they inject their own."""

    def refuse(url: str, etag: str | None) -> tuple[int, str | None, bytes]:
        message = "no network in unit tests"
        raise OSError(message)

    monkeypatch.setattr("heinzel.modules.system.plugin._feed._fetch", refuse)
