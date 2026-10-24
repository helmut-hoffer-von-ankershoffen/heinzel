"""Settings and the models the plugin's own operations return."""

import re
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator, model_validator

from heinzel import ModuleSettings

GITHUB_HOST = "github.com"
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})+$")


class GithubAbout(BaseModel):
    """What this plugin is, where it runs and which GitHub it talks to."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    host: str = Field(description="The GitHub host the adapter reads, through the GitHub CLI `gh`.")
    gh: str | None = Field(description="Path of the GitHub CLI every read goes through; none if missing.")


class GithubSettings(ModuleSettings):
    """Settings of the GitHub adapter: the `[github]` table of the `hz` config files, or `HZ_GITHUB__<FIELD>`.

    Authentication is gh's: log in with `gh auth login`. All three are ignored in a project hz.toml: a repository
    must not choose which program runs, where the login goes, or how long a call may take.
    """

    host: str = Field(
        default=GITHUB_HOST,
        description="The GitHub host `gh` talks to: `github.com`, or the host name of a GitHub Enterprise Server "
        "such as `ghe.example.com`; passed to `gh` as `--hostname` and `GH_HOST`, so `gh` uses its login for that "
        "host. Only the site, user and HZ_CONFIG files and the environment can set it, never a project hz.toml.",
    )
    gh: str = Field(
        default="gh",
        min_length=1,
        description="The GitHub CLI to run: a name looked up on PATH (absolute entries only) or an absolute path.",
    )
    timeout: float = Field(
        default=60.0, gt=0, le=600, description="Seconds to wait for each `gh` call before stopping it."
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"host", "gh", "timeout"})

    @model_validator(mode="before")
    @classmethod
    def _no_api_url(cls, data: Any) -> Any:  # noqa: ANN401 - the raw settings, whatever they are
        # `api_url` was replaced by `host`; ignoring a leftover would quietly read github.com instead of the server.
        if isinstance(data, dict) and "api_url" in data:
            msg = (
                "api_url was replaced by host, the host name gh logs in to: remove api_url and set e.g. "
                "`hz system config set github.host ghe.example.com`"
            )
            raise ValueError(msg)
        return data

    @field_validator("host")
    @classmethod
    def _host_name(cls, value: str) -> str:
        host = value.strip().lower()
        if not _HOST.fullmatch(host):
            msg = (
                f"host must be a host name such as {GITHUB_HOST} or ghe.example.com, without scheme, port or path, "
                f"not {value!r}"
            )
            raise ValueError(msg)
        return host


def api_host(host: str) -> str:
    """The host `gh` sends API requests to for `host`: `api.github.com` and `api.<name>.ghe.com`, else `host`."""
    if host == GITHUB_HOST or host.endswith(".ghe.com"):
        return f"api.{host}"
    return host
