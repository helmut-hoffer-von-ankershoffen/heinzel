"""What the plugin says about itself: `hz atlassian info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, Kernel, Status, __version__
from heinzel_atlassian._acli import MINIMUM_VERSION, Acli, AcliTimeoutError
from heinzel_atlassian._client import INSTALL, LOGIN, SITE_FIX
from heinzel_atlassian._models import AtlassianAbout, AtlassianSettings

DISTRIBUTION = "heinzel-atlassian"
ADAPTER = "atlassian"
CAPABILITIES = ("tracker", "documents")
COMMANDS = [
    "acli jira workitem search --jql <jql> --fields <fields> --paginate --json",
    "acli confluence page view --id <id> --body-format storage --json",
]
LOGINS = f"{LOGIN['Jira']}; {LOGIN['Confluence']}"


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class AtlassianService:
    """Describes the plugin and checks its setup; doctor runs only `acli --version`, which needs no network."""

    def __init__(self, kernel: Kernel, settings: AtlassianSettings, acli: Acli) -> None:
        """Create the service; the kernel, the plugin's settings and the acli runner are injected."""
        self._kernel = kernel
        self._settings = settings
        self._acli = acli

    def about(self) -> AtlassianAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in, the site and acli."""
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
            acli=self._acli.path(),
            commands=COMMANDS,
        )

    def _acli_check(self) -> Check:
        """Where acli is and which version it says it is; `acli --version` needs no network."""
        path = self._acli.path()
        if path is None:
            return Check(
                name="acli",
                status=Status.DEGRADED,
                detail=f"{self._settings.acli}, Atlassian's CLI, is not on PATH; every read goes through it",
                fix=f"install acli ({INSTALL}), then {LOGINS}",
            )
        try:
            found = self._acli.version()
        except (AcliTimeoutError, OSError) as exc:  # FileNotFoundError too, if it went away meanwhile
            return Check(
                name="acli",
                status=Status.DEGRADED,
                detail=f"{path} did not answer `acli --version` ({type(exc).__name__})",
                fix=f"reinstall acli ({INSTALL})",
            )
        minimum = ".".join(str(part) for part in MINIMUM_VERSION)
        if found is not None and found < MINIMUM_VERSION:
            return Check(
                name="acli",
                status=Status.DEGRADED,
                detail=f"{path} is acli {'.'.join(str(part) for part in found)}; the adapter needs {minimum} or later",
                fix=f"upgrade acli ({INSTALL})",
            )
        said = ".".join(str(part) for part in found) if found else "of a version it doesn't name"
        return Check(
            name="acli",
            status=Status.UP,
            detail=f"{path}, acli {said}; login is acli's ({LOGINS}), checked on first use: doctor stays offline",
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """Acli on PATH and recent enough, the site, and which capabilities this adapter serves.

        Degraded when acli is missing (its fix names the install and `acli jira auth login`) or older than the
        adapter needs, when the site is not set, and when the plugin is installed but serves no capability. Whether
        acli is logged in is not checked: asking acli could reach Atlassian, and doctor stays offline; a read that
        acli refuses as unauthorized fails with the login command.
        """
        acli = self._acli_check()
        if self._settings.site_url is None:
            site = Check(
                name="site",
                status=Status.DEGRADED,
                detail="atlassian.site_url is not set: the site acli is logged in to, for links",
                fix=SITE_FIX,
            )
        else:
            site = Check(name="site", status=Status.UP, detail=f"{self._settings.site_url}; not contacted")
        checks = [acli, site]
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
