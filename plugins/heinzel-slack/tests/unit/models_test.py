import pytest
from heinzel_slack import SLACK_API, SlackSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_defaults_are_a_user_token_on_slack_com():
    settings = SlackSettings()
    assert (settings.api_url, settings.token_kind, settings.thread_lookback_days) == (SLACK_API, "user", 30)


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://slack.com/api/", SLACK_API),
        (" HTTPS://Slack.com/api ", SLACK_API),
        ("https://acme.slack.com/api", None),
    ],
)
def test_the_api_url_is_normalised(given, kept):
    assert SlackSettings(api_url=given).api_url == (kept or given)


@pytest.mark.parametrize(
    "url",
    [
        "http://slack.com/api",
        "https://slack.com.evil.example/api",
        "https://evilslack.com/api",
        "https://user@slack.com/api",
        "https://slack.com:8443/api",
        "https://slack.com/api?x=1",
        "https://slack.com",
    ],
)
def test_the_api_url_must_be_slack_over_https(url):
    with pytest.raises(ValidationError, match="api_url must be the Slack Web API"):
        SlackSettings(api_url=url)


def test_project_files_can_set_neither_where_the_token_goes_nor_as_whom():
    assert SlackSettings.ignored_in_project == {"api_url", "token_kind"}


@pytest.mark.parametrize("days", [-1, 366])
def test_the_lookback_is_bounded(days):
    with pytest.raises(ValidationError):
        SlackSettings(thread_lookback_days=days)
