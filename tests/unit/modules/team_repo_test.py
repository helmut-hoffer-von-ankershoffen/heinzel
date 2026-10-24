"""Repository URLs, the GitHub repository a URL names, and running git and gh (`team._repo`)."""

from pathlib import Path

import pytest

from heinzel.kernel import HeinzelError
from heinzel.modules.team._repo import Runner, check_url, directory_name, git, github_repository

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme-example/hz-team-payments.git",
        "ssh://git@git.example.invalid/acme/payments.git",
        "git@github.com:acme-example/hz-team-payments.git",
        "file:///srv/git/payments.git",
        "/srv/git/payments.git",
    ],
)
def test_https_ssh_scp_and_local_repositories_are_accepted(url):
    assert check_url(f"  {url} ") == url


def test_a_home_relative_path_is_expanded_because_no_shell_does_it():
    assert not check_url("~/teams/payments").startswith("~")


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("", "empty"),
        ("ext::sh -c touch% /tmp/x", "one line without spaces"),
        ("ext::sh", "transport"),
        ("fd::17", "transport"),
        ("--upload-pack=touch", "dash"),
        ("git@github.com:-oProxyCommand", "dash"),
        ("git@github.com:a=b", "isn.t a repository URL"),
        ("http://github.com/acme/payments.git", "uses http://"),
        ("https://user:token@github.com/acme/payments.git", "password or token"),
        ("https:///payments.git", "names no host"),
        ("https://github.com/acme/payments.git?x=1", "path holds"),
        ("payments", "isn't a repository URL"),
        ("https://github.com/acme/\x1b[31m", "control"),
        ("a" * 3000, "one line"),
        ("ssh://-oProxyCommand=id/x", "host or user"),
        ("ssh://-oProxyCommand@host.example/x", "host or user"),
        ("ssh://host.example:-1/x", "invalid port"),
        ("git@-oProxyCommand:x", "host or user"),
        ("-x@host.example:x", "dash"),
        ("https://ghp_token@github.com/acme/payments.git", "user, password or token"),
    ],
)
def test_urls_git_would_treat_as_commands_or_secrets_are_refused(url, problem):
    with pytest.raises(ValueError, match=problem):
        check_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme-example/hz-team-payments.git", "acme-example/hz-team-payments"),
        ("git@github.com:acme-example/payments", "acme-example/payments"),
        ("ssh://git@GitHub.com/acme-example/payments.git", "acme-example/payments"),
        ("https://git.example.invalid/acme/payments.git", None),
        ("https://github.com/acme-example", None),
        ("/srv/git/payments.git", None),
    ],
)
def test_only_github_repositories_get_pull_requests_through_gh(url, expected):
    assert github_repository(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme-example/hz-team-payments.git", "hz-team-payments"),
        ("git@github.com:acme/Pay_Ments.git", "pay-ments"),
        ("/srv/git/", "git"),
        ("/srv/git/...git", "team"),
    ],
)
def test_a_checkout_is_named_after_the_repository_in_safe_characters(url, expected):
    assert directory_name(url) == expected


def test_a_missing_program_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(HeinzelError, match=r"cli\.github\.com"):
        Runner()(["gh", "api", "user"], None)
    with pytest.raises(HeinzelError, match=r"git-scm\.com"):
        Runner()(["git", "status"], None)


def test_every_git_command_starts_hardened():
    assert git("-C", Path("/x"), "pull")[:7] == [
        "git",
        "-c",
        "submodule.recurse=false",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "protocol.file.allow=user",
    ]
