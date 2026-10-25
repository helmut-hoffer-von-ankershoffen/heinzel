"""The Google adapter of the `documents` capability (ADR 0028), read mode only, and Drive's list of recent files.

Everything goes through `gws` (`GoogleSession`). A reference is a Docs, Sheets or Drive URL, or a bare file id.
Drive's metadata (`files.get`, scope
`drive.metadata.readonly`) says what the file is; a document then comes from the Docs API (`documents.get` with every
tab) and a spreadsheet from the Sheets API (`spreadsheets.get` with grid data), each turned into Markdown-flavoured
text. URLs anywhere else are refused before any request; the `url` returned is built from the id, never taken from
an answer.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from heinzel import Document, HeinzelError, clean_text
from heinzel_google._docs import document_to_text
from heinzel_google._gws import GoogleSession
from heinzel_google._models import DriveFile
from heinzel_google._sheets import spreadsheet_to_text

DRIVE_GET = ("drive", "files", "get")
DRIVE_LIST = ("drive", "files", "list")
DOCS_GET = ("docs", "documents", "get")
SHEETS_GET = ("sheets", "spreadsheets", "get")
DOCUMENT = "application/vnd.google-apps.document"
SPREADSHEET = "application/vnd.google-apps.spreadsheet"
SHORTCUT = "application/vnd.google-apps.shortcut"
KINDS = {DOCUMENT: "document", SPREADSHEET: "spreadsheet"}
EDITORS = {"document": "https://docs.google.com/document/d/", "spreadsheet": "https://docs.google.com/spreadsheets/d/"}
_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_EDITOR_PATH = re.compile(r"^/(?:document|spreadsheets)(?:/u/[0-9]{1,3})?/d/([A-Za-z0-9_-]{10,200})(?:/.*)?$")
_FILE_PATH = re.compile(r"^/file(?:/u/[0-9]{1,3})?/d/([A-Za-z0-9_-]{10,200})(?:/.*)?$")
MAX_NAME = 100
"""Characters of a name filter for `hz google files`."""
EXAMPLE = "https://docs.google.com/document/d/1AbC.../edit"
MAX_REF = 200
"""Characters of a refused reference repeated in its error."""


def _line(value: object) -> str:
    """One clean line: control characters gone, whitespace collapsed."""
    return " ".join(clean_text(str(value or "")).split())


def file_id_of(ref: str) -> str:
    """The Drive file id in `ref`: a bare id, or a Docs, Sheets or Drive URL.

    Raises:
        HeinzelError: If `ref` is neither, or a URL on another host or not over https.
    """
    text = clean_text(ref).strip()
    if _ID.fullmatch(text):
        return text
    parts = urlsplit(text)
    if parts.scheme != "https" or parts.netloc not in {"docs.google.com", "drive.google.com"}:
        msg = (
            f"{clean_text(ref)[:MAX_REF]!r} is not a Google Docs, Sheets or Drive link or file id; pass e.g. {EXAMPLE}."
        )
        raise HeinzelError(msg)
    pattern = _EDITOR_PATH if parts.netloc == "docs.google.com" else _FILE_PATH
    if found := pattern.fullmatch(parts.path):
        return found.group(1)
    if parts.netloc == "drive.google.com" and parts.path == "/open":
        ids = parse_qs(parts.query).get("id", [])
        if len(ids) == 1 and _ID.fullmatch(ids[0]):
            return ids[0]
    msg = f"{clean_text(ref)[:MAX_REF]!r} is not a link to one document or spreadsheet; pass e.g. {EXAMPLE}."
    raise HeinzelError(msg)


class GoogleDocuments:
    """Reads Google Docs and Sheets the signed-in user can open, by URL or file id."""

    def __init__(self, session: GoogleSession) -> None:
        """Build the adapter; the kernel injects the session."""
        self._session = session

    def read(self, ref: str) -> Document:
        """The text of the Google Doc or Sheet `ref`: its URL, or its Drive file id.

        Raises:
            HeinzelError: If `ref` is neither, the file is something else, the sign-in is missing, or Google fails.
        """
        file_id = file_id_of(ref)
        meta = self._metadata(file_id)
        if meta.get("mimeType") == SHORTCUT:  # follow one shortcut to its target, no further
            target = cast("dict[str, Any]", meta.get("shortcutDetails") or {}).get("targetId")
            if not isinstance(target, str) or not _ID.fullmatch(target):
                msg = f"The Drive shortcut {file_id} has no target the signed-in account can see."
                raise HeinzelError(msg)
            file_id, meta = target, self._metadata(target)
        kind = KINDS.get(str(meta.get("mimeType")))
        if kind is None:
            msg = (
                f"The Drive file {file_id} is neither a Google Doc nor a Google Sheet; the adapter reads those only "
                "(an uploaded Word or Excel file needs to be opened as a Google Doc or Sheet first)."
            )
            raise HeinzelError(msg)
        name = _line(meta.get("name"))
        url = f"{EDITORS[kind]}{file_id}/edit"
        if kind == "document":
            data, size = self._session.get(
                "Google Docs", DOCS_GET, {"documentId": file_id, "includeTabsContent": True}, f"document {file_id}"
            )
            title = _line(data.get("title") or name)
            revision = data.get("revisionId")
            text = document_to_text(data, size)
        else:
            fields = "properties.title,sheets(properties(title,sheetType),data(rowData(values(formattedValue))))"
            data, size = self._session.get(
                "Google Sheets",
                SHEETS_GET,
                {"spreadsheetId": file_id, "includeGridData": True, "fields": fields},
                f"spreadsheet {file_id}",
            )
            title = _line(cast("dict[str, Any]", data.get("properties") or {}).get("title") or name)
            revision = meta.get("version")
            text = spreadsheet_to_text(data, size)
        return Document(
            ref=clean_text(ref.strip()),
            title=title,
            text=clean_text(text),
            url=url,
            revision=clean_text(str(revision)) if isinstance(revision, str | int) else None,
        )

    def _metadata(self, file_id: str) -> dict[str, Any]:
        return self._session.get(
            "Google Drive",
            DRIVE_GET,
            {"fileId": file_id, "fields": "id,name,mimeType,version,shortcutDetails", "supportsAllDrives": True},
            f"file {file_id}",
        )[0]


class Drive:
    """Lists the Google Docs and Sheets the signed-in user can open that changed recently; metadata only."""

    def __init__(self, session: GoogleSession) -> None:
        """Build the lister; the kernel injects the session."""
        self._session = session

    def recent(self, days: int, name: str | None = None, now: datetime | None = None) -> list[DriveFile]:
        """Docs and Sheets modified in the last `days` days, newest first, optionally whose name contains `name`.

        Raises:
            HeinzelError: If `name` is too long, the sign-in is missing, or Google fails or has too many to list.
        """
        since = (now or datetime.now(UTC)) - timedelta(days=days)
        types = " or ".join(f"mimeType = '{mime}'" for mime in KINDS)
        query = f"({types}) and trashed = false and modifiedTime > '{since.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
        if name:
            text = " ".join(clean_text(name).split())
            if len(text) > MAX_NAME:
                msg = f"The name to look for is longer than {MAX_NAME} characters."
                raise HeinzelError(msg)
            query += " and name contains '" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
        params: dict[str, object] = {
            "q": query,
            "orderBy": "modifiedTime desc",
            "pageSize": 100,
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        found: list[DriveFile] = []
        for items, _ in self._session.pages(
            "Google Drive", DRIVE_LIST, params, f"files changed in {days} days", "files"
        ):
            for item in items:
                file = _file(item)
                if file is not None:
                    found.append(file)
        return found


def _file(item: dict[str, Any]) -> DriveFile | None:
    kind = KINDS.get(str(item.get("mimeType")))
    file_id = item.get("id")
    if kind is None or not isinstance(file_id, str) or not _ID.fullmatch(file_id):
        return None
    try:
        modified = datetime.fromisoformat(str(item.get("modifiedTime"))).astimezone(UTC)
    except ValueError:
        return None
    return DriveFile(
        ref=f"{EDITORS[kind]}{file_id}/edit",
        name=_line(item.get("name")),
        kind=kind,
        modified_at=modified,
    )
