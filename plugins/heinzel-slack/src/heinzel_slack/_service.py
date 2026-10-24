"""What the plugin says about itself: `hz slack info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, Credentials, Kernel, Status, __version__
from heinzel_slack._client import TOKEN_FIX
from heinzel_slack._models import READ_SCOPES, WRITE_SCOPES, SlackAbout, SlackSettings
from heinzel_slack._module import TOKEN

DISTRIBUTION = "heinzel-slack"
ADAPTER = "slack"
CAPABILITY = "chat"


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class SlackService:
    """Describes the plugin and checks its setup, without ever sending a request (doctor stays offline)."""

    def __init__(self, kernel: Kernel, settings: SlackSettings, credentials: Credentials) -> None:
        """Create the service; the kernel, the plugin's settings and the credentials are injected."""
        self._kernel = kernel
        self._settings = settings
        self._credentials = credentials

    def about(self) -> SlackAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in and the API it talks to."""
        try:
            installed = version(DISTRIBUTION)
        except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
            installed = "0.0.0"
        return SlackAbout(
            plugin=DISTRIBUTION,
            version=installed,
            heinzel_version=__version__,
            modules=[r.qualname for r in self._kernel.modules if r.origin.distribution == DISTRIBUTION],
            api_url=self._settings.api_url,
            token_kind=self._settings.token_kind,
            read_scopes=list(READ_SCOPES),
            write_scopes=list(WRITE_SCOPES),
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """The API and token in use, and whether `chat` is bound to this adapter.

        Degraded when no token is stored (there is no anonymous access) and when the plugin is installed but serves
        no capability. Whether the token has the scopes is only known when Slack is asked, which doctor never does.
        """
        kind = self._settings.token_kind
        if self._credentials.has(TOKEN):
            checks = [
                Check(
                    name="api",
                    status=Status.UP,
                    detail=f"{self._settings.api_url}, with a {kind} token; not contacted, scopes not checked",
                )
            ]
        else:
            checks = [
                Check(
                    name="api",
                    status=Status.DEGRADED,
                    detail=f"needs a Slack {kind} token; no anonymous access",
                    fix=TOKEN_FIX,
                )
            ]
        adapter = bindings.bound(CAPABILITY)
        # The CLI renders text as Rich markup, which swallows a literal `[capabilities]`: name the command.
        fix = f"hz system config set capabilities.{CAPABILITY} {ADAPTER}"
        if adapter == ADAPTER:
            checks.append(Check(name=CAPABILITY, status=Status.UP, detail=f"served by {ADAPTER}"))
        elif adapter:
            checks.append(Check(name=CAPABILITY, status=Status.UP, detail=f"served by {adapter}, not {ADAPTER}"))
        else:
            checks.append(Check(name=CAPABILITY, status=Status.UP, detail=f"not bound; to use Slack: {fix}"))
        if adapter != ADAPTER:
            checks.append(
                Check(
                    name="bindings",
                    status=Status.DEGRADED,
                    detail=f"{DISTRIBUTION} is installed but serves no capability",
                    fix=fix,
                )
            )
        return checks
