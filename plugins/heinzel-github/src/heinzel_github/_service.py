"""What the plugin says about itself: `hz github info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, Credentials, Kernel, Status, __version__
from heinzel_github._models import GithubAbout, GithubSettings
from heinzel_github._module import TOKEN

DISTRIBUTION = "heinzel-github"
ADAPTER = "github"
CAPABILITIES = ("code-host", "tracker")


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class GithubService:
    """Describes the plugin and checks its setup, without ever sending a request (doctor stays offline)."""

    def __init__(self, kernel: Kernel, settings: GithubSettings, credentials: Credentials) -> None:
        """Create the service; the kernel, the plugin's settings and the credentials are injected."""
        self._kernel = kernel
        self._settings = settings
        self._credentials = credentials

    def about(self) -> GithubAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in and the API it talks to."""
        try:
            installed = version(DISTRIBUTION)
        except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
            installed = "0.0.0"
        return GithubAbout(
            plugin=DISTRIBUTION,
            version=installed,
            heinzel_version=__version__,
            modules=[r.qualname for r in self._kernel.modules if r.origin.distribution == DISTRIBUTION],
            api_url=self._settings.api_url,
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """The API in use and which capabilities this adapter serves; degraded when it is installed but serves none.

        A missing token is reported by `system.credential` already, with the command that stores it.
        """
        access = (
            "with a token"
            if self._credentials.has(TOKEN)
            else "anonymously: public repositories only, 60 requests an hour"
        )
        checks = [Check(name="api", status=Status.UP, detail=f"{self._settings.api_url}, {access}; not contacted")]
        bound = {capability: bindings.bound(capability) for capability in CAPABILITIES}
        for capability, adapter in bound.items():
            # The CLI renders text as Rich markup, which swallows a literal `[capabilities]`: name the command.
            setting = f"capabilities.{capability.replace('-', '_')}"
            if adapter == ADAPTER:
                detail = f"served by {ADAPTER}"
            elif adapter:
                detail = f"served by {adapter}, not {ADAPTER}"
            else:
                detail = f"not bound; to use GitHub: hz system config set {setting} {ADAPTER}"
            checks.append(Check(name=capability, status=Status.UP, detail=detail))
        if ADAPTER not in bound.values():
            checks.append(
                Check(
                    name="bindings",
                    status=Status.DEGRADED,
                    detail=f"{DISTRIBUTION} is installed but serves no capability",
                    fix="hz system config set capabilities.code_host github; "
                    "hz system config set capabilities.tracker github",
                )
            )
        return checks
