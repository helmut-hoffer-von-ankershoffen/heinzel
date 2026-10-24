import pytest
from heinzel_github import GITHUB_API, GithubSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_defaults_to_github_com():
    assert GithubSettings().api_url == GITHUB_API


def test_an_enterprise_url_loses_its_trailing_slash():
    assert GithubSettings(api_url=" https://ghe.example.com/api/v3/ ").api_url == "https://ghe.example.com/api/v3"


@pytest.mark.parametrize(
    "url",
    [
        "http://ghe.example.com/api/v3",
        "ghe.example.com",
        "https://",
        "",
        "https://ghe.example.com/api/v3?x=1",
        "https://ghe.example.com/api/v3?",
        "https://ghe.example.com/api/v3#top",
        "https://user:pw@ghe.example.com/api/v3",
        "https://token@ghe.example.com",
        "https://ghe.example.com:port/api/v3",
        "https:///api/v3",
    ],
)
def test_only_https_urls_without_user_query_or_fragment_are_accepted(url):
    with pytest.raises(ValidationError, match="https URL"):
        GithubSettings(api_url=url)


def test_a_port_is_fine():
    assert (
        GithubSettings(api_url="https://ghe.example.com:8443/api/v3").api_url == "https://ghe.example.com:8443/api/v3"
    )


def test_a_project_file_may_not_set_the_api_url():
    assert "api_url" in GithubSettings.ignored_in_project
