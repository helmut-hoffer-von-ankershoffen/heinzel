"""The diagnostic may change transport but never the Ubuntu trust or suite scope."""

import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SOURCE = (
    "Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\n"
    "Suites: noble noble-updates noble-backports\nComponents: main restricted universe multiverse\n"
    "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n\nTypes: deb\n"
    "URIs: http://security.ubuntu.com/ubuntu\nSuites: noble-security\n"
    "Components: main restricted universe multiverse\n"
    "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
)
MIRRORS = "http://azure.archive.ubuntu.com/ubuntu\n"
TRANSFORM = runpy.run_path(str(Path(__file__).resolve().parents[2] / "diagnostics/h17g-python/sources.py"))["transform"]


def non_uri(source: str) -> list[str]:
    """Compare every source field except the transport endpoint."""
    return [line for line in source.splitlines() if not line.startswith("URIs:")]


def test_unchanged_route_keeps_security_and_non_uri_fields(tmp_path):
    out, mirrors = TRANSFORM(SOURCE, MIRRORS, str(tmp_path / "mirrors"), "unchanged")
    assert f"URIs: mirror+file:{tmp_path}/mirrors" in out
    assert "URIs: http://security.ubuntu.com/ubuntu" in out
    assert mirrors == "https://archive.ubuntu.com/ubuntu\n"
    assert non_uri(SOURCE) == non_uri(out)


def test_alternate_preserves_suites_and_components(tmp_path):
    out, _ = TRANSFORM(SOURCE, MIRRORS, str(tmp_path / "mirrors"), "alternate")
    assert out.count("URIs: https://mirrors.edge.kernel.org/ubuntu") == 2
    assert non_uri(SOURCE) == non_uri(out)


def test_unknown_uri_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="unexpected Ubuntu URI"):
        TRANSFORM(
            SOURCE.replace("http://security.ubuntu.com/ubuntu", "http://unknown.invalid/ubuntu"),
            MIRRORS,
            str(tmp_path / "mirrors"),
            "alternate",
        )


@pytest.mark.parametrize(
    "broken",
    [
        SOURCE.replace("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg", "Signed-By: /different/key"),
        SOURCE.replace("noble-security", "noble"),
        SOURCE.replace("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg", "Signed-By: /different/key", 1),
    ],
)
def test_missing_trust_or_suite_fails_closed(broken, tmp_path):
    with pytest.raises(ValueError, match=r"keyring|inventory"):
        TRANSFORM(broken, MIRRORS, str(tmp_path / "mirrors"), "alternate")


@pytest.mark.parametrize(
    "override",
    [
        "tRuStEd: yes",
        "Allow-Insecure: yes",
        "check-date: no",
        "Check-Valid-Until: no",
        "Signed-By: /another/key",
        " Signed-By: /another/key",
    ],
)
def test_trust_overrides_duplicate_and_folded_fields_fail_closed(override, tmp_path):
    source = SOURCE.replace("Types: deb", "Types: deb\n" + override, 1)
    with pytest.raises(ValueError, match="folded, duplicated or forbidden"):
        TRANSFORM(source, MIRRORS, str(tmp_path / "mirrors"), "alternate")


def test_signed_by_field_name_is_case_insensitive(tmp_path):
    source = SOURCE.replace("Signed-By:", "sIgNeD-bY:")
    out, _ = TRANSFORM(source, MIRRORS, str(tmp_path / "mirrors"), "alternate")
    assert non_uri(source) == non_uri(out)


def test_unreviewed_mirror_endpoint_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="unexpected endpoint"):
        TRANSFORM(SOURCE, MIRRORS + "https://unknown.invalid/ubuntu\n", str(tmp_path / "mirrors"), "unchanged")
