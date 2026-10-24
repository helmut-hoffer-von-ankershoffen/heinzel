import pytest
from heinzel_github import TOKEN, GithubService, GithubSettings

from heinzel import Credentials, Kernel, Status, __version__
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit


class Bindings:
    def __init__(self, **bound: str) -> None:
        self._bound = {key.replace("_", "-"): value for key, value in bound.items()}

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)


def service(token: str | None = None) -> GithubService:
    credentials = Credentials(MemoryBackend(), [("github", TOKEN)])
    if token:
        credentials.set(TOKEN, token)
    return GithubService(Kernel(), GithubSettings(), credentials)


def test_about_describes_plugin_without_modules_in_an_empty_kernel():
    about = service().about()
    assert about.plugin == "heinzel-github"
    assert about.heinzel_version == __version__
    assert about.modules == []
    assert about.api_url == "https://api.github.com"


def test_checks_when_both_capabilities_are_bound_to_github():
    checks = {c.name: c for c in service(token="t").checks(Bindings(code_host="github", tracker="github"))}
    assert set(checks) == {"api", "code-host", "tracker"}
    assert all(c.status is Status.UP for c in checks.values())
    assert checks["api"].detail == "https://api.github.com, with a token; not contacted"
    assert checks["tracker"].detail == "served by github"


def test_checks_name_other_adapters_and_the_binding_to_write():
    checks = {c.name: c for c in service().checks(Bindings(code_host="github", tracker="acme"))}
    assert checks["tracker"].detail == "served by acme, not github"
    assert "anonymously: public repositories only" in checks["api"].detail
    assert "bindings" not in checks


def test_installed_but_serving_nothing_is_degraded_with_the_fix():
    checks = {c.name: c for c in service().checks(Bindings())}
    assert checks["code-host"].detail == "not bound; to use GitHub: hz system config set capabilities.code_host github"
    assert checks["bindings"].status is Status.DEGRADED
    assert "hz system config set capabilities.tracker github" in checks["bindings"].fix
    assert "[" not in checks["bindings"].fix + checks["code-host"].detail  # Rich markup would eat it


def test_checks_never_contain_the_token():
    secret = "ghp_never-in-doctor"
    checks = service(token=secret).checks(Bindings())
    assert secret not in repr(checks)
