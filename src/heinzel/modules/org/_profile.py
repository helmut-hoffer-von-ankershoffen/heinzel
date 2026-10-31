"""Your profile: what the identity step finds, what it still asks, and where it writes (the perfect-onboarding ADR).

Identity is found, not asked: handle and GitHub login from `gh`, name and email from `gws`, the time zone from this
machine, the coding agent from your PATH (`_discovery`). A first run asks only what can't be found: always your role
and working hours, and the handle or time zone when nothing was found. Changing a profile asks everything, with what
is configured as the defaults. The answers go to your user configuration, `[org]`, through `ConfigEditor`, so the
step can't write what `hz system config set` would refuse.
"""

from contextlib import suppress
from pathlib import Path
from zoneinfo import available_timezones

from heinzel import ConfigEditor, HeinzelError, Prompter
from heinzel.modules.org import _discovery
from heinzel.modules.org._discovery import Discovered
from heinzel.modules.org._models import (
    CODING_AGENTS,
    DEFAULT_HOURS,
    ROLE_LABELS,
    ROLE_MEANINGS,
    ROLES,
    CodingAgent,
    OrgSettings,
    Profile,
    format_hours,
    github_problem,
    handle_problem,
    parse_hours,
)

NONE = "none"
MAX_MATCHES = 15
"""More matches than this, and the step asks for more of the time zone's name instead of listing them."""


class ProfileService:
    """Your profile, for `hz org show`, the doctor check and modules that need to know who you are."""

    def __init__(self, settings: OrgSettings) -> None:
        """Read the profile from `settings`."""
        self._settings = settings

    def profile(self) -> Profile:
        """The profile as configured, with what is missing."""
        return Profile.of(self._settings)

    def require(self) -> Profile:
        """The profile, complete; for operations that act as you, such as joining a team.

        Raises:
            HeinzelError: If handle, role or time zone are missing, naming them and how to set them.
        """
        profile = self.profile()
        if not profile.complete:
            msg = f"Your profile is missing {', '.join(profile.missing)}; run `hz setup` to set it."
            raise HeinzelError(msg)
        return profile


def default_hours(timezone: str | None) -> tuple[int, int]:
    """The working hours suggested for a time zone; one default everywhere until teams say otherwise."""
    del timezone
    return DEFAULT_HOURS


def ask_profile(settings: OrgSettings, prompter: Prompter, editor: ConfigEditor) -> Path:
    """Find what can be found, ask the rest, show what will be kept, and write it to the user configuration."""
    prompter.say("Looking up your gh and gws logins...")
    found = _discovery.discover()
    change = Profile.of(settings).complete
    if not change:
        _say_found(prompter, found)
    values = _ask(settings, found, prompter, change=change)
    kept = {key: value for key, value in values.items() if value is not None}
    prompter.say("hz keeps this about you, on this machine only ([org] of your user configuration):")
    for key, value in kept.items():
        shown = format_hours((value[0], value[1])) if isinstance(value, list) else value
        prompter.say(f"  {key:<13} {shown}")
    path = Path()
    for key, value in kept.items():
        path = editor.write(f"org.{key}", value)
    if change and values["github"] is None and settings.github is not None:
        with suppress(HeinzelError):  # set where setup doesn't write: a site file or an HZ_* variable
            path = editor.unset("org.github")
    return path


def _ask(
    settings: OrgSettings, found: Discovered, prompter: Prompter, *, change: bool
) -> dict[str, list[int] | str | None]:
    login = settings.github or (found.github.id if found.github else None)
    handle = settings.handle or _handle_from(login)
    if change or handle is None or prompter.has_preset("handle"):
        handle = prompter.text("Handle, e.g. mira", default=handle, validate=handle_problem, key="handle")
    role = settings.role
    if change or role is None or prompter.has_preset("role"):
        if settings.retired_role:
            prompter.say(f"Your saved role {settings.retired_role!r} is no longer offered; choose a new one.")
        for name, meaning in ROLE_MEANINGS.items():
            prompter.say(f"  {ROLE_LABELS[name]} ({name}): {meaning}")
        role = prompter.choice("Your role", ROLES, default=role if role in ROLES else None, key="role")
    timezone = settings.timezone or found.timezone
    if change or timezone is None or prompter.has_preset("timezone"):
        timezone = ask_timezone(prompter, timezone)
    hours = settings.working_hours if "working_hours" in settings.model_fields_set else default_hours(timezone)
    if change or "working_hours" not in settings.model_fields_set or prompter.has_preset("working_hours"):
        hours = parse_hours(
            prompter.text("Working hours", default=format_hours(hours), validate=_hours_problem, key="working_hours")
        )
    github = login
    if change or prompter.has_preset("github"):  # a first run doesn't ask: it matters only once you join a team
        answer = prompter.text(
            f"GitHub login, {NONE} for none", default=github or NONE, validate=_github_problem, key="github"
        )
        github = None if answer.lower() == NONE else answer
    agent = settings.coding_agent or found.coding_agent
    if change or prompter.has_preset("coding_agent"):
        agent = _ask_agent(prompter, agent)
    return {
        "handle": handle,
        "role": role,
        "timezone": timezone,
        "working_hours": list(hours),
        "github": github,
        "coding_agent": agent,
        "name": settings.name or _name(found),
        "email": settings.email or (found.google.id if found.google else None),
    }


def _say_found(prompter: Prompter, found: Discovered) -> None:
    def source(key: str) -> str:
        return found.sources.get(key, "found")

    lines: list[str] = []
    if found.github:
        lines.append(f"  GitHub login  {found.github.id} ({source('github')})")
    if found.google:
        lines.append(f"  Google login  {found.google.id} ({source('google')})")
    if found.timezone:
        lines.append(f"  time zone     {found.timezone} ({source('timezone')})")
    if found.coding_agent != NONE:
        lines.append(f"  coding agent  {found.coding_agent} ({source('coding_agent')})")
    if not lines:
        prompter.say("No gh or gws login found; a few questions instead.")
        return
    prompter.say("Found about you:")
    for line in lines:
        prompter.say(line)


def _handle_from(login: str | None) -> str | None:
    return login.lower() if login and handle_problem(login.lower()) is None else None


def _name(found: Discovered) -> str | None:
    return (found.google.name if found.google else None) or (found.github.name if found.github else None)


KEY = "timezone"


def ask_timezone(prompter: Prompter, default: str | None) -> str:
    """An IANA time zone, found by part of its name (`berlin`, `new york`); an empty answer takes `default`."""
    while True:
        query = prompter.text("Time zone, a city or region such as Berlin", default=default, key=KEY)
        if query in available_timezones():
            return query
        matches = search(query)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            prompter.refuse(f"No time zone matches {query!r}; try a city such as Lisbon or a region, Europe.", KEY)
        elif len(matches) > MAX_MATCHES or prompter.has_preset(KEY):  # a preset can't pick among several
            prompter.refuse(f"{len(matches)} time zones match {query!r}; give more of the name.", KEY)
        else:
            return prompter.choice("Which one?", matches)


def search(query: str) -> list[str]:
    """IANA time zone names containing `query`, ignoring case; spaces match underscores (`new york`)."""
    needle = query.strip().lower().replace(" ", "_")
    return sorted(name for name in available_timezones() if needle in name.lower())


def _github_problem(answer: str) -> str | None:
    return None if answer.lower() == NONE else github_problem(answer)


def _hours_problem(answer: str) -> str | None:
    try:
        parse_hours(answer)
    except ValueError as exc:
        return str(exc)
    return None


def _ask_agent(prompter: Prompter, current: CodingAgent) -> CodingAgent:
    prompter.say("claude-code: Claude Code; command: a command of your own; none: none yet")
    answer = prompter.choice("Coding agent you draft with", CODING_AGENTS, default=current, key="coding_agent")
    return next(agent for agent in CODING_AGENTS if agent == answer)
