"""The sound module of system: soundscapes for the web app, chosen like a theme but on their own (ADR 0058).

A soundscape is data a plugin offers with `module.soundscape(...)`: licensed audio files the web app mixes live, each
layer following a signal such as the part of the day, or a stream in the official player of an allowed host. The core
offers Claude FM, a stream on YouTube. Sound is off until the person picks a soundscape and presses Play;
`hz system sound use <name>` picks one, `off` is none.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Module, SoundAssets
from heinzel.modules.system.sound._models import (
    SoundAssetView,
    SoundEarconView,
    SoundLayerView,
    SoundList,
    SoundscapeView,
    SoundSettings,
    SoundStreamView,
)
from heinzel.modules.system.sound._service import SoundService

module = Module("sound", "Pick the web app's soundscape: off, a built-in one or one a plugin offers.")
module.settings(SoundSettings)
module.service(SoundService)
module.service(SoundAssets, SoundService)  # the web head serves audio and sets its page's CSP through it (ADR 0058)
module.soundscape(Path(__file__).parent / "soundscapes")


def _kind(view: SoundscapeView) -> str:
    if view.stream is not None:
        return f"stream ({view.stream.provider})"
    if view.kind != "layers":
        return ""
    return "1 layer" if len(view.layers) == 1 else f"{len(view.layers)} layers"


def render(result: SoundList) -> RenderableType:
    """The soundscapes with the one in effect marked, where each comes from, and what to run next."""
    table = Table(box=None, pad_edge=False)
    for column in ("", "Soundscape", "Title", "Kind", "From"):
        table.add_column(column, overflow="fold")
    for view in result.soundscapes:
        table.add_row(
            "*" if view.effective else "",
            Text(view.name, style="bold" if view.effective else ""),
            Text(view.title),
            Text(_kind(view)),
            Text(view.plugin or ""),
        )
    parts: list[RenderableType] = [table]
    parts.append(
        Text(
            f"Volume {result.volume}; follow my theme {'on' if result.follow_theme else 'off'}; mute in meetings "
            f"{'on' if result.mute_in_meetings else 'off'}. It plays in the web app (hz ui) after you press Play."
        )
    )
    parts += [Text(f"Left out: {conflict}", style="yellow") for conflict in result.conflicts]
    if result.note:
        parts.append(Text(result.note, style="yellow"))
    if result.next:
        parts.append(Text("Next:\n" + "\n".join(f"  {step}" for step in result.next)))
    return Group(*parts)


@module.operation(name="list", render=render)
def list_(service: SoundService) -> SoundList:
    """List the soundscapes of the web app, where each comes from, which one is picked and which plays."""
    return service.listing()


# It can switch on a stream from another host, so the unauthenticated REST head doesn't get it (ADR 0058).
@module.operation(read_only=False, render=render, heads={"cli", "mcp", "web"})
def use(
    service: SoundService,
    name: Annotated[
        str, Field(description="The soundscape to play, one `hz system sound list` shows, e.g. `claude-fm`, or `off`.")
    ],
) -> SoundList:
    """Pick the web app's soundscape, `off` for none; turns off theme following and saves your choice."""
    return service.use(name)


__all__ = [
    "SoundAssetView",
    "SoundEarconView",
    "SoundLayerView",
    "SoundList",
    "SoundService",
    "SoundSettings",
    "SoundStreamView",
    "SoundscapeView",
    "module",
]
