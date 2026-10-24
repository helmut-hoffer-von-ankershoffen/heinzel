import pytest
from heinzel_github._gh import DROPPED, environment, login_fix

pytestmark = pytest.mark.unit


def test_gh_runs_pinned_to_the_host_and_never_interactive(monkeypatch):
    monkeypatch.setenv("GH_HOST", "elsewhere.example.com")
    env = environment("ghe.example.com")
    assert env["GH_HOST"] == "ghe.example.com"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env["GH_PAGER"] == ""
    assert env["NO_COLOR"] == "1"


def test_gh_never_sees_credentials_or_debug_switches(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_ACME_TOKEN", "made-up-secret")
    for name in DROPPED:
        monkeypatch.setenv(name, "1")
    env = environment("github.com")
    assert "HZ_CREDENTIAL_ACME_TOKEN" not in env
    assert not DROPPED & set(env)


def test_gh_keeps_its_own_login_variables(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "made-up-gh-token")
    assert environment("github.com")["GH_TOKEN"] == "made-up-gh-token"


def test_the_login_fix_names_the_host():
    assert login_fix("ghe.example.com") == "gh auth login --hostname ghe.example.com"
