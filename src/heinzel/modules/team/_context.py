"""The team module's `OrgContext`: your team's checkout, as `[team]` names it."""

from pathlib import Path
from typing import override

from heinzel.kernel import OrgContext
from heinzel.modules.team._service import TeamService


class TeamContext(OrgContext):
    """Your team's checkout, read through the team service, so a `join` in this process counts at once."""

    def __init__(self, service: TeamService) -> None:
        """Read the checkout from `service`."""
        self._service = service

    @override
    def team_checkout(self) -> Path | None:
        """The checkout `[team]` names, or where `join` puts the configured repository; None without a team."""
        return self._service.checkout
