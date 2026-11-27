"""Pure spec helpers retain the callable identities and names used by older kernels."""

import importlib
import pickle

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("_json", "_refuse"),
        ("_json", "jsonable"),
        ("_text", "clean_text"),
        ("_text", "display_name"),
        ("_text", "redact_emails"),
    ],
)
def test_spec_helpers_keep_the_published_kernel_identity(module: str, name: str) -> None:
    old_name = f"heinzel.kernel.{module}"
    legacy = getattr(importlib.import_module(old_name), name)
    assert legacy is getattr(importlib.import_module(f"heinzel.spec.{module}"), name)
    assert legacy.__module__ == old_name
    assert pickle.loads(pickle.dumps(legacy)) is legacy  # noqa: S301 - serialized by this test
