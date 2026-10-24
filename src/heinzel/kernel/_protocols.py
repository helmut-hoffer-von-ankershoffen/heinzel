"""Integration capabilities: what workflows read from and write to a team's tools, whatever the vendor (ADR 0028).

Each protocol covers what the first workflows need, not a vendor's whole API. Adapters in plugins implement them over
any transport (a vendor API, a CLI, an MCP server); the kernel hands a requester a handle limited to the modes it
declared. The protocols are part of the public API and versioned with it (ADR 0026).
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class Window(BaseModel):
    """A half-open time window `[since, until)` to list changes in."""

    since: datetime = Field(description="Start of the window, inclusive; timezone-aware.")
    until: datetime = Field(description="End of the window, exclusive; timezone-aware.")


class PullRequest(BaseModel):
    """A merged pull request (or merge request) on a code host."""

    repository: str = Field(description="Repository it belongs to, in the code host's notation, e.g. `org/app`.")
    number: int = Field(description="Number of the pull request within the repository.")
    title: str = Field(description="Title of the pull request.")
    url: str = Field(description="Where people open it.")
    author: str | None = Field(default=None, description="Login of the author; none if the host doesn't say.")
    merged_at: datetime = Field(description="When it was merged.")
    labels: list[str] = Field(default_factory=list, description="Labels on the pull request.")


class Issue(BaseModel):
    """An issue in a tracker or on a code host."""

    key: str = Field(description="Identifier people use, e.g. `APP-123` or `org/app#42`.")
    title: str = Field(description="Title or summary of the issue.")
    url: str = Field(description="Where people open it.")
    state: str = Field(description="State in the tracker's own words, e.g. `open`, `In Progress`, `Done`.")
    updated_at: datetime = Field(description="When it last changed.")
    assignee: str | None = Field(default=None, description="Who it is assigned to; none if unassigned.")
    labels: list[str] = Field(default_factory=list, description="Labels, tags or components.")


class Document(BaseModel):
    """The text of a document, for reading."""

    ref: str = Field(description="The reference it was read by: an id or URL in the adapter's notation.")
    title: str = Field(description="Title of the document.")
    text: str = Field(description="Content as plain text or Markdown.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")
    revision: str | None = Field(default=None, description="Revision read, if the adapter tracks revisions.")


class SectionReplacement(BaseModel):
    """What `Documents.replace_section` changed."""

    ref: str = Field(description="The document written to.")
    marker: str = Field(description="The marker of the section that was replaced.")
    revision: str | None = Field(default=None, description="Revision after the write, if the adapter tracks them.")


class CodeHost(Protocol):
    """A code host: repositories, pull requests and their issues (capability `code-host`)."""

    def merged_pull_requests(self, repository: str, window: Window) -> list[PullRequest]:
        """Pull requests of `repository` merged within `window`, oldest first. Mode: read."""
        ...

    def issues(self, repository: str, window: Window) -> list[Issue]:
        """Issues of `repository` created or updated within `window`, oldest first. Mode: read."""
        ...


class Tracker(Protocol):
    """An issue tracker (capability `tracker`)."""

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        """Issues of `project` updated within `window`, oldest first. Mode: read."""
        ...


class Documents(Protocol):
    """Documents people write together (capability `documents`)."""

    def read(self, ref: str) -> Document:
        """The text of the document `ref`. Mode: read."""
        ...

    def replace_section(self, ref: str, marker: str, text: str) -> SectionReplacement:
        """Replace the section of `ref` marked by `marker` with `text`, leaving the rest alone. Mode: write.

        Only publish operations (`read_only=False`) get a handle on which this works (ADR 0028).
        """
        ...
