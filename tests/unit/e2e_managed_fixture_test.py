"""The E2E managed interpreter copy remains inside its private installation."""

import pytest

from tests.e2e.conftest import copy_managed_installation

pytestmark = pytest.mark.unit


def test_private_copy_remaps_absolute_and_relative_internal_links(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "library").write_text("fixture")
    (source / "absolute").symlink_to(source / "library")
    (source / "relative").symlink_to("library")
    copied = tmp_path / "private"

    copy_managed_installation(source, copied)

    for name in ("absolute", "relative"):
        assert (copied / name).resolve(strict=True) == copied / "library"
        assert (copied / name).read_text() == "fixture"


def test_private_copy_rejects_escaping_link_before_copying(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "outside").write_text("outside")
    (source / "escape").symlink_to("../outside")
    copied = tmp_path / "private"

    with pytest.raises(ValueError, match="link outside"):
        copy_managed_installation(source, copied)
    assert not copied.exists()
