"""Fixture startup diagnostics stay within the newly installed SDK and preserve existing code."""

import pytest

from tests.e2e._ui_service import startup_diagnostics

pytestmark = pytest.mark.integration


def test_startup_diagnostics_refuse_a_shared_interpreter_directory(tmp_path):
    prefix = tmp_path / "sdk"
    library = prefix / "lib/python3.14"
    library.mkdir(parents=True)
    shared = tmp_path / "shared-packages"
    shared.mkdir()
    (library / "site-packages").symlink_to(shared, target_is_directory=True)
    with pytest.raises(AssertionError, match="fixture SDK"):
        startup_diagnostics(prefix, tmp_path)
    assert list(shared.iterdir()) == []


@pytest.mark.parametrize(
    "startup_name",
    ["sitecustomize.py", "_heinzel_fixture_startup_diagnostics.py", "_heinzel_fixture_startup_diagnostics.pth"],
)
def test_startup_diagnostics_preserve_existing_startup_code(tmp_path, startup_name):
    prefix = tmp_path / "sdk"
    packages = prefix / "lib/python3.14/site-packages"
    packages.mkdir(parents=True)
    target = packages / startup_name
    target.write_text("existing startup code")
    with pytest.raises(AssertionError, match="existing startup code"):
        startup_diagnostics(prefix, tmp_path)
    assert target.read_text() == "existing startup code"
