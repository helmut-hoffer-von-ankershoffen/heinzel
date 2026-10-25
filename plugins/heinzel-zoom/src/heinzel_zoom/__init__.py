"""Read meetings from Zoom's meeting-summary emails: the meetings capability.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It provides the
adapter `zoom` for the capability `meetings` in read mode (ADR 0028), built on the `mail` capability: it reads the
summaries Zoom's AI Companion mails after a meeting through whichever mail adapter is bound, with no Zoom API and no
Zoom credentials. It serves `meetings` automatically while it is the only provider (ADR 0055), or bind it with
`[capabilities] meetings = "zoom"`; `mail` needs a binding of its own.
"""

from heinzel import Check, Mail
from heinzel.kernel import CapabilityView
from heinzel_zoom._adapter import Zoom
from heinzel_zoom._models import QUERY, SUMMARY_SETTING, ZoomAbout, ZoomSettings
from heinzel_zoom._module import module
from heinzel_zoom._service import ZoomService

module.settings(ZoomSettings)
module.service(ZoomService)
# No hosts: the adapter sends nothing anywhere; the mail adapter it reads through declares its own.
module.provides("meetings", modes={"read"}, hosts=())(Zoom)


@module.operation
def info(service: ZoomService) -> ZoomAbout:
    """Show this plugin's version, the Heinzelmännchen it runs on and where its meetings come from."""
    return service.about()


@module.info
def system_info(service: ZoomService) -> ZoomAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health
def health(service: ZoomService, view: CapabilityView) -> list[Check]:
    """Where summaries come from and must be switched on, the mail binding and this adapter's; reads no mail."""
    return service.checks(view)


@module.health(live=True)
def live(service: ZoomService, mail: Mail) -> list[Check]:
    """List the last day's summary emails through the bound mail adapter; run only by `hz system connector test`."""
    return service.live(mail)


__all__ = ["QUERY", "SUMMARY_SETTING", "Zoom", "ZoomAbout", "ZoomService", "ZoomSettings", "module"]
