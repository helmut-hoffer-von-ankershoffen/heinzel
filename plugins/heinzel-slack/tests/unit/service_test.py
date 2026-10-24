import pytest
from heinzel_slack import TOKEN, SlackService, SlackSettings

from heinzel import Credentials, Kernel, Status, __version__
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit


class Bindings:
    def __init__(self, **bound: str) -> None:
        self._bound = bound

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)


def service(token: str | None = None, **settings: object) -> SlackService:
    credentials = Credentials(MemoryBackend(), [("slack", TOKEN)])
    if token:
        credentials.set(TOKEN, token)
    return SlackService(Kernel(), SlackSettings(**settings), credentials)


def test_about_describes_the_plugin_and_the_scopes_it_needs():
    about = service().about()
    assert about.plugin == "heinzel-slack"
    assert about.heinzel_version == __version__
    assert about.modules == []
    assert about.api_url == "https://slack.com/api"
    assert about.token_kind == "user"
    assert "channels:history" in about.read_scopes
    assert about.write_scopes == ["chat:write"]


def test_checks_when_chat_is_bound_and_a_token_is_stored():
    checks = {c.name: c for c in service(token="xoxp-t").checks(Bindings(chat="slack"))}
    assert set(checks) == {"api", "chat"}
    assert all(c.status is Status.UP for c in checks.values())
    assert checks["api"].detail == "https://slack.com/api, with a user token; not contacted, scopes not checked"
    assert checks["chat"].detail == "served by slack"


def test_without_a_token_the_api_is_degraded_with_the_fix():
    api = {c.name: c for c in service(token_kind="bot").checks(Bindings(chat="slack"))}["api"]
    assert api.status is Status.DEGRADED
    assert api.detail == "needs a Slack bot token; no anonymous access"
    assert api.fix == "hz system credential set slack-token"


def test_checks_name_another_adapter_and_the_binding_to_write():
    checks = {c.name: c for c in service().checks(Bindings(chat="teams"))}
    assert checks["chat"].detail == "served by teams, not slack"
    assert checks["bindings"].status is Status.DEGRADED


def test_installed_but_serving_nothing_is_degraded_with_the_fix():
    checks = {c.name: c for c in service().checks(Bindings())}
    assert checks["chat"].detail == "not bound; to use Slack: hz system config set capabilities.chat slack"
    assert checks["bindings"].fix == "hz system config set capabilities.chat slack"
    assert "[" not in checks["bindings"].fix + checks["chat"].detail  # Rich markup would eat it


def test_checks_never_contain_the_token():
    secret = "xoxp-never-in-doctor"
    assert secret not in repr(service(token=secret).checks(Bindings()))
