"""The team module's `OrgContext`: your team's checkout, as `[team]` names it, and the layer your team is."""

from pathlib import Path
from typing import override

from heinzel.kernel import OrgContext, Recommendation
from heinzel.modules.team._models import TeamSettings
from heinzel.modules.team._service import TeamService


class TeamContext(OrgContext):
    """Your team's checkout, read through the team service, so a `join` in this process counts at once."""

    def __init__(self, service: TeamService, settings: TeamSettings) -> None:
        """Read the checkout from `service` and your choice of layer from `settings`."""
        self._service = service
        self._settings = settings

    @override
    def team_checkout(self) -> Path | None:
        """The checkout `[team]` names, or where `join` puts the configured repository; None without a team."""
        return self._service.checkout

    @override
    def recommendations(self) -> tuple[Recommendation, ...]:
        """What your team's `hz.yaml` recommends; nothing without a team or with an invalid manifest."""
        return self._service.recommendations()

    @override
    def layer(self) -> str | None:
        """`[team] layer` when you set it, else the plugin your team repository's `hz.yaml` names.

        The kernel finds the layer by its name or by its plugin's, so a plugin whose layer module is named otherwise
        is found too.

        Raises:
            HeinzelError: If the team repository's manifest is missing, unreadable or invalid.
        """
        if self._settings.layer is not None:
            return self._settings.layer
        manifest = self._service.manifest()
        if manifest is None or manifest.plugin is None:
            return None
        return manifest.plugin.name
