import pytest
from heinzel_atlassian import AtlassianService, AtlassianSettings
from heinzel_atlassian._acli import AcliTimeoutError

from heinzel import Kernel, Login, Status, __version__

pytestmark = pytest.mark.unit

CONFIGURED = {"site_url": "https://acme.atlassian.net"}
ACLI = "/opt/acli/bin/acli"


class Bindings:
    def __init__(self, **bound: str) -> None:
        self._bound = bound

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)


def service(fake, **settings) -> AtlassianService:
    return AtlassianService(Kernel(), AtlassianSettings(**settings), fake)


def check(fake, name: str = "acli", bindings: Bindings | None = None, **settings):
    return {c.name: c for c in service(fake, **settings).checks(bindings or Bindings(tracker="atlassian"))}[name]


def test_about_describes_plugin_site_acli_and_commands(fake_acli):
    about = service(fake_acli, **CONFIGURED).about()
    assert about.plugin == "heinzel-atlassian"
    assert about.heinzel_version == __version__
    assert about.modules == []
    assert about.site_url == "https://acme.atlassian.net"
    assert about.acli == ACLI
    assert any(c.startswith("acli jira workitem search") for c in about.commands)
    assert any(c.startswith("acli confluence page view") for c in about.commands)


def test_checks_when_acli_is_there_and_the_site_set_and_bound(fake_acli):
    bindings = Bindings(tracker="atlassian", documents="atlassian")
    checks = {c.name: c for c in service(fake_acli, **CONFIGURED).checks(bindings)}
    assert set(checks) == {"acli", "site", "tracker", "documents"}
    assert all(c.status is Status.UP for c in checks.values())
    assert checks["acli"].detail.startswith(f"{ACLI}, acli 1.3.39; login is acli's (acli jira auth login;")
    assert "checked on first use: doctor stays offline" in checks["acli"].detail
    assert (
        checks["site"].detail
        == "https://acme.atlassian.net; not contacted; with a browser login, acli's answers name no site to match"
    )
    assert fake_acli.calls == []  # `--version` only, which the fake answers without a run


def test_without_acli_the_check_is_degraded_and_names_the_install_and_login(fake_acli):
    fake_acli.location = None
    acli = check(fake_acli, **CONFIGURED)
    assert acli.status is Status.DEGRADED
    assert acli.fix is not None
    assert "install-acli" in acli.fix
    assert "acli jira auth login" in acli.fix
    assert "acli confluence auth login" in acli.fix


def test_an_old_acli_is_degraded_with_the_upgrade(fake_acli):
    fake_acli.reported = (1, 2, 0)
    acli = check(fake_acli, **CONFIGURED)
    assert acli.status is Status.DEGRADED
    assert acli.detail == f"{ACLI} is acli 1.2.0; the adapter needs 1.3.39 or later"
    assert acli.fix is not None
    assert acli.fix.startswith("upgrade acli")


@pytest.mark.parametrize("error", [AcliTimeoutError("acli"), PermissionError(13, "denied")])
def test_an_acli_that_does_not_answer_its_version_is_degraded(fake_acli, error):
    fake_acli.version_error = error
    acli = check(fake_acli, **CONFIGURED)
    assert acli.status is Status.DEGRADED
    assert type(error).__name__ in acli.detail


def test_an_acli_that_names_no_version_is_up_and_says_so(fake_acli):
    fake_acli.reported = None
    acli = check(fake_acli, **CONFIGURED)
    assert acli.status is Status.UP
    assert "of a version it doesn't name" in acli.detail


def test_without_a_site_the_check_names_the_command(fake_acli):
    site = check(fake_acli, "site")
    assert site.status is Status.DEGRADED
    assert site.fix == "hz system config set atlassian.site_url https://acme.atlassian.net"


def test_checks_name_other_adapters_and_the_binding_to_write(fake_acli):
    checks = {
        c.name: c for c in service(fake_acli, **CONFIGURED).checks(Bindings(tracker="atlassian", documents="acme"))
    }
    assert checks["documents"].detail == "served by acme, not atlassian"
    assert "bindings" not in checks


def test_installed_but_serving_nothing_is_degraded_with_the_fix(fake_acli):
    checks = {c.name: c for c in service(fake_acli, **CONFIGURED).checks(Bindings())}
    assert (
        checks["tracker"].detail == "not bound; to use Atlassian: hz system config set capabilities.tracker atlassian"
    )
    assert checks["bindings"].status is Status.DEGRADED
    assert checks["bindings"].fix is not None
    assert "hz system config set capabilities.documents atlassian" in checks["bindings"].fix
    assert "[" not in checks["bindings"].fix + checks["tracker"].detail  # Rich markup would eat it


def test_offline_the_sign_in_is_unknown_with_the_login_of_the_product_in_use(fake_acli):
    jira = check(fake_acli, bindings=Bindings(tracker="atlassian"), **CONFIGURED).login
    assert jira == Login(signed_in=None, site="acme.atlassian.net", command="acli jira auth login")
    confluence = check(fake_acli, bindings=Bindings(documents="atlassian")).login
    assert confluence == Login(signed_in=None, command="acli confluence auth login")
    fake_acli.location = None
    assert check(fake_acli, bindings=Bindings()).login == Login(signed_in=None, command="acli jira auth login")


def test_the_live_test_reads_one_item_of_each_product_in_use(fake_acli):
    fake_acli.search([fake_acli.issue(1, updated="2026-10-20T10:00:00.000+0000")])
    fake_acli.answer("confluence space list", [{"key": "WID"}])
    both = Bindings(tracker="atlassian", documents="atlassian")
    checks = service(fake_acli, **CONFIGURED).live(both)
    assert [(c.name, c.status) for c in checks] == [("Jira read", Status.UP), ("Confluence read", Status.UP)]
    assert all(c.login is not None and c.login.signed_in for c in checks)
    assert [call.args[:3] for call in fake_acli.calls] == [
        ["jira", "workitem", "search"],
        ["confluence", "space", "list"],
    ]
    assert all(call.timeout == 15.0 for call in fake_acli.calls)
    assert fake_acli.calls[0].args[fake_acli.calls[0].args.index("--limit") + 1] == "1"


def test_a_logged_out_live_test_is_not_signed_in_with_the_login(fake_acli):
    fake_acli.fail(fake_acli.SEARCH, fake_acli.UNAUTHORIZED)
    (jira,) = service(fake_acli, **CONFIGURED).live(Bindings())
    assert jira.status is Status.DEGRADED
    assert jira.fix == "acli jira auth login"
    assert jira.login == Login(signed_in=False, site="acme.atlassian.net", command="acli jira auth login")


def test_a_live_test_that_fails_otherwise_is_down_without_a_sign_in(fake_acli):
    fake_acli.fail("confluence space list", "boom", exit_status=3)
    (confluence,) = service(fake_acli).live(Bindings(documents="atlassian"))
    assert confluence.status is Status.DOWN
    assert confluence.login is None
