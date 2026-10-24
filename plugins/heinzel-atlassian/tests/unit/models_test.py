import pytest
from heinzel_atlassian import AtlassianSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_nothing_is_set_by_default():
    settings = AtlassianSettings()
    assert (settings.site_url, settings.email, settings.timeout) == (None, None, 30.0)


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.atlassian.net",
        " https://ACME.atlassian.net/ ",
        "https://acme.atlassian.net/wiki",
        "https://acme.atlassian.net/wiki/",
        "https://acme.atlassian.net/jira",
    ],
)
def test_a_cloud_site_is_normalised(url):
    assert AtlassianSettings(site_url=url).site_url == "https://acme.atlassian.net"


@pytest.mark.parametrize(
    "url",
    [
        "http://acme.atlassian.net",
        "acme.atlassian.net",
        "https://acme.atlassian.net.evil.example",
        "https://evil.example/acme.atlassian.net",
        "https://a.b.atlassian.net",
        "https://acme.atlassian.net:8443",
        "https://ada@acme.atlassian.net",
        "https://acme.atlassian.net/wiki/spaces",
        "https://acme.atlassian.net?x=1",
        "https://jira.acme.example",
        "https://-acme.atlassian.net",
    ],
)
def test_anything_but_an_atlassian_cloud_site_is_refused(url):
    with pytest.raises(ValidationError, match="Atlassian Cloud site"):
        AtlassianSettings(site_url=url)


def test_blank_values_mean_unset():
    assert AtlassianSettings(site_url=" ", email="").site_url is None
    assert AtlassianSettings(email="  ").email is None


def test_an_email_is_trimmed():
    assert AtlassianSettings(email=" ada@acme.example ").email == "ada@acme.example"


@pytest.mark.parametrize("email", ["ada", "ada@", "ada:x@acme.example", "a da@acme.example", "ada@acme"])
def test_implausible_emails_are_refused(email):
    with pytest.raises(ValidationError, match="Atlassian account"):
        AtlassianSettings(email=email)


def test_a_refused_email_is_not_echoed():
    with pytest.raises(ValidationError) as caught:
        AtlassianSettings(email="secret-person:pw@acme.example")
    assert "secret-person" not in str(caught.value)


def test_a_project_file_may_set_neither_the_site_nor_the_email():
    assert {"site_url", "email"} <= AtlassianSettings.ignored_in_project
