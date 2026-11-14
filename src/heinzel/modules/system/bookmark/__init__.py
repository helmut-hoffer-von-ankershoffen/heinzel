"""The bookmark module of system: the owner's bookmark tree for the built-in browser (ADR 0140).

One ordered tree of folders and bookmarks per owner, kept as owner data and never in the browser's own storage, so
clearing browsing data keeps it. The web app stars pages into it, manages it and lists the top level in its
navigation. Agents can read it and add to it; changing, moving and removing entries is the person's, so those
operations aren't MCP tools.
"""

from typing import Annotated

from pydantic import Field
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Module
from heinzel.modules.system.bookmark._models import Bookmark, BookmarkList
from heinzel.modules.system.bookmark._service import BookmarkService

module = Module("bookmark", "Keep the built-in browser's bookmarks: a tree of folders and bookmarks.")
module.service(BookmarkService)

# Bookmarks are as private as a browsing habit: the unauthenticated REST head gets none of it.
READ_ADD = {"cli", "mcp", "web"}
# Rename, move, remove, restore and the favicon cache stay with the person: no MCP tool, so no agent proposes them.
PERSON = {"cli", "web"}

Entry = Annotated[str, Field(description="The entry's id, as `hz system bookmark list` shows it.")]
Parent = Annotated[str | None, Field(description="The folder's id to put it in; the top level when left out.")]
Before = Annotated[str | None, Field(description="The id of the entry in that folder to put it before; else last.")]


def render(result: BookmarkList) -> RenderableType:
    """The tree, indented by folder, with each entry's id."""
    if not result.bookmarks:
        return Text("No bookmarks yet. Add one: hz system bookmark add https://example.com")
    table = Table(box=None, pad_edge=False)
    for column in ("Id", "Title", "Address"):
        table.add_column(column, overflow="fold")
    for item in result.bookmarks:
        mark = "▸ " if item.kind == "folder" else ""
        table.add_row(item.id, Text("  " * item.depth + mark + item.title), Text(item.url or ""))
    return table


@module.operation(name="list", render=render, heads=READ_ADD)
def list_(service: BookmarkService) -> BookmarkList:
    """List the bookmarks and folders in their order, with each entry's id and folder."""
    return service.listing()


@module.operation(read_only=False, render=render, heads=READ_ADD)
def add(
    service: BookmarkService,
    url: Annotated[str, Field(description="The page's https:// address.")],
    title: Annotated[str | None, Field(description="The name shown; the host when left out.")] = None,
    parent: Parent = None,
    before: Before = None,
) -> BookmarkList:
    """Add a bookmark; an address already in that folder is kept once."""
    return service.add(url, title, parent, before)


@module.operation(read_only=False, render=render, heads=READ_ADD)
def folder(
    service: BookmarkService,
    title: Annotated[str, Field(description="The folder's name.")],
    parent: Parent = None,
    before: Before = None,
) -> BookmarkList:
    """Add an empty bookmark folder."""
    return service.folder(title, parent, before)


@module.operation(read_only=False, render=render, heads=PERSON)
def edit(
    service: BookmarkService,
    entry: Entry,
    title: Annotated[str | None, Field(description="The new name.")] = None,
    url: Annotated[str | None, Field(description="The new https:// address of a bookmark.")] = None,
) -> BookmarkList:
    """Rename a bookmark or folder, or change a bookmark's address."""
    return service.edit(entry, title, url)


@module.operation(read_only=False, render=render, heads=PERSON)
def move(service: BookmarkService, entry: Entry, parent: Parent = None, before: Before = None) -> BookmarkList:
    """Move a bookmark or folder into another folder or to another place in its own."""
    return service.move(entry, parent, before)


@module.operation(read_only=False, render=render, heads=PERSON)
def remove(service: BookmarkService, entry: Entry) -> BookmarkList:
    """Remove a bookmark, or a folder with everything in it; `restore` brings it back."""
    return service.remove(entry)


@module.operation(read_only=False, render=render, heads=PERSON)
def restore(
    service: BookmarkService,
    entry: Annotated[str | None, Field(description="The removed entry's id; the latest removal when left out.")] = None,
) -> BookmarkList:
    """Bring back a removed bookmark or folder to where it was."""
    return service.restore(entry)


# The desktop app hands over the icon a page itself showed; nothing is fetched from a third party.
@module.operation(read_only=False, render=render, heads={"web"})
def favicon(
    service: BookmarkService,
    origin: Annotated[str, Field(description="The origin the icon belongs to, e.g. `https://example.com`.")],
    image: Annotated[str, Field(description="The icon as a small base64 `data:` URL (PNG, JPEG, GIF or ICO).")],
) -> BookmarkList:
    """Keep the small icon a bookmarked site showed, per origin."""
    return service.set_favicon(origin, image)


__all__ = ["Bookmark", "BookmarkList", "BookmarkService", "module"]
