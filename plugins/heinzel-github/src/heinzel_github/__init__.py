"""Read pull requests and issues from GitHub: the code-host and tracker capabilities.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It provides the
adapter `github` for the capabilities `code-host` and `tracker` in read mode (ADR 0028); bind it with
`[capabilities] code_host = "github"` and `tracker = "github"` in your user configuration.
"""

from heinzel import Check
from heinzel.kernel import CapabilityView
from heinzel_github._adapter import GitHub
from heinzel_github._models import GITHUB_API, GithubAbout, GithubSettings
from heinzel_github._module import TOKEN, module
from heinzel_github._service import GithubService
from heinzel_github._transport import Response, Transport

module.settings(GithubSettings)
module.service(GithubService)
module.service(Transport)


def hosts(settings: GithubSettings) -> list[str]:
    """Where requests go: the host of `[github] api_url`, `api.github.com` unless a GitHub Enterprise Server is set."""
    return [settings.api_url]


# One class serves both capabilities: issues are `tracker` even when they live on the code host (ADR 0028).
module.provides("code-host", modes={"read"}, hosts=hosts)(GitHub)
module.provides("tracker", modes={"read"}, hosts=hosts)(GitHub)


@module.operation
def info(service: GithubService) -> GithubAbout:
    """Show this plugin's version, the Heinzelmännchen it runs on and the GitHub API it uses."""
    return service.about()


@module.info
def system_info(service: GithubService) -> GithubAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health
def health(service: GithubService, view: CapabilityView) -> list[Check]:
    """The API in use and the bindings; never a request to GitHub."""
    return service.checks(view)


__all__ = [
    "GITHUB_API",
    "TOKEN",
    "GitHub",
    "GithubAbout",
    "GithubService",
    "GithubSettings",
    "Response",
    "Transport",
    "hosts",
    "module",
]
