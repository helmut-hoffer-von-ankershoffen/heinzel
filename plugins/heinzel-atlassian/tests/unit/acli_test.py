import pytest
from heinzel_atlassian._acli import environment, parse_version

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("acli version 1.3.39-stable\n", (1, 3, 39)),
        ("acli version v2.0.1\n", (2, 0, 1)),
        ("something else\n", None),
        ("", None),
    ],
)
def test_the_version_is_read_from_acli_version(text, version):
    assert parse_version(text) == version


def test_only_allowlisted_variables_and_no_colour_reach_acli():
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/wilma",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "C",
        "ACLI_LOG_FILE": "/home/wilma/acli.log",
        "https_proxy": "http://proxy.acme.example:3128",
        "NO_PROXY": "localhost",
        "HZ_CREDENTIAL_ATLASSIAN_ATLASSIAN_TOKEN": "fake-token-never-passed-on",
        "GH_TOKEN": "fake-token-of-another-vendor",
        "AWS_SECRET_ACCESS_KEY": "fake-secret",
        "PYTHONPATH": "/somewhere",
    }
    assert environment(source) == {
        "PATH": "/usr/bin",
        "HOME": "/home/wilma",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "C",
        "ACLI_LOG_FILE": "/home/wilma/acli.log",
        "https_proxy": "http://proxy.acme.example:3128",
        "NO_PROXY": "localhost",
        "NO_COLOR": "1",
    }
