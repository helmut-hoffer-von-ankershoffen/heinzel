"""What the plugin says about itself: `hz github info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, Kernel, Status, __version__
from heinzel_github._gh import INSTALL, Gh, login_fix
from heinzel_github._models import GithubAbout, GithubSettings

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

    def __init__(self, kernel: Kernel, settings: GithubSettings, gh: Gh) -> None:
        """Create the service; the kernel, the plugin's settings and the `gh` runner are injected."""
        self._kernel = kernel
        self._settings = settings
        self._gh = gh

    def about(self) -> GithubAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in and the GitHub host it reads."""
        try:
            installed = version(DISTRIBUTION)
        except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
            installed = "0.0.0"
        return GithubAbout(
            plugin=DISTRIBUTION,
            version=installed,
            heinzel_version=__version__,
            modules=[r.qualname for r in self._kernel.modules if r.origin.distribution == DISTRIBUTION],
            host=self._settings.host,
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """Whether `gh` is installed and logged in, and which capabilities this adapter serves.

        Degraded when `gh` is missing or has no login for the host, and when the adapter is installed but serves
        nothing. Checking the login reads `gh`'s local configuration only; GitHub is never contacted.
        """
        return [self._gh_check(), *self._binding_checks(bindings)]

    def _gh_check(self) -> Check:
        host = self._settings.host
        if (path := self._gh.executable()) is None:
            return Check(
                name="gh",
                status=Status.DEGRADED,
                detail="the GitHub CLI gh is not installed; the adapter reads GitHub through it",
                fix=f"install gh from {INSTALL}, then: {login_fix(host)}",
            )
        if not self._gh.logged_in(host):
            return Check(
                name="gh", status=Status.DEGRADED, detail=f"gh is not logged in to {host}", fix=login_fix(host)
            )
        return Check(name="gh", status=Status.UP, detail=f"gh ({path}) is logged in to {host}; GitHub not contacted")

    def _binding_checks(self, bindings: Bindings) -> list[Check]:
        checks: list[Check] = []
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
