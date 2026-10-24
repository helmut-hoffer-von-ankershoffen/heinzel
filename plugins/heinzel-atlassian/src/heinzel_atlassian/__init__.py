"""Read Jira issues and Confluence pages through acli: the tracker and documents capabilities.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It provides the
adapter `atlassian` for the capabilities `tracker` (Jira) and `documents` (Confluence) in read mode (Heinzelmännchen
ADR 0028); bind it with `[capabilities] tracker = "atlassian"` and `documents = "atlassian"` in your user
configuration. Every read runs Atlassian's CLI `acli`, which owns authentication: the plugin stores no credential.
"""

from heinzel import Check
from heinzel.kernel import CapabilityView
from heinzel_atlassian._acli import Acli, Completed
from heinzel_atlassian._confluence import Confluence
from heinzel_atlassian._jira import Jira
from heinzel_atlassian._models import AtlassianAbout, AtlassianSettings
from heinzel_atlassian._module import module
from heinzel_atlassian._service import AtlassianService

GATEWAY = "api.atlassian.com"
"""Atlassian's API gateway, which acli uses when it logged in with OAuth (`acli jira auth login --web`)."""


def hosts(settings: AtlassianSettings) -> list[str]:
    """Where acli sends the reads: the configured site and Atlassian's API gateway; none until the site is set."""
    return [settings.site_url, GATEWAY] if settings.site_url else []


module.settings(AtlassianSettings)
module.service(AtlassianService)
module.service(Acli)
module.provides("tracker", modes={"read"}, hosts=hosts)(Jira)
module.provides("documents", modes={"read"}, hosts=hosts)(Confluence)


@module.operation
def info(service: AtlassianService) -> AtlassianAbout:
    """Show this plugin's version, the Atlassian site and the acli commands it runs."""
    return service.about()


@module.info
def system_info(service: AtlassianService) -> AtlassianAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health
def health(service: AtlassianService, view: CapabilityView) -> list[Check]:
    """acli, the site and bindings; never runs acli, so never a request to Atlassian."""
    return service.checks(view)


__all__ = [
    "GATEWAY",
    "Acli",
    "AtlassianAbout",
    "AtlassianService",
    "AtlassianSettings",
    "Completed",
    "Confluence",
    "Jira",
    "hosts",
    "module",
]
