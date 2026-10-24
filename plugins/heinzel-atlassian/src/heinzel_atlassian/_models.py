"""Settings and the models the plugin's own operations return."""

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

from heinzel import ModuleSettings

SITE_EXAMPLE = "https://acme.atlassian.net"
_SITE = re.compile(r"^https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net$")


class AtlassianAbout(BaseModel):
    """What this plugin is, where it runs, which Atlassian site its links name and how it reads."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    site_url: str | None = Field(description="The Atlassian Cloud site that links point to and refs must name.")
    acli: str | None = Field(
        description="Path of Atlassian's CLI `acli`, which every read goes through; none if missing."
    )
    commands: list[str] = Field(description="The acli commands the adapters run.")


class AtlassianSettings(ModuleSettings):
    """Settings of the Atlassian adapters: the `[atlassian]` table of the `hz` config files, or `HZ_ATLASSIAN__<FIELD>`.

    Authentication is acli's: log in with `acli jira auth login` and `acli confluence auth login`. `site_url`,
    `acli` and `timeout` are ignored in a project hz.toml: the site decides which page URLs are accepted, a repository
    must not pick the program that runs, nor make reads hang.
    """

    site_url: str | None = Field(
        default=None,
        description="The Atlassian Cloud site acli is logged in to, `https://<site>.atlassian.net`: issue and page "
        "links are built on it, and page URLs must name it. Only the site, user and HZ_CONFIG files and the "
        "environment can set it, never a project hz.toml.",
    )
    acli: str = Field(
        default="acli",
        description="Atlassian's CLI: a name looked up on PATH (only absolute PATH entries count) or an absolute "
        "path. Never read from a project hz.toml.",
    )
    timeout: float = Field(
        default=60.0, gt=0, le=600, description="Seconds to wait for each acli command before stopping it."
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"site_url", "acli", "timeout"})

    @field_validator("acli")
    @classmethod
    def _name_or_absolute_path(cls, value: str) -> str:
        text = value.strip()
        if not text or (("/" in text or "\\" in text) and not Path(text).is_absolute()):
            msg = f"acli must be a program name such as acli or an absolute path, not {value!r}"
            raise ValueError(msg)
        return text

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
