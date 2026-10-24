"""What the plugin says about itself: `hz openmeteo info`, its `hz system info` section and its live check."""

from importlib.metadata import PackageNotFoundError, version

from heinzel import Check, HeinzelError, Kernel, Status, __version__
from heinzel_openmeteo._client import HOSTS, Client
from heinzel_openmeteo._models import OpenMeteoAbout

DISTRIBUTION = "heinzel-openmeteo"
SOURCE = "Weather data by Open-Meteo.com (CC BY 4.0), free for non-commercial use, with no account and no key"
LIVE_PLACE = (52.52, 13.41)
"""Berlin's coordinates, so the live test geocodes nothing and asks about no one's own place."""


class OpenMeteoService:
    """Describes the plugin and tests it live; doctor never asks Open-Meteo anything."""

    def __init__(self, kernel: Kernel, client: Client) -> None:
        """Create the service; the kernel and the client are injected."""
        self._kernel = kernel
        self._client = client

    def about(self) -> OpenMeteoAbout:
        """Describe the plugin, the Heinzelmännchen installation it runs in and where the weather comes from."""
        try:
            installed = version(DISTRIBUTION)
        except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
            installed = "0.0.0"
        return OpenMeteoAbout(
            plugin=DISTRIBUTION,
            version=installed,
            heinzel_version=__version__,
            modules=[r.qualname for r in self._kernel.modules if r.origin.distribution == DISTRIBUTION],
            hosts=list(HOSTS),
            source=SOURCE,
        )

    def live(self) -> list[Check]:
        """Ask Open-Meteo for the weather at fixed coordinates: one read, no place of yours."""
        try:
            self._client.forecast(*LIVE_PLACE, 1)
        except HeinzelError as exc:
            return [Check(name="forecast", status=Status.DOWN, detail=str(exc), fix="check your connection")]
        return [Check(name="forecast", status=Status.UP, detail=f"{HOSTS[0]} answered; no key is needed")]
