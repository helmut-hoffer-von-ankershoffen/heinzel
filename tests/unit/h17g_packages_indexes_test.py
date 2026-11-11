"""The probe must retain each amd64 Packages index advertised by signed pockets."""

import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
CHECK = runpy.run_path(str(Path(__file__).resolve().parents[2] / "diagnostics/h17g-python/check_indexes.py"))[
    "check_targets"
]
SOURCE = (
    "Types: deb\nURIs: https://example.invalid/ubuntu\nSuites: noble noble-updates noble-backports\n"
    "Components: main universe restricted multiverse\n\nTypes: deb\nURIs: https://example.invalid/ubuntu\n"
    "Suites: noble-security\nComponents: main universe restricted multiverse\n"
)
SUITES = ("noble", "noble-updates", "noble-backports", "noble-security")


@pytest.fixture
def signed_index_fixture(tmp_path):
    """Represent signed Release metadata and downloaded APT targets without network access."""
    lists = tmp_path / "lists"
    lists.mkdir()
    rows = []
    for suite in SUITES:
        # Some components may have no index advertised by a particular pocket.
        release = lists / f"archive_dists_{suite}_InRelease"
        release.write_text(f"SHA256:\n {'a' * 64} 123 main/binary-amd64/Packages.xz\nSHA512:\n")
        packages = lists / f"archive_{suite}_main_binary-amd64_Packages.lz4"
        packages.write_text("fixture")
        rows.append(f"{suite}|main|amd64|yes|{packages}")
    return lists, rows


def test_every_signed_advertised_target_is_present(signed_index_fixture):
    lists, rows = signed_index_fixture
    assert CHECK(SOURCE, lists, "\n".join(rows)) == (4, 0)


def test_missing_advertised_target_is_rejected(signed_index_fixture):
    lists, rows = signed_index_fixture
    with pytest.raises(ValueError, match="Missing advertised Packages targets"):
        CHECK(SOURCE, lists, "\n".join(rows[:-1]))


def test_untrusted_or_missing_target_is_rejected(signed_index_fixture):
    lists, rows = signed_index_fixture
    rows[0] = rows[0].replace("|yes|", "|no|")
    with pytest.raises(ValueError, match="missing, untrusted or outside"):
        CHECK(SOURCE, lists, "\n".join(rows))


def test_missing_signed_pocket_is_rejected(signed_index_fixture):
    lists, rows = signed_index_fixture
    (lists / "archive_dists_noble-security_InRelease").unlink()
    with pytest.raises(ValueError, match="Expected one signed InRelease"):
        CHECK(SOURCE, lists, "\n".join(rows))


def test_signed_zero_byte_index_can_be_omitted(signed_index_fixture):
    lists, rows = signed_index_fixture
    release = lists / "archive_dists_noble-security_InRelease"
    release.write_text(
        "SHA256:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 0 "
        "main/binary-amd64/Packages\n " + "b" * 64 + " 32 main/binary-amd64/Packages.xz\nSHA512:\n"
    )
    assert CHECK(SOURCE, lists, "\n".join(rows[:-1])) == (3, 1)


def test_zero_size_with_nonempty_digest_cannot_be_omitted(signed_index_fixture):
    lists, rows = signed_index_fixture
    release = lists / "archive_dists_noble-security_InRelease"
    release.write_text(f"SHA256:\n {'a' * 64} 0 main/binary-amd64/Packages\nSHA512:\n")
    with pytest.raises(ValueError, match="Missing advertised Packages targets"):
        CHECK(SOURCE, lists, "\n".join(rows[:-1]))


def test_reported_signed_empty_target_can_have_no_local_file(signed_index_fixture):
    lists, rows = signed_index_fixture
    release = lists / "archive_dists_noble-security_InRelease"
    release.write_text(
        "SHA256:\n e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 0 "
        "main/binary-amd64/Packages\nSHA512:\n"
    )
    (lists / "archive_noble-security_main_binary-amd64_Packages.lz4").unlink()
    assert CHECK(SOURCE, lists, "\n".join(rows)) == (3, 1)
    rows[-1] = rows[-1].replace("|yes|", "|no|")
    with pytest.raises(ValueError, match="missing, untrusted or outside"):
        CHECK(SOURCE, lists, "\n".join(rows))
