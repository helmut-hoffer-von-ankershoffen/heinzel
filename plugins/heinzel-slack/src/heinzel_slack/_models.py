"""Settings and the models the plugin's own operations return."""

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from heinzel import ModuleSettings

SLACK_API = "https://slack.com/api"
_API = re.compile(r"^https://(?:[a-z0-9-]+\.)*slack\.com/api$")

READ_SCOPES = ("channels:history", "groups:history", "channels:read", "groups:read", "users:read", "users:read.email")
"""Scopes `messages` uses: history and names of public and private channels, and who posted (email is optional)."""
WRITE_SCOPES = ("chat:write",)
"""The one scope `post` needs, and only `post`."""

TokenKind = Literal["user", "bot"]


class SlackAbout(BaseModel):
    """What this plugin is, where it runs and which Slack API it talks to."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    api_url: str = Field(description="The Slack Web API the adapter sends requests to.")
    token_kind: TokenKind = Field(description="Which kind of Slack token the adapter expects.")
    read_scopes: list[str] = Field(description="Token scopes reading messages needs.")
    write_scopes: list[str] = Field(description="Token scopes posting needs, on top of the read scopes.")


class SlackSettings(ModuleSettings):
    """Settings of the Slack adapter: the `[slack]` table of the `hz` config files, or `HZ_SLACK__<FIELD>`.

    `api_url` and `token_kind` are ignored in a project hz.toml: they decide where the token goes and as whom.
    """

    api_url: str = Field(
        default=SLACK_API,
        description="Base URL of the Slack Web API, `https://slack.com/api`; only an https URL on slack.com or one of "
        "its subdomains is accepted. Only the site, user and HZ_CONFIG files and the environment can set it, never a "
        "project hz.toml, because the token goes there.",
    )
    token_kind: TokenKind = Field(
        default="user",
        description="`user`: a user token (xoxp-...) that reads and posts as you, with your access to channels; "
        "`bot`: a bot token (xoxb-...) that sees only channels the bot was added to and posts as the bot. Never read "
        "from a project hz.toml.",
    )
    thread_lookback_days: int = Field(
        default=30,
        ge=0,
        le=365,
        description="How many days before a window `messages` looks for threads with replies inside it: a reply "
        "posted in the window to a parent older than this is not found. Larger reads more history.",
    )
    timeout: float = Field(
        default=30.0, gt=0, le=300, description="Seconds to wait for each answer from Slack before giving up."
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"api_url", "token_kind"})

    @field_validator("api_url")
    @classmethod
    def _slack_over_https(cls, value: str) -> str:
        url = value.strip().lower().rstrip("/")
        if not _API.fullmatch(url):
            msg = f"api_url must be the Slack Web API on slack.com over https, such as {SLACK_API}, not {value!r}"
            raise ValueError(msg)
        return url
