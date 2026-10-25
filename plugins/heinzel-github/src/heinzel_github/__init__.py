"""Read pull requests and issues from GitHub: the code-host and tracker capabilities.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It provides the
adapter `github` for the capabilities `code-host` and `tracker` in read mode (ADR 0028); bind it with
`[capabilities] code_host = "github"` and `tracker = "github"` in your user configuration. It talks to GitHub only
through the GitHub CLI `gh`, which owns authentication: run `gh auth login` once.
"""

from pathlib import Path

from heinzel import Check
from heinzel.kernel import CapabilityView
from heinzel_github._adapter import GitHub
from heinzel_github._gh import Gh, GhResult
from heinzel_github._models import GITHUB_HOST, GithubAbout, GithubSettings, api_host
from heinzel_github._module import module
from heinzel_github._service import GithubService

module.settings(GithubSettings)
module.service(GithubService)
module.service(Gh)
module.skill(Path(__file__).parent / "skills")  # advice for AI, read-only (ADR 0061)


def hosts(settings: GithubSettings) -> list[str]:
    """Where `gh` sends requests: `api.github.com` unless `github.host` names a GitHub Enterprise Server."""
    return [api_host(settings.host)]


# One class serves both capabilities: issues are `tracker` even when they live on the code host (ADR 0028).
module.provides("code-host", modes={"read"}, hosts=hosts)(GitHub)
module.provides("tracker", modes={"read"}, hosts=hosts)(GitHub)


@module.operation
def info(service: GithubService) -> GithubAbout:
    """Show this plugin's version, the Heinzelmännchen it runs on and the GitHub host it reads."""
    return service.about()


@module.info
def system_info(service: GithubService) -> GithubAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health
def health(service: GithubService, view: CapabilityView) -> list[Check]:
    """Whether `gh` is installed and logged in, and the bindings; never a request to GitHub."""
    return service.checks(view)


@module.health(live=True)
def live(service: GithubService) -> list[Check]:
    """Ask GitHub who `gh` is logged in as: one read, run only by `hz system connector test github`."""
    return service.live()


__all__ = [
    "GITHUB_HOST",
    "Gh",
    "GhResult",
    "GitHub",
    "GithubAbout",
    "GithubService",
    "GithubSettings",
    "hosts",
    "module",
]
