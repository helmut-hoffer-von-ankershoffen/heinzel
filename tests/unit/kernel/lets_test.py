"""Host names of adapters (ADR 0028): what `host_of` accepts and what a hosts function may read."""

import pytest

from heinzel import ModuleSettings, ProviderLet
from heinzel.kernel._lets import host_of, settings_parameters

pytestmark = pytest.mark.unit


class AcmeSettings(ModuleSettings):
    api_url: str = "https://api.acme.invalid"


@pytest.mark.parametrize(
    ("value", "host"),
    [
        ("api.acme.invalid", "api.acme.invalid"),
        ("API.Acme.Invalid", "api.acme.invalid"),
        ("https://api.acme.invalid/v3", "api.acme.invalid"),
        ("https://user:s3cret@git.acme.invalid:8443/api/v3?x=1#y", "git.acme.invalid"),
        ("git.acme.invalid:8443/api", "git.acme.invalid"),
        ("10.0.0.1", "10.0.0.1"),
        ("http://[2001:DB8::1]:8080/", "2001:db8::1"),
    ],
)
def test_host_of_keeps_only_the_host_name(value, host):
    assert host_of(value) == host


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://",
        "not a host",
        "https://[::1",
        "http://[zz::1]/",
        "http://[v1.future:host]/",
        "bücher.invalid",
        "-acme.invalid",
        "a..b",
        "x" * 254,
    ],
)
def test_host_of_refuses_what_names_no_host(value):
    assert host_of(value) is None


def test_settings_parameters_maps_each_parameter_to_its_settings_class():
    def hosts(acme: AcmeSettings) -> list[str]:
        return [acme.api_url]

    assert settings_parameters(hosts) == {"acme": AcmeSettings}


def no_settings() -> list[str]:
    return []


def a_service(acme: AcmeSettings, other: str) -> list[str]:
    return [acme.api_url, other]


def variadic(*settings: AcmeSettings) -> list[str]:
    return [s.api_url for s in settings]


def positional(acme: AcmeSettings, /) -> list[str]:
    return [acme.api_url]


def unresolvable(acme: "Missing") -> list[str]:  # noqa: F821 - a forward reference that never resolves
    return [acme]


@pytest.mark.parametrize(
    ("func", "problem"),
    [
        (AcmeSettings, "not a class"),
        (no_settings, "takes no settings"),
        (a_service, "'other' is not one"),
        (variadic, "'settings' is not one"),
        (positional, "'acme' is not one"),
        (unresolvable, "Can't read the parameters"),
    ],
)
def test_settings_parameters_refuses_functions_that_read_more_than_settings(func, problem):
    with pytest.raises(ValueError, match=problem):
        settings_parameters(func)


def provider(**kwargs: object) -> ProviderLet:
    return ProviderLet(
        name="acme:tracker", adapter="acme", capability="tracker", modes=frozenset({"read"}), factory=object, **kwargs
    )


def test_a_provider_let_refuses_hosts_that_are_not_host_names():
    with pytest.raises(ValueError, match="hosts must be host names"):
        provider(hosts=("https://api.acme.invalid",))


def test_a_provider_let_takes_hosts_as_a_list_or_a_function_not_both():
    def hosts(acme: AcmeSettings) -> list[str]:
        return [acme.api_url]

    with pytest.raises(ValueError, match="not both"):
        provider(hosts=("api.acme.invalid",), configured_hosts=hosts)
