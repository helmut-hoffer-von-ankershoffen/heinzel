"""Settings and the models the plugin's own operations return."""

from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

from heinzel import MailQuery, ModuleSettings

QUERY = "from:zoom.us"
"""The mail query that finds Zoom's summaries: everything Zoom sends; the subject then tells summaries apart."""
SUMMARY_SETTING = (
    "Zoom web portal (zoom.us/profile/setting) > AI Companion > Meeting summary with AI Companion: switch it on and "
    "share summaries by email with yourself (as host, or as an invitee if the host shares them); an account admin "
    "may have to allow it first"
)
"""Where people switch on the summaries the adapter reads; doctor and errors name it."""


class ZoomAbout(BaseModel):
    """What this plugin is, where it runs and where its meetings come from."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    source: str = Field(description="Where meetings come from: Zoom's summary emails, read through `mail`.")
    query: str = Field(description="The mail query that finds the summaries (the `zoom.query` setting).")


class ZoomSettings(ModuleSettings):
    """Settings of the Zoom adapter: the `[zoom]` table of the `hz` config files, or `HZ_ZOOM__<FIELD>`.

    `query` is ignored in a project hz.toml: it decides which mail is read.
    """

    query: str = Field(
        default=QUERY,
        title="Summary mail search",
        description="The mail search that finds Zoom's meeting-summary emails, in your mail's "
        "search syntax; mails that aren't summaries are skipped. Never read from a "
        "project's settings.",
        examples=["from:zoom.us label:Meetings"],
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"query"})

    @field_validator("query")
    @classmethod
    def _a_mail_query(cls, value: str) -> str:
        MailQuery.parse(value)  # a ValueError says what a valid query looks like
        return value.strip()
