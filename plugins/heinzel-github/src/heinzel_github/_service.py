"""What the plugin says about itself: `hz github info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, HeinzelError, Kernel, Login, Status, __version__
from heinzel_github._client import GhClient, GhLoginError, said
from heinzel_github._gh import INSTALL, MINIMUM_VERSION, Gh, login_fix, version_text
from heinzel_github._models import GithubAbout, GithubSettings

DISTRIBUTION = "heinzel-github"
ADAPTER = "github"
CAPABILITIES = ("code-host", "tracker")
LIVE_TIMEOUT = 15.0
"""Seconds the live test's one call may take: it asks GitHub who gh is logged in as, nothing more."""


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class GithubService:
    """Describes the plugin and checks its setup; doctor runs only `gh --version` and reads gh's hosts file."""

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
            gh=self._gh.executable(),
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """Whether `gh` is installed and logged in, and which capabilities this adapter serves.

        Degraded when `gh` is missing, older than the adapter needs or has no login for the host, and when the
        adapter is installed but serves nothing. The login is looked up in gh's hosts file and the token variables gh
        reads, never by running a command that prints a token or asks GitHub (ADR 0035).
        """
        return [self._gh_check(), *self._binding_checks(bindings)]

    def _gh_check(self) -> Check:
        host, name = self._settings.host, self._settings.gh
        unknown = Login(signed_in=None, site=host, command=login_fix(host))
        if (path := self._gh.executable()) is None:
            return Check(
                name="gh",
                status=Status.DEGRADED,
                detail=f"{name}, the GitHub CLI, is not on PATH; every read goes through it",
                fix=f"install gh ({INSTALL}), then {login_fix(host)}",
                login=unknown,
            )
        try:
            found = self._gh.version()
        except HeinzelError as exc:
            return Check(
                name="gh", status=Status.DEGRADED, detail=str(exc), fix=f"reinstall gh ({INSTALL})", login=unknown
            )
        if found is not None and found < MINIMUM_VERSION:
            return Check(
                name="gh",
                status=Status.DEGRADED,
                detail=f"{path} is gh {version_text(found)}; "
                f"the adapter needs {version_text(MINIMUM_VERSION)} or later",
                fix=f"upgrade gh ({INSTALL})",
                login=unknown,
            )
        shown = f"gh {version_text(found)}" if found else "gh of a version it doesn't name"
        if not self._gh.logged_in():
            return Check(
                name="gh",
                status=Status.DEGRADED,
                detail=f"{path}, {shown}, has no login for {host}",
                fix=login_fix(host),
                login=Login(signed_in=False, site=host, command=login_fix(host)),
            )
        return Check(
            name="gh",
            status=Status.UP,
            detail=f"{path}, {shown}, has a login for {host}; checked on first use: doctor stays offline",
            login=Login(signed_in=True, account=self._gh.account(), site=host, command=login_fix(host)),
        )

    def live(self) -> list[Check]:
        """Ask GitHub who gh is logged in as (`gh api user`), within `LIVE_TIMEOUT`: one read, nothing written."""
        host = self._settings.host
        client = GhClient(self._gh, host, min(LIVE_TIMEOUT, self._settings.timeout))
        try:
            user = client.get("user")
        except GhLoginError as exc:
            return [
                Check(
                    name="gh api user",
                    status=Status.DEGRADED,
                    detail=str(exc),
                    fix=login_fix(host),
                    login=Login(signed_in=False, site=host, command=login_fix(host)),
                )
            ]
        except HeinzelError as exc:
            fix = f"gh auth status --hostname {host}"
            return [Check(name="gh api user", status=Status.DOWN, detail=str(exc), fix=fix)]
        login = user.get("login")
        account = said(login) if isinstance(login, str) and login.strip() else ""
        return [
            Check(
                name="gh api user",
                status=Status.UP,
                detail=f"GitHub ({host}) answered: gh is logged in as {account or 'an account it does not name'}",
                login=Login(signed_in=True, account=account, site=host, command=login_fix(host)),
            )
        ]

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
