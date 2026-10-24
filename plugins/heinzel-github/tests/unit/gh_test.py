from pathlib import Path

import pytest
from heinzel_github._gh import FIXED, absolute_path, environment, hosts_file, login_fix, parse_version

pytestmark = pytest.mark.unit


def test_gh_gets_only_allowlisted_variables_pinned_to_the_host():
    source = {
        "PATH": "/usr/bin:bin::/opt/bin",
        "HOME": "/home/ada",
        "GH_HOST": "elsewhere.example.com",
        "GH_DEBUG": "api",
        "GODEBUG": "http2debug=2",
        "HZ_CREDENTIAL_ACME_TOKEN": "made-up-secret",
        "LC_TIME": "C",
        "https_proxy": "http://proxy.example.com:3128",
        "GH_ENTERPRISE_TOKEN": "made-up-gh-token",
    }
    assert environment("ghe.example.com", source) == {
        "PATH": "/usr/bin:/opt/bin",
        "HOME": "/home/ada",
        "LC_TIME": "C",
        "https_proxy": "http://proxy.example.com:3128",
        "GH_ENTERPRISE_TOKEN": "made-up-gh-token",
        **FIXED,
        "GH_HOST": "ghe.example.com",
    }


def test_without_a_path_gh_gets_none():
    assert "PATH" not in environment("github.com", {"HOME": "/home/ada"})


def test_relative_path_entries_are_left_out():
    assert absolute_path(".:bin::/usr/local/bin") == "/usr/local/bin"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("gh version 2.103.2 (2026-10-23)\nhttps://github.com/cli/cli/releases/tag/v2.103.2\n", (2, 103, 2)),
        ("gh version 2.40.0\n", (2, 40, 0)),
        ("gh version DEV\n", None),
        ("", None),
    ],
)
def test_the_version_is_read_from_gh_version(text, found):
    assert parse_version(text) == found


def test_the_hosts_file_follows_gh_config_dir_then_xdg_config_home_then_home():
    assert hosts_file({"GH_CONFIG_DIR": "/c", "XDG_CONFIG_HOME": "/x"}) == Path("/c/hosts.yml")
    assert hosts_file({"XDG_CONFIG_HOME": "/x"}) == Path("/x/gh/hosts.yml")
    assert hosts_file({}) == Path.home() / ".config" / "gh" / "hosts.yml"


def test_the_login_fix_names_the_host():
    assert login_fix("ghe.example.com") == "gh auth login --hostname ghe.example.com"
