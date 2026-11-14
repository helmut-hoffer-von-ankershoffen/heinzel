"""Models of the bookmark module: the tree as a flat, ordered list any head can show."""

from typing import Literal

from pydantic import BaseModel, Field


class Bookmark(BaseModel):
    """One entry of the tree: a bookmark with an address, or a folder holding entries."""

    id: str = Field(description="The entry's id, e.g. `b-1a2b3c4d`; the other operations take it.")
    kind: Literal["bookmark", "folder"] = Field(description="A bookmark with an address, or a folder.")
    title: str = Field(description="The name shown.")
    url: str | None = Field(default=None, description="The address; a folder has none.")
    origin: str | None = Field(default=None, description="The address's origin, the key of its favicon.")
    parent: str | None = Field(default=None, description="The folder it is in; none at the top level.")
    depth: int = Field(default=0, description="How deep it sits: 0 at the top level.")


class BookmarkList(BaseModel):
    """The owner's bookmarks, depth-first in their order, with the cached favicons of their origins."""

    bookmarks: list[Bookmark] = Field(
        default_factory=list, description="Every entry, depth-first in order; `parent` and `depth` give the tree."
    )
    favicons: dict[str, str] = Field(
        default_factory=dict, description="A small image per origin as a `data:` URL, where a page showed one."
    )
    restorable: list[str] = Field(
        default_factory=list, description="Ids of removed entries `restore` can still bring back, latest first."
    )
    changed: str | None = Field(default=None, description="The id of the entry this call added or changed.")
