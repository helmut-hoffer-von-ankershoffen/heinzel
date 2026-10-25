"""What the plugin says about itself: `hz atlassian info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol
from urllib.parse import urlsplit

from heinzel import Check, HeinzelError, Kernel, Login, Status, __version__
from heinzel_atlassian._acli import MINIMUM_VERSION, Acli, AcliTimeoutError
from heinzel_atlassian._client import INSTALL, LOGIN, SITE_FIX, AcliClient, AcliLoginError, Product
from heinzel_atlassian._models import AtlassianAbout, AtlassianSettings

DISTRIBUTION = "heinzel-atlassian"
ADAPTER = "atlassian"
CAPABILITIES = ("tracker", "documents")
COMMANDS = [
    "acli jira workitem search --jql <jql> --fields <fields> --paginate --json",
    "acli confluence page view --id <id> --body-format storage --json",
]
LOGINS = f"{LOGIN['Jira']}; {LOGIN['Confluence']}"
GATEWAY_NOTE = "with a browser login, acli's answers name no site to match"
LIVE_TIMEOUT = 15.0
"""Seconds each live read may take: one Jira issue, or one Confluence space, nothing more."""
PROBES: dict[Product, list[str]] = {
    "Jira": [
        "jira",
        "workitem",
        "search",
        "--jql",
        "updated >= -30d ORDER BY updated DESC",
        "--limit",
        "1",
        "--fields",
        "key",
        "--json",
    ],
    "Confluence": ["confluence", "space", "list", "--limit", "1", "--json"],
}
"""The live test's reads: the cheapest each product has that needs a working login and prints JSON."""
PRODUCTS: dict[str, Product] = {"tracker": "Jira", "documents": "Confluence"}


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class AtlassianService:
    """Describes the plugin and checks its setup; doctor runs only `acli --version`, a local command."""

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

    def _site(self) -> str:
        return urlsplit(self._settings.site_url).hostname or "" if self._settings.site_url else ""

    def products(self, bindings: Bindings) -> list[Product]:
        """The products the adapters serve now (Jira for tracker, Confluence for documents); Jira if neither."""
        served = [product for capability, product in PRODUCTS.items() if bindings.bound(capability) == ADAPTER]
        return served or ["Jira"]

    def _acli_check(self, bindings: Bindings) -> Check:
        """Where acli is and which version it says it is, from `acli --version`; the login is unknown offline."""
        path = self._acli.path()
        login = Login(signed_in=None, site=self._site(), command=LOGIN[self.products(bindings)[0]])
        if path is None:
            return Check(
                name="acli",
                status=Status.DEGRADED,
                detail=f"{self._settings.acli}, Atlassian's CLI, is not on PATH; every read goes through it",
                fix=f"install acli ({INSTALL}), then {LOGINS}",
                login=login,
            )
        try:
            found = self._acli.version()
        except (AcliTimeoutError, OSError, RuntimeError) as exc:  # RuntimeError: no home directory
            return Check(
                name="acli",
                status=Status.DEGRADED,
                detail=f"{path} did not answer `acli --version` ({type(exc).__name__})",
                fix=f"reinstall acli ({INSTALL})",
                login=login,
            )
        minimum = ".".join(str(part) for part in MINIMUM_VERSION)
        if found is not None and found < MINIMUM_VERSION:
            return Check(
                name="acli",
                status=Status.DEGRADED,
                detail=f"{path} is acli {'.'.join(str(part) for part in found)}; the adapter needs {minimum} or later",
                fix=f"upgrade acli ({INSTALL})",
                login=login,
            )
        said = ".".join(str(part) for part in found) if found else "of a version it doesn't name"
        return Check(
            name="acli",
            status=Status.UP,
            detail=f"{path}, acli {said}; login is acli's ({LOGINS}), checked on first use: doctor stays offline",
            login=login,
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """Acli on PATH and recent enough, the site, and which capabilities this adapter serves.

        Degraded when acli is missing (its fix names the install and `acli jira auth login`) or older than the
        adapter needs, when the site is not set, and when the plugin is installed but serves no capability. Whether
        acli is logged in is not checked: asking acli could reach Atlassian, and doctor stays offline; a read that
        acli refuses as unauthorized fails with the login command.
        """
        acli = self._acli_check(bindings)
        if self._settings.site_url is None:
            site = Check(
                name="site",
                status=Status.DEGRADED,
                detail="atlassian.site_url is not set: the site acli is logged in to, for links",
                fix=SITE_FIX,
            )
        else:
            site = Check(
                name="site",
                status=Status.UP,
                detail=f"{self._settings.site_url}; not contacted; {GATEWAY_NOTE}",
            )
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

    def live(self, bindings: Bindings) -> list[Check]:
        """One read per product the adapters serve, through acli, within `LIVE_TIMEOUT`; nothing is written."""
        client = AcliClient(self._acli, min(LIVE_TIMEOUT, self._settings.timeout))
        checks: list[Check] = []
        for product in self.products(bindings):
            name, command = f"{product} read", LOGIN[product]
            try:
                answer = client.json(product, PROBES[product], f"a {product} read")
            except AcliLoginError as exc:
                login = Login(signed_in=False, site=self._site(), command=command)
                checks.append(Check(name=name, status=Status.DEGRADED, detail=str(exc), fix=command, login=login))
                continue
            except HeinzelError as exc:
                checks.append(
                    Check(name=name, status=Status.DOWN, detail=str(exc), fix=f"acli {product.lower()} auth status")
                )
                continue
            what = "a work item" if product == "Jira" else "a space"
            found = "answered" if answer else "answered with nothing"
            checks.append(
                Check(
                    name=name,
                    status=Status.UP,
                    detail=f"acli read {what} from {product} ({' '.join(PROBES[product][:3])}): {product} {found}",
                    login=Login(signed_in=True, site=self._site(), command=command),
                )
            )
        return checks
