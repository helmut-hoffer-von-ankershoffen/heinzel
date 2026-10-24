import pytest
from heinzel_github import GITHUB_HOST, GithubSettings
from heinzel_github._models import api_host
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_defaults_to_github_com():
    assert GithubSettings().host == GITHUB_HOST == "github.com"


def test_an_enterprise_host_is_trimmed_and_lower_cased():
    assert GithubSettings(host=" GHE.Example.com ").host == "ghe.example.com"


@pytest.mark.parametrize(
    "host",
    [
        "https://ghe.example.com",
        "ghe.example.com/api/v3",
        "ghe.example.com:8443",
        "user@ghe.example.com",
        "localhost",
        "",
        "-ghe.example.com",
        "ghe..example.com",
        "ghe.example.com --with-token",
        "ghe_example.com",
    ],
)
def test_only_host_names_are_accepted(host):
    with pytest.raises(ValidationError, match="host must be a host name"):
        GithubSettings(host=host)


def test_a_project_file_may_not_set_the_host():
    assert "host" in GithubSettings.ignored_in_project


@pytest.mark.parametrize(
    ("host", "api"),
    [("github.com", "api.github.com"), ("acme.ghe.com", "api.acme.ghe.com"), ("ghe.example.com", "ghe.example.com")],
)
def test_the_api_host_is_where_gh_sends_requests(host, api):
    assert api_host(host) == api
