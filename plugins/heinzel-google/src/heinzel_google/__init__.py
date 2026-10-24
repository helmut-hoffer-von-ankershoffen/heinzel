"""Read Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It is the default
provider of the `documents` (Docs and Sheets), `calendar` (Google Calendar) and `mail` (Gmail) capabilities for
Google Workspace, in read mode (Heinzelmännchen ADR 0028), backed by the Google Workspace CLI (`gws`), which signs in
and talks to Google (ADR 0035). Bind it with `[capabilities] documents = "google"`, `calendar = "google"` and, if you
want mail read, `mail = "google"` in your user configuration; another plugin providing the same capabilities can be
bound instead.
"""

from typing import Annotated

from pydantic import Field

from heinzel import Check, ConfigEditor, Prompter
from heinzel.kernel import CapabilityView
from heinzel_google._calendar import GoogleCalendar
from heinzel_google._documents import Drive, GoogleDocuments
from heinzel_google._gws import LOGIN, LOGIN_WITH_MAIL, MAIL_SCOPE, SCOPES, GoogleSession, Gws, Process
from heinzel_google._mail import Gmail
from heinzel_google._models import DriveFile, GoogleAbout, GoogleSettings
from heinzel_google._module import module
from heinzel_google._service import GoogleService
from heinzel_google._setup import run_setup

DOCUMENT_HOSTS = ["www.googleapis.com", "docs.googleapis.com", "sheets.googleapis.com", "oauth2.googleapis.com"]
"""Where `documents` reads go, through `gws`: Drive v3 (on www.googleapis.com), Docs v1 and Sheets v4."""
MAIL_HOSTS = ["gmail.googleapis.com", "oauth2.googleapis.com"]
"""Where `mail` reads go, through `gws`: Gmail v1."""
CALENDAR_HOSTS = ["www.googleapis.com", "oauth2.googleapis.com"]
"""Where `calendar` reads go, through `gws`: Calendar v3; every provider's `gws` refreshes its sign-in at
oauth2.googleapis.com."""

module.settings(GoogleSettings)
module.service(GoogleService)
module.service(GoogleSession)
module.service(Drive)
module.service(Gws)
module.provides("documents", modes={"read"}, hosts=DOCUMENT_HOSTS)(GoogleDocuments)
module.provides("calendar", modes={"read"}, hosts=CALENDAR_HOSTS)(GoogleCalendar)
module.provides("mail", modes={"read"}, hosts=MAIL_HOSTS)(Gmail)

Days = Annotated[int, Field(ge=1, le=366, description="How many days back to look for changes, 1 to 366.")]
Name = Annotated[
    str | None, Field(description="Only files whose name contains this text; all Docs and Sheets when not given.")
]


@module.operation
def info(service: GoogleService) -> GoogleAbout:
    """Show this plugin's version, the Heinzelmännchen it runs on and the gws, scopes and APIs it uses."""
    return service.about()


@module.operation
def files(drive: Drive, days: Days = 7, name: Name = None) -> list[DriveFile]:
    """List the Google Docs and Sheets you can open that changed recently, newest first."""
    return drive.recent(days, name)


@module.setup
def setup(  # noqa: PLR0913, PLR0917 - what the step is injected with
    settings: GoogleSettings,
    session: GoogleSession,
    gws: Gws,
    view: CapabilityView,
    prompter: Prompter,
    editor: ConfigEditor,
) -> None:
    """Find the Google Workspace CLI, sign it in read-only and read documents, calendar and, if you like, mail from it.

    Without a terminal, do it by hand: install gws (`npm install -g @googleworkspace/cli`), run
    `gws auth login --scopes` with the read-only scopes `hz google info` lists (and Gmail's for mail), then
    `hz system config set capabilities.documents google`, and the same for `calendar` and `mail`.
    """
    run_setup(settings, session, gws, view, prompter, editor)


@module.info
def system_info(service: GoogleService) -> GoogleAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health
def health(service: GoogleService, view: CapabilityView) -> list[Check]:
    """gws, its sign-in and the bindings; never a request to Google."""
    return service.checks(view)


@module.health(live=True)
def live(service: GoogleService) -> list[Check]:
    """Ask Google who `gws` is signed in as: one read, run only by `hz system connector test google`."""
    return service.live()


__all__ = [
    "CALENDAR_HOSTS",
    "DOCUMENT_HOSTS",
    "LOGIN",
    "LOGIN_WITH_MAIL",
    "MAIL_HOSTS",
    "MAIL_SCOPE",
    "SCOPES",
    "Drive",
    "DriveFile",
    "Gmail",
    "GoogleAbout",
    "GoogleCalendar",
    "GoogleDocuments",
    "GoogleService",
    "GoogleSession",
    "GoogleSettings",
    "Gws",
    "Process",
    "module",
]
