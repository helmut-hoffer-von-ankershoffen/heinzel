"""Settings and the models the plugin's own operations return."""

from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from heinzel import ModuleSettings

GITHUB_API = "https://api.github.com"


class GithubAbout(BaseModel):
    """What this plugin is, where it runs and which GitHub it talks to."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    api_url: str = Field(description="The GitHub REST API the adapter sends requests to.")


class GithubSettings(ModuleSettings):
    """Settings of the GitHub adapter: the `[github]` table of the `hz` config files, or `HZ_GITHUB__<FIELD>`."""

    api_url: str = Field(
        default=GITHUB_API,
        description="Base URL of the GitHub REST API: `https://api.github.com`, or for GitHub Enterprise Server "
        "`https://<host>/api/v3`. Only the site, user and HZ_CONFIG files and the environment can set it, never a "
        "project hz.toml, because the token goes there. Because this decides the host, `hz system capability list` "
        "shows the adapter's hosts as `unknown (configured)`.",
    )
    timeout: float = Field(
        default=30.0, gt=0, le=300, description="Seconds to wait for each answer from GitHub before giving up."
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"api_url"})

    @field_validator("api_url")
    @classmethod
    def _https_without_trailing_slash(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        parts = urlsplit(url)
        try:
            parts.port  # noqa: B018 - raises ValueError on a malformed port
        except ValueError:
            valid = False
        else:
            valid = (
                parts.scheme == "https"
                and bool(parts.hostname)
                and "@" not in parts.netloc  # no user or password: the token goes to the host alone
                and not any(mark in url for mark in "?#")  # a query or fragment would break every request URL
            )
        if not valid:
            msg = (
                f"api_url must be an https URL without user, query or fragment, such as {GITHUB_API} or "
                f"https://ghe.example.com/api/v3, not {value!r}"
            )
            raise ValueError(msg)
        return url
