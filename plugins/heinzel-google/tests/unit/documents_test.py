"""The documents adapter and Drive's list of recent files, against the fake Google."""

import json
from datetime import UTC, datetime

import pytest
from heinzel_google import Drive, GoogleDocuments, Process
from heinzel_google._documents import file_id_of

from heinzel import HeinzelError

pytestmark = pytest.mark.unit

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
SHEET_ID = "1SheetIdSheetIdSheetIdSheet"
DOCUMENT = "application/vnd.google-apps.document"
SPREADSHEET = "application/vnd.google-apps.spreadsheet"


def output(body: object) -> Process:
    return Process(0, json.dumps(body).encode())


@pytest.mark.parametrize(
    "ref",
    [
        DOC_ID,
        f"  {DOC_ID} ",
        f"https://docs.google.com/document/d/{DOC_ID}/edit?tab=t.0#heading=h.1",
        f"https://docs.google.com/document/u/1/d/{DOC_ID}",
        f"https://docs.google.com/spreadsheets/d/{DOC_ID}/edit#gid=0",
        f"https://drive.google.com/file/d/{DOC_ID}/view",
        f"https://drive.google.com/open?id={DOC_ID}",
    ],
)
def test_references_name_a_file_id(ref):
    assert file_id_of(ref) == DOC_ID


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("short", "not a Google Docs, Sheets or Drive link"),
        (f"http://docs.google.com/document/d/{DOC_ID}", "not a Google Docs, Sheets or Drive link"),
        (f"https://evil.example/document/d/{DOC_ID}", "not a Google Docs, Sheets or Drive link"),
        (f"https://docs.google.com.evil.example/document/d/{DOC_ID}", "not a Google Docs, Sheets or Drive link"),
        ("https://docs.google.com/document/u/0/", "not a link to one document"),
        (f"https://drive.google.com/open?id={DOC_ID}&id=other", "not a link to one document"),
        ("https://drive.google.com/drive/folders/abc", "not a link to one document"),
    ],
)
def test_other_references_are_refused(ref, message):
    with pytest.raises(HeinzelError, match=message):
        file_id_of(ref)


def test_a_doc_is_read_as_text(session, fake_gws):
    fake_gws.doc(content=[{"paragraph": {"elements": [{"textRun": {"content": "Hi \u202eyou\n"}}]}}])
    document = GoogleDocuments(session).read(f"https://docs.google.com/document/d/{DOC_ID}/edit")
    assert document.model_dump() == {
        "ref": f"https://docs.google.com/document/d/{DOC_ID}/edit",
        "title": "Plan",
        "text": "Hi you\n",
        "url": f"https://docs.google.com/document/d/{DOC_ID}/edit",
        "revision": "rev-7",
    }
    drive, docs = fake_gws.api_runs
    assert drive.command == ("drive", "files", "get")
    assert drive.params == {
        "fileId": DOC_ID,
        "fields": "id,name,mimeType,version,shortcutDetails",
        "supportsAllDrives": True,
    }
    assert (docs.command, docs.params) == (
        ("docs", "documents", "get"),
        {"documentId": DOC_ID, "includeTabsContent": True},
    )
    assert docs.arguments[-2:] == ["--format", "json"]


def test_a_sheet_is_read_as_tables(session, fake_gws):
    fake_gws.file(SHEET_ID, SPREADSHEET, "Budget", version="9")
    fake_gws.json(
        ("sheets", "spreadsheets", "get"),
        {
            "properties": {"title": "Budget"},
            "sheets": [{"properties": {"title": "Q1"}, "data": [{"rowData": [{"values": [{"formattedValue": "1"}]}]}]}],
        },
    )
    document = GoogleDocuments(session).read(SHEET_ID)
    assert (document.title, document.text, document.revision) == ("Budget", "## Q1\n| 1 |\n| --- |\n", "9")
    assert document.url == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
    sheets = fake_gws.api_runs[1]
    assert sheets.command == ("sheets", "spreadsheets", "get")
    assert sheets.params["includeGridData"] is True


def test_a_shortcut_is_followed_once(session, fake_gws):
    fake_gws.doc()
    shortcut = {"mimeType": "application/vnd.google-apps.shortcut", "shortcutDetails": {"targetId": DOC_ID}}
    target = {"id": DOC_ID, "name": "Plan", "mimeType": DOCUMENT}
    fake_gws.on(("drive", "files", "get"), output(shortcut), output(target))
    assert GoogleDocuments(session).read(SHEET_ID).text == "Hello\n"


def test_a_shortcut_without_a_target_is_refused(session, fake_gws):
    fake_gws.json(("drive", "files", "get"), {"mimeType": "application/vnd.google-apps.shortcut"})
    with pytest.raises(HeinzelError, match="has no target"):
        GoogleDocuments(session).read(SHEET_ID)


def test_other_files_are_refused(session, fake_gws):
    fake_gws.file(DOC_ID, "application/pdf")
    with pytest.raises(HeinzelError, match="neither a Google Doc nor a Google Sheet"):
        GoogleDocuments(session).read(DOC_ID)


def test_a_doc_without_title_or_revision_falls_back(session, fake_gws):
    fake_gws.file(DOC_ID, DOCUMENT, "From Drive")
    fake_gws.json(("docs", "documents", "get"), {"body": {"content": []}})
    document = GoogleDocuments(session).read(DOC_ID)
    assert (document.title, document.revision, document.text) == ("From Drive", None, "\n")


def file(number: int, mime: str = DOCUMENT, modified: str = "2026-10-20T08:00:00.000Z") -> dict[str, object]:
    return {"id": f"{DOC_ID}{number}", "name": f"File \u200b{number}", "mimeType": mime, "modifiedTime": modified}


def test_recent_files_are_listed_with_a_safe_query(session, fake_gws):
    fake_gws.json(
        ("drive", "files", "list"),
        {
            "files": [
                file(1),
                file(2, SPREADSHEET),
                file(3, "application/pdf"),
                file(4, modified="yesterday"),
                {"id": "bad id", "mimeType": DOCUMENT},
            ]
        },
    )
    now = datetime(2026, 10, 24, 12, tzinfo=UTC)
    found = Drive(session).recent(7, "it's a \\ plan", now=now)
    assert [(f.ref, f.name, f.kind) for f in found] == [
        (f"https://docs.google.com/document/d/{DOC_ID}1/edit", "File 1", "document"),
        (f"https://docs.google.com/spreadsheets/d/{DOC_ID}2/edit", "File 2", "spreadsheet"),
    ]
    query = fake_gws.api_runs[0].params["q"]
    assert query == (
        "(mimeType = 'application/vnd.google-apps.document' or mimeType = 'application/vnd.google-apps.spreadsheet') "
        "and trashed = false and modifiedTime > '2026-10-17T12:00:00Z' and name contains 'it\\'s a \\\\ plan'"
    )


def test_a_long_name_filter_is_refused(session):
    with pytest.raises(HeinzelError, match="longer than 100"):
        Drive(session).recent(7, "x" * 101)


def test_recent_files_without_a_name_filter(session, fake_gws):
    fake_gws.json(("drive", "files", "list"), {"files": []})
    assert Drive(session).recent(1) == []
    assert "name contains" not in fake_gws.api_runs[0].params["q"]


def test_refs_titles_and_names_come_back_as_clean_single_lines(session, fake_gws):
    fake_gws.file(DOC_ID, DOCUMENT, "From\nDrive")
    fake_gws.json(("docs", "documents", "get"), {"title": "Plan\n# Forged", "body": {"content": []}})
    document = GoogleDocuments(session).read(f" {DOC_ID}\u202e ")
    assert (document.ref, document.title) == (DOC_ID, "Plan # Forged")


def test_a_refused_reference_is_cut_in_the_message():
    with pytest.raises(HeinzelError) as caught:
        file_id_of("x" * 5000)
    assert len(str(caught.value)) < 400


def test_recent_files_follow_pages_newest_first(session, fake_gws):
    first = {"files": [file(1, modified="2026-10-23T08:00:00Z")], "nextPageToken": "p2"}
    fake_gws.json(("drive", "files", "list"), first)
    fake_gws.json(("drive", "files", "list"), {"files": [file(2, modified="2026-10-21T08:00:00Z")]}, page="p2")
    found = Drive(session).recent(7, now=datetime(2026, 10, 24, tzinfo=UTC))
    assert [f.name for f in found] == ["File 1", "File 2"]
