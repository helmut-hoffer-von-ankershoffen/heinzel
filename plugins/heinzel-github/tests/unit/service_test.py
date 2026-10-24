import pytest
from heinzel_github import GithubService, GithubSettings

from heinzel import Kernel, Status, __version__

pytestmark = pytest.mark.unit


class Bindings:
    def __init__(self, **bound: str) -> None:
        self._bound = {key.replace("_", "-"): value for key, value in bound.items()}

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)


BOTH = Bindings(code_host="github", tracker="github")


def service(fake, **settings) -> GithubService:
    return GithubService(Kernel(), GithubSettings(**settings), fake)


def test_about_describes_plugin_without_modules_in_an_empty_kernel(fake_gh):
    about = service(fake_gh).about()
    assert about.plugin == "heinzel-github"
    assert about.heinzel_version == __version__
    assert about.modules == []
    assert about.host == "github.com"


def test_checks_when_gh_is_logged_in_and_both_capabilities_are_bound(fake_gh):
    checks = {c.name: c for c in service(fake_gh).checks(BOTH)}
    assert set(checks) == {"gh", "code-host", "tracker"}
    assert all(c.status is Status.UP for c in checks.values())
    assert checks["gh"].detail == "gh is logged in to github.com; GitHub not contacted"
    assert checks["tracker"].detail == "served by github"
    assert fake_gh.calls == []


def test_gh_without_a_login_for_the_host_is_degraded_with_the_login_command(fake_gh):
    gh = {c.name: c for c in service(fake_gh, host="ghe.example.com").checks(BOTH)}["gh"]
    assert gh.status is Status.DEGRADED
    assert gh.detail == "gh is not logged in to ghe.example.com"
    assert gh.fix == "gh auth login --hostname ghe.example.com"


def test_missing_gh_is_degraded_with_where_to_get_it(fake_gh):
    fake_gh.installed = False
    gh = {c.name: c for c in service(fake_gh).checks(BOTH)}["gh"]
    assert gh.status is Status.DEGRADED
    assert gh.fix == "install gh from https://cli.github.com, then: gh auth login --hostname github.com"


def test_checks_name_other_adapters_and_the_binding_to_write(fake_gh):
    checks = {c.name: c for c in service(fake_gh).checks(Bindings(code_host="github", tracker="acme"))}
    assert checks["tracker"].detail == "served by acme, not github"
    assert "bindings" not in checks


def test_installed_but_serving_nothing_is_degraded_with_the_fix(fake_gh):
    checks = {c.name: c for c in service(fake_gh).checks(Bindings())}
    assert checks["code-host"].detail == "not bound; to use GitHub: hz system config set capabilities.code_host github"
    assert checks["bindings"].status is Status.DEGRADED
    assert "hz system config set capabilities.tracker github" in checks["bindings"].fix
    assert "[" not in checks["bindings"].fix + checks["code-host"].detail  # Rich markup would eat it
