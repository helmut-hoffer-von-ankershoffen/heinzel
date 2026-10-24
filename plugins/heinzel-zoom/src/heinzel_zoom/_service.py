"""What the plugin says about itself: `hz zoom info`, its `hz system info` section and its doctor checks."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from heinzel import Check, Kernel, Status, __version__
from heinzel_zoom._models import SUMMARY_SETTING, ZoomAbout, ZoomSettings

DISTRIBUTION = "heinzel-zoom"
ADAPTER = "zoom"
SOURCE = "Zoom's meeting-summary emails, read through the adapter bound to mail"


class Bindings(Protocol):
    """What the checks need to know about bindings; `heinzel.kernel.CapabilityView` is one."""

    def bound(self, capability: str) -> str | None:
        """The adapter bound to `capability`, or None."""
        ...


class ZoomService:
    """Describes the plugin and checks its setup, without reading any mail (doctor stays offline)."""

    def __init__(self, kernel: Kernel, settings: ZoomSettings) -> None:
        """Create the service; the kernel and the plugin's settings are injected."""
        self._kernel = kernel
        self._settings = settings

    def about(self) -> ZoomAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in and where meetings come from."""
        try:
            installed = version(DISTRIBUTION)
        except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
            installed = "0.0.0"
        return ZoomAbout(
            plugin=DISTRIBUTION,
            version=installed,
            heinzel_version=__version__,
            modules=[r.qualname for r in self._kernel.modules if r.origin.distribution == DISTRIBUTION],
            source=SOURCE,
            query=self._settings.query,
        )

    def checks(self, bindings: Bindings) -> list[Check]:
        """Where the summaries come from and must be switched on, the mailbox they are read from, and the binding.

        Whether Zoom sends summaries can only be seen in Zoom and in the mailbox, so doctor says where to switch them
        on. An unbound `mail` is also reported by `system.capability`, as an unmet requirement of this module.
        """
        checks = [
            Check(
                name="summaries",
                status=Status.UP,
                detail=f"meetings come from Zoom's summary emails matching `{self._settings.query}`; Zoom must send "
                f"them to this mailbox: {SUMMARY_SETTING}. Not checked: no mail is read",
            )
        ]
        mail = bindings.bound("mail")
        if mail:
            checks.append(Check(name="mail", status=Status.UP, detail=f"summaries are read through {mail}"))
        else:
            checks.append(
                Check(
                    name="mail",
                    status=Status.DEGRADED,
                    detail="no mail adapter bound, so no summary can be read",
                    fix="hz system config set capabilities.mail <a mail adapter, e.g. google>",
                )
            )
        adapter = bindings.bound("meetings")
        if adapter == ADAPTER:
            checks.append(Check(name="meetings", status=Status.UP, detail=f"served by {ADAPTER}"))
        else:
            # The CLI renders text as Rich markup, which swallows a literal `[capabilities]`: name the command.
            detail = f"served by {adapter}, not {ADAPTER}" if adapter else "not bound"
            checks.append(Check(name="meetings", status=Status.UP, detail=detail))
            checks.append(
                Check(
                    name="bindings",
                    status=Status.DEGRADED,
                    detail=f"{DISTRIBUTION} is installed but serves no capability",
                    fix=f"hz system config set capabilities.meetings {ADAPTER}",
                )
            )
        return checks
