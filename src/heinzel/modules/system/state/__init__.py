"""The state module: what this machine says about using hz, pushed to your own state repository (ADR 0081).

Two local streams, both strict metadata: `presence`, one line for each local day on which hz was used here (a
command, a tool call, the web app; never the daemon's own ticks), and `adoption`, a line when hz is first seen and
one for each new version. They stay on this machine until you name a private repository of your own in
`[system.state] personal`; then the daemon's `push` job copies what is new to this machine's writer branch,
`hz/<your handle>/<machine>`, every 15 minutes, fast-forward only. The team sink, behind its own consent, follows.
"""

from pydantic import JsonValue

from heinzel import Check, CurrentUser, HeinzelError, JobInactive, Module
from heinzel.kernel import Presence
from heinzel.modules.system.state._models import (
    AdoptionLine,
    PresenceLine,
    Pushed,
    StateSettings,
    StateStatus,
    StreamStatus,
)
from heinzel.modules.system.state._render import render_pushed, render_status
from heinzel.modules.system.state._service import LocalPresence, StateService

module = Module("state", "Note how hz is used on this machine and push it to your own state repository.")
module.settings(StateSettings)
module.service(StateService, StateService.for_user)
module.service(Presence, LocalPresence)


@module.operation(render=render_status, private=True)
def status(service: StateService) -> StateStatus:
    """Show where this machine's state goes, what is still to push and how the last push went."""
    return service.status()


@module.operation(read_only=False, render=render_pushed, private=True)
def push(service: StateService) -> Pushed:
    """Push this machine's new presence and adoption lines to your personal state repository now."""
    return service.push()


@module.operation(read_only=False, render=render_status, private=True)
def reset(service: StateService) -> StateStatus:
    """Start this machine's clone of its branch afresh after a refused push; the old one is kept aside."""
    return service.reset()


@module.job(schedule="*/15 * * * *", name="push", timeout="5m")
def push_on_schedule(service: StateService) -> JsonValue | JobInactive:
    """Push new presence and adoption lines to your personal state repository, if you named one."""
    return service.scheduled()


@module.health
def health(service: StateService, user: CurrentUser) -> list[Check]:
    """Whether pushes to your personal state repository work."""
    checks = service.health()
    try:
        permitted = "private:read" in user.context().permissions
    except HeinzelError:
        permitted = False
    if permitted:
        return checks
    return [
        Check(
            name=check.name,
            status=check.status,
            detail="personal state: use authenticated state status for details",
            fix="hz system state status",
        )
        for check in checks
    ]


__all__ = [
    "AdoptionLine",
    "LocalPresence",
    "PresenceLine",
    "Pushed",
    "StateService",
    "StateSettings",
    "StateStatus",
    "StreamStatus",
    "module",
]
