"""What the plugin says about itself: `hz google info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, HeinzelError, Kernel, Status, __version__
from heinzel_google._gws import SCOPES, SETUP, GoogleSession
from heinzel_google._models import GoogleAbout

DISTRIBUTION = "heinzel-google"
ADAPTER = "google"
CAPABILITIES = ("documents", "calendar", "mail")
APIS = [
    "Google Docs API v1 (documents.get)",
    "Google Sheets API v4 (spreadsheets.get)",
    "Google Drive API v3 (files.get, files.list; metadata only)",
    "Google Calendar API v3 (events.list)",
    "Gmail API v1 (users.messages.list, users.messages.get, users.labels.list; only when mail is bound)",
]


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class GoogleService:
    """Describes the plugin and checks its setup; doctor asks `gws` only for its local sign-in state."""

    def __init__(self, kernel: Kernel, session: GoogleSession) -> None:
        """Create the service; the kernel and the session are injected."""
        self._kernel = kernel
        self._session = session

    def about(self) -> GoogleAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in and the `gws` it uses."""
        try:
            installed = version(DISTRIBUTION)
        except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
            installed = "0.0.0"
        try:
            gws: str | None = self._session.executable()
        except HeinzelError:
            gws = None
        return GoogleAbout(
            plugin=DISTRIBUTION,
            version=installed,
            heinzel_version=__version__,
            modules=[r.qualname for r in self._kernel.modules if r.origin.distribution == DISTRIBUTION],
            backend="Google Workspace CLI (gws)",
            gws=gws,
            scopes=list(SCOPES),
            apis=APIS,
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """`gws` and its sign-in, and which capabilities this adapter serves; never a request to Google.

        Degraded when `gws` is missing or has no credentials, and when the plugin is installed but serves no
        capability. Whether Google still accepts the sign-in shows on the first read.
        """
        try:
            signed_in, detail = self._session.signed_in()
        except HeinzelError as exc:
            checks = [Check(name="gws", status=Status.DEGRADED, detail=str(exc), fix=SETUP)]
        else:
            if signed_in:
                checks = [
                    Check(name="gws", status=Status.UP, detail=f"{detail}; checked locally, Google not contacted")
                ]
            else:
                checks = [
                    Check(
                        name="gws",
                        status=Status.DEGRADED,
                        detail=f"{detail}; checked locally, Google not contacted",
                        fix=SETUP,
                    )
                ]
        bound = {capability: bindings.bound(capability) for capability in CAPABILITIES}
        for capability, adapter in bound.items():
            if adapter == ADAPTER:
                detail = f"served by {ADAPTER}"
            elif adapter:
                detail = f"served by {adapter}, not {ADAPTER}"
            else:
                detail = f"not bound; to use Google: hz system config set capabilities.{capability} {ADAPTER}"
            checks.append(Check(name=capability, status=Status.UP, detail=detail))
        if ADAPTER not in bound.values():
            checks.append(
                Check(
                    name="bindings",
                    status=Status.DEGRADED,
                    detail=f"{DISTRIBUTION} is installed but serves no capability",
                    fix=SETUP,
                )
            )
        return checks
