import pytest
from heinzel_atlassian import TOKEN, AtlassianService, AtlassianSettings

from heinzel import Credentials, Kernel, Status, __version__
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

CONFIGURED = {"site_url": "https://acme.atlassian.net", "email": "ada@acme.example"}


class Bindings:
    def __init__(self, **bound: str) -> None:
        self._bound = bound

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)


def service(token: str | None = None, **settings) -> AtlassianService:
    credentials = Credentials(MemoryBackend(), [("atlassian", TOKEN)])
    if token:
        credentials.set(TOKEN, token)
    return AtlassianService(Kernel(), AtlassianSettings(**settings), credentials)


def test_about_describes_plugin_site_and_apis():
    about = service(**CONFIGURED).about()
    assert about.plugin == "heinzel-atlassian"
    assert about.heinzel_version == __version__
    assert about.modules == []
    assert about.site_url == "https://acme.atlassian.net"
    assert any("/rest/api/3/search/jql" in api for api in about.apis)
    assert any("/wiki/api/v2/pages" in api for api in about.apis)


def test_checks_when_configured_and_bound():
    checks = {
        c.name: c for c in service("t", **CONFIGURED).checks(Bindings(tracker="atlassian", documents="atlassian"))
    }
    assert set(checks) == {"site", "tracker", "documents"}
    assert all(c.status is Status.UP for c in checks.values())
    assert checks["site"].detail == "https://acme.atlassian.net, with a token; not contacted"
    assert checks["documents"].detail == "served by atlassian"


def test_without_site_email_and_token_the_site_check_is_degraded_with_every_fix():
    site = {c.name: c for c in service().checks(Bindings(tracker="atlassian"))}["site"]
    assert site.status is Status.DEGRADED
    assert "no anonymous access" in site.detail
    assert site.fix is not None
    assert "hz system config set atlassian.site_url https://acme.atlassian.net" in site.fix
    assert "hz system config set atlassian.email" in site.fix
    assert "hz system credential set atlassian-token" in site.fix


def test_only_what_is_missing_is_named():
    site = {c.name: c for c in service(**CONFIGURED).checks(Bindings())}["site"]
    assert site.status is Status.DEGRADED
    assert site.fix == "hz system credential set atlassian-token"


def test_checks_name_other_adapters_and_the_binding_to_write():
    checks = {c.name: c for c in service("t", **CONFIGURED).checks(Bindings(tracker="atlassian", documents="acme"))}
    assert checks["documents"].detail == "served by acme, not atlassian"
    assert "bindings" not in checks


def test_installed_but_serving_nothing_is_degraded_with_the_fix():
    checks = {c.name: c for c in service("t", **CONFIGURED).checks(Bindings())}
    assert (
        checks["tracker"].detail == "not bound; to use Atlassian: hz system config set capabilities.tracker atlassian"
    )
    assert checks["bindings"].status is Status.DEGRADED
    assert checks["bindings"].fix is not None
    assert "hz system config set capabilities.documents atlassian" in checks["bindings"].fix
    assert "[" not in checks["bindings"].fix + checks["tracker"].detail  # Rich markup would eat it


def test_checks_never_contain_the_token():
    secret = "fake-token-never-in-doctor"
    assert secret not in repr(service(secret, **CONFIGURED).checks(Bindings()))
