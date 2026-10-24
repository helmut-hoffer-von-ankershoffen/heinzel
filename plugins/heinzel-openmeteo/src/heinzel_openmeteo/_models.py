"""What the plugin's own operations return."""

from pydantic import BaseModel, Field


class OpenMeteoAbout(BaseModel):
    """What this plugin is, where it runs and where its weather comes from."""

    plugin: str = Field(description="Distribution name of the plugin.")
    version: str = Field(description="Installed version of the plugin.")
    heinzel_version: str = Field(description="Version of the Heinzelmännchen it runs in.")
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides.")
    hosts: list[str] = Field(description="The hosts it sends requests to; the places you ask about go there.")
    source: str = Field(description="Where the weather comes from.")
