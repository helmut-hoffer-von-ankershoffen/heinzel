"""What the plugin says about itself: `hz atlassian info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, Credentials, Kernel, Status, __version__
from heinzel_atlassian._client import missing
from heinzel_atlassian._models import AtlassianAbout, AtlassianSettings

DISTRIBUTION = "heinzel-atlassian"
ADAPTER = "atlassian"
CAPABILITIES = ("tracker", "documents")
APIS = ["Jira Cloud REST API v3 (/rest/api/3/search/jql)", "Confluence Cloud REST API v2 (/wiki/api/v2/pages)"]


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class AtlassianService:
    """Describes the plugin and checks its setup, without ever sending a request (doctor stays offline)."""

    def __init__(self, kernel: Kernel, settings: AtlassianSettings, credentials: Credentials) -> None:
        """Create the service; the kernel, the plugin's settings and the credentials are injected."""
        self._kernel = kernel
        self._settings = settings
        self._credentials = credentials

    def about(self) -> AtlassianAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in and the site it talks to."""
        try:
            installed = version(DISTRIBUTION)
        except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
            installed = "0.0.0"
        return AtlassianAbout(
            plugin=DISTRIBUTION,
            version=installed,
            heinzel_version=__version__,
            modules=[r.qualname for r in self._kernel.modules if r.origin.distribution == DISTRIBUTION],
            site_url=self._settings.site_url,
            apis=APIS,
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """The site and account in use, and which capabilities this adapter serves.

        Degraded when the site, the email or the token is missing (there is no anonymous access), and when the plugin
        is installed but serves no capability. A missing token is also reported by `system.credential`.
        """
        needs = missing(self._settings, self._credentials)
        if needs:
            checks = [
                Check(
                    name="site",
                    status=Status.DEGRADED,
                    detail=f"needs {', '.join(name for name, _ in needs)}; no anonymous access",
                    fix="; ".join(fix for _, fix in needs),
                )
            ]
        else:
            checks = [
                Check(name="site", status=Status.UP, detail=f"{self._settings.site_url}, with a token; not contacted")
            ]
        bound = {capability: bindings.bound(capability) for capability in CAPABILITIES}
        for capability, adapter in bound.items():
            # The CLI renders text as Rich markup, which swallows a literal `[capabilities]`: name the command.
            if adapter == ADAPTER:
                detail = f"served by {ADAPTER}"
            elif adapter:
                detail = f"served by {adapter}, not {ADAPTER}"
            else:
                detail = f"not bound; to use Atlassian: hz system config set capabilities.{capability} {ADAPTER}"
            checks.append(Check(name=capability, status=Status.UP, detail=detail))
        if ADAPTER not in bound.values():
            checks.append(
                Check(
                    name="bindings",
                    status=Status.DEGRADED,
                    detail=f"{DISTRIBUTION} is installed but serves no capability",
                    fix="hz system config set capabilities.tracker atlassian; "
                    "hz system config set capabilities.documents atlassian",
                )
            )
        return checks
