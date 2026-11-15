"""Documents in every documents connector that is on: open one by its link or id, search them all (ADR 0161).

With several connectors switched on for `documents`, such as Google Docs and Confluence, `hz document read` opens a
link in the connector whose links match its host, and an id in the connector that found it (`google:<id>` names one);
`hz document search` asks every connector at once, each with its own time limit, and lists what each found, labelled
with its source, next to any connector that couldn't answer. With one connector, both work as they always did.

They read your documents, so they are on the CLI, MCP and the web app, not the unauthenticated REST head.
"""

from typing import Annotated

from pydantic import BaseModel, Field

from heinzel import Document, DocumentHit, Documents, Module, SourceFailure

HEADS = frozenset({"cli", "mcp", "web"})
MAX_RESULTS = 50

module = Module("document", "Open and search your documents in every documents connector that is on.")


class DocumentSearch(BaseModel):
    """What a search found in every documents connector that is on, and which couldn't answer."""

    query: str = Field(description="What was searched for.")
    hits: list[DocumentHit] = Field(description="What the connectors found, in turns, each labelled with its source.")
    failures: list[SourceFailure] = Field(
        default_factory=list[SourceFailure],
        description="Connectors that couldn't answer, and why; the others' results are still listed.",
    )


@module.operation(heads=HEADS, requires=["documents:read"], optional=True)
def read(
    documents: Documents,
    ref: Annotated[
        str,
        Field(
            description="The document: its link, an id a search returned, or `<connector>:<id>`, e.g. `wiki:123456`."
        ),
    ],
) -> Document:
    """Read a document's text through the connector it belongs to."""
    return documents.read(ref)


@module.operation(heads=HEADS, requires=["documents:read"], optional=True)
def search(
    documents: Documents,
    query: Annotated[str, Field(min_length=1, description="What to look for in titles and text.")],
    limit: Annotated[int, Field(ge=1, le=MAX_RESULTS, description="At most this many results.")] = 20,
) -> DocumentSearch:
    """Search every documents connector at once; one that fails or is slow is reported, not fatal."""
    found = documents.search(query, limit)
    return DocumentSearch(query=query, hits=list(found), failures=list(getattr(found, "failures", [])))
