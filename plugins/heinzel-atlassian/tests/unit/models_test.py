import pytest
from heinzel_atlassian import AtlassianSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_nothing_is_set_by_default():
    settings = AtlassianSettings()
    assert (settings.site_url, settings.timeout) == (None, 60.0)


def test_no_email_or_token_setting_exists_any_more():
    assert set(AtlassianSettings.model_fields) == {"site_url", "acli", "timeout"}


@pytest.mark.parametrize("acli", ["acli", " acli ", "/opt/acli/bin/acli"])
def test_acli_is_a_name_or_an_absolute_path(acli):
    assert AtlassianSettings(acli=acli).acli == acli.strip()


@pytest.mark.parametrize("acli", ["", "  ", "bin/acli", "./acli", "../acli"])
def test_a_relative_path_to_acli_is_refused(acli):
    with pytest.raises(ValidationError, match="absolute path"):
        AtlassianSettings(acli=acli)


def test_a_project_file_may_not_pick_acli_or_its_timeout():
    assert {"acli", "timeout"} <= AtlassianSettings.ignored_in_project


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
    assert AtlassianSettings(site_url=" ").site_url is None


@pytest.mark.parametrize("timeout", [0, -1, 601])
def test_the_timeout_is_bounded(timeout):
    with pytest.raises(ValidationError):
        AtlassianSettings(timeout=timeout)


def test_a_project_file_may_not_set_the_site():
    assert {"site_url"} <= AtlassianSettings.ignored_in_project
