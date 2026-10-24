"""The org module: who you are to hz and, later, whom you work with: your team, org and company (ADR 0024, 0033).

Your profile (handle, role, time zone, working hours, GitHub login, coding agent) is asked once by `hz setup` and kept
in your user configuration, `[org]`; nothing about you is written to a repository by this module. Other modules ask
for `ProfileService` by type to learn who is acting.
"""

from typing import Annotated

from pydantic import Field

from heinzel import Check, ConfigEditor, Module, Prompter, Status
from heinzel.modules.org._models import ROLES, OrgSettings, Profile
from heinzel.modules.org._profile import ProfileService, ask_profile
from heinzel.modules.org._render import render_profile
from heinzel.modules.org._sync import SyncItem, SyncReport, SyncService, render_sync

module = Module("org", "Your profile, and the team, org and company you work with.")
module.settings(OrgSettings)
module.service(ProfileService)
module.service(SyncService)


# Your name and email are in it; a web page can reach a local REST server, so the CLI and MCP get it, like drafts.
@module.operation(render=render_profile, heads={"cli", "mcp"})
def show(service: ProfileService) -> Profile:
    """Show your profile: who you are, your role, time zone and working hours, and what is still missing."""
    return service.profile()


@module.health
def profile(service: ProfileService, recommendations: SyncService) -> list[Check]:
    """Your profile names your handle, role and time zone; and drift from what your team recommends."""
    found = service.profile()
    drift = recommendations.health()
    if not found.complete:
        return [
            Check(
                name="profile",
                status=Status.DEGRADED,
                detail=f"missing {', '.join(found.missing)}",
                fix=f"Run `{found.fix}`",
            ),
            *drift,
        ]
    detail = f"{found.handle}, {found.role}, {found.timezone} {found.working_hours}"
    return [Check(name="profile", status=Status.UP, detail=detail), *drift]


# It writes your configuration on your say-so; a web page can reach a local REST server, so the CLI and MCP get it.
@module.operation(read_only=False, render=render_sync, heads={"cli", "mcp"})
def sync(
    service: SyncService,
    accept: Annotated[
        str | None,
        Field(description="Recommendations to apply, by id and comma-separated (binding:documents), or all."),
    ] = None,
) -> SyncReport:
    """Show what your team recommends beside what you have, and apply to your user file only what you accept."""
    return service.sync(accept)


@module.setup(required=True)
def setup(settings: OrgSettings, prompter: Prompter, editor: ConfigEditor) -> None:
    """Tell hz who you are; what your gh and gws logins and this machine say is found, the rest is asked once."""
    prompter.say("Your profile is kept on this machine only; nothing goes into a repository.")
    path = ask_profile(settings, prompter, editor)
    prompter.say(
        f"Saved to {path}. `hz org show` shows it; correct one part with e.g. `hz system config set org.role engineer`."
    )


__all__ = ["ROLES", "OrgSettings", "Profile", "ProfileService", "SyncItem", "SyncReport", "SyncService", "module"]
