"""Settings and the models the plugin's own operations return."""

import re
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import SettingsConfigDict

from heinzel import ModuleSettings

SITE_EXAMPLE = "https://acme.atlassian.net"
_SITE = re.compile(r"^https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net$")
_EMAIL = re.compile(r"^[^@\s:]+@[^@\s:]+\.[^@\s:]+$")


class AtlassianAbout(BaseModel):
    """What this plugin is, where it runs and which Atlassian site it talks to."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    site_url: str | None = Field(description="The Atlassian Cloud site the adapters send requests to; none if unset.")
    apis: list[str] = Field(description="The REST APIs the adapters use.")


class AtlassianSettings(ModuleSettings):
    """Settings of the Atlassian adapters: the `[atlassian]` table of the `hz` config files, or `HZ_ATLASSIAN__<FIELD>`.

    `site_url` and `email` are ignored in a project hz.toml: the token goes to that site, as that account.
    """

    site_url: str | None = Field(
        default=None,
        description="The Atlassian Cloud site, `https://<site>.atlassian.net`, where Jira and Confluence live. Only "
        "the site, user and HZ_CONFIG files and the environment can set it, never a project hz.toml, because the "
        "token goes there.",
    )
    email: str | None = Field(
        default=None,
        description="Email address of the Atlassian account the API token belongs to; with the token, it is the "
        "Basic auth of every request. Never read from a project hz.toml.",
    )
    timeout: float = Field(
        default=30.0, gt=0, le=300, description="Seconds to wait for each answer from Atlassian before giving up."
    )

    model_config = SettingsConfigDict(hide_input_in_errors=True)  # a refused email isn't repeated in the error
    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"site_url", "email"})

    @field_validator("site_url")
    @classmethod
    def _atlassian_cloud_site(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        url = value.strip().lower().rstrip("/").removesuffix("/wiki").removesuffix("/jira")
        if not _SITE.fullmatch(url):
            msg = f"site_url must be an Atlassian Cloud site such as {SITE_EXAMPLE}, not {value!r}"
            raise ValueError(msg)
        return url

    @field_validator("email")
    @classmethod
    def _plausible_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        email = value.strip()
        if not _EMAIL.fullmatch(email):
            msg = "email must be the address of an Atlassian account, such as ada@acme.example"  # never echo it
            raise ValueError(msg)
        return email
