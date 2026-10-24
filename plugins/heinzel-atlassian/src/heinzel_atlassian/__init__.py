"""Read Jira issues and Confluence pages: the tracker and documents capabilities.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It provides the
adapter `atlassian` for the capabilities `tracker` (Jira) and `documents` (Confluence) in read mode (Heinzelmännchen
ADR 0028); bind it with `[capabilities] tracker = "atlassian"` and `documents = "atlassian"` in your user
configuration.
"""

from heinzel import Check
from heinzel.kernel import CapabilityView
from heinzel_atlassian._confluence import Confluence
from heinzel_atlassian._jira import Jira
from heinzel_atlassian._models import AtlassianAbout, AtlassianSettings
from heinzel_atlassian._module import TOKEN, module
from heinzel_atlassian._service import AtlassianService
from heinzel_atlassian._transport import Response, Transport


def hosts(settings: AtlassianSettings) -> list[str]:
    """Where data goes: the configured site, which the settings accept only under atlassian.net; none until set."""
    return [settings.site_url] if settings.site_url else []


module.settings(AtlassianSettings)
module.service(AtlassianService)
module.service(Transport)
module.provides("tracker", modes={"read"}, hosts=hosts)(Jira)
module.provides("documents", modes={"read"}, hosts=hosts)(Confluence)


@module.operation
def info(service: AtlassianService) -> AtlassianAbout:
    """Show this plugin's version, the Heinzelmännchen it runs on and the Atlassian site and APIs it uses."""
    return service.about()


@module.info
def system_info(service: AtlassianService) -> AtlassianAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health
def health(service: AtlassianService, view: CapabilityView) -> list[Check]:
    """The site, account and bindings; never a request to Atlassian."""
    return service.checks(view)


__all__ = [
    "TOKEN",
    "AtlassianAbout",
    "AtlassianService",
    "AtlassianSettings",
    "Confluence",
    "Jira",
    "Response",
    "Transport",
    "hosts",
    "module",
]
