from collections.abc import Sequence

import pytest
from heinzel_atlassian import Acli, Completed
from heinzel_atlassian._acli import MAX_BYTES, AcliTimeoutError
from heinzel_atlassian._client import MESSAGE_CHARS, AcliClient, documents, redact, said

from heinzel import HeinzelError

pytestmark = pytest.mark.unit

ARGS = ["jira", "workitem", "search", "--jql", "x", "--json"]
TOKEN = "ATATT3xFfGF0" + "x" * 180  # shaped like an Atlassian API token, made up


class Stub(Acli):
    def __init__(
        self,
        done: Completed | None = None,
        error: Exception | None = None,
        reported: tuple[int, int, int] | None = (1, 3, 39),
    ) -> None:
        self.done, self.error, self.reported = done, error, reported

    def version(self) -> tuple[int, int, int] | None:
        if self.error is not None:
            raise self.error
        return self.reported

    def run(self, args: Sequence[str], timeout: float) -> Completed:
        del args, timeout
        if self.error is not None:
            raise self.error
        assert self.done is not None
        return self.done


def failing(stderr: str, returncode: int = 1) -> Stub:
    return Stub(Completed(returncode, b"", stderr.encode(), overflow=False))


def message(stub: Stub, product: str = "Jira") -> str:
    with pytest.raises(HeinzelError) as caught:
        AcliClient(stub, 7.0).json(product, ARGS, "project WID")  # type: ignore[arg-type] # a Product
    return str(caught.value)


def test_json_is_parsed_from_standard_output():
    stub = Stub(Completed(0, b'[{"key": "WID-1"}]', b"", overflow=False))
    assert AcliClient(stub, 7.0).json("Jira", ARGS, "project WID") == [[{"key": "WID-1"}]]


def test_an_acli_older_than_the_minimum_is_refused_before_the_read():
    stub = Stub(Completed(0, b"[]", b"", overflow=False), reported=(1, 2, 9))
    text = message(stub)
    assert "acli 1.2.9 is older than 1.3.39" in text
    assert "upgrade it" in text


@pytest.mark.parametrize("reported", [(1, 3, 39), (2, 0, 0), None])
def test_the_minimum_a_newer_or_an_unnamed_version_reads(reported):
    stub = Stub(Completed(0, b"[]", b"", overflow=False), reported=reported)
    assert AcliClient(stub, 7.0).json("Jira", ARGS, "project WID") == [[]]


def test_a_missing_acli_names_the_install_and_the_login():
    text = message(Stub(error=FileNotFoundError("acli")))
    assert "not on PATH" in text
    assert "install-acli" in text
    assert text.endswith("acli jira auth login")


def test_a_missing_acli_for_confluence_names_its_login():
    assert message(Stub(error=FileNotFoundError("acli")), "Confluence").endswith("acli confluence auth login")


def test_a_timeout_says_what_to_change():
    text = message(Stub(error=AcliTimeoutError("acli")))
    assert "within 7 seconds" in text
    assert "atlassian.timeout" in text


def test_acli_that_cannot_start_names_the_error_type_only():
    text = message(Stub(error=PermissionError(13, "denied /secret/path")))
    assert "PermissionError" in text
    assert "/secret/path" not in text


def test_too_much_output_asks_for_a_shorter_window():
    text = message(Stub(Completed(-9, b"[", b"", overflow=True)))
    assert f"more than {MAX_BYTES} bytes" in text
    assert "shorter window" in text


def test_output_that_is_not_json_is_a_clear_error():
    assert "is not JSON" in message(Stub(Completed(0, b"<html>", b"", overflow=False)))


@pytest.mark.parametrize(
    "stderr",
    [
        "✗ Error: unauthorized: use 'acli jira auth login' to authenticate\n",
        "Error: not logged in\n",
        "request failed with status 401\n",
    ],
)
def test_unauthorized_names_the_login(stderr):
    text = message(failing(stderr))
    assert text.startswith("acli is not logged in to Jira")
    assert text.endswith("Log in: acli jira auth login")


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("Error: 429 Too Many Requests\n", "rate limit is reached for project WID"),
        ("Error: forbidden (403)\n", "needs the permission Browse projects on it"),
        ("Error: project does not exist\n", "Jira has no project WID, or the account acli is logged in as can't see"),
        ("Error: something odd\n", "acli failed for project WID with exit status 3: something odd"),
        # Numbers inside ids are no statuses, and words elsewhere are no signs.
        ("Error: page 4013377 not found\n", "Jira has no project WID"),
        ("Error: work item WID-403 does not exist\n", "Jira has no project WID"),
        ("Error: work item WID-4291 does not exist\n", "Jira has no project WID"),
        ("Error: see the permissions page; run acli jira auth login later\n", "acli failed for project WID"),
    ],
)
def test_other_failures_say_what_to_do(stderr, expected):
    assert expected in message(failing(stderr, 3))


def test_without_stderr_standard_output_is_shown_and_otherwise_no_details():
    assert "boom" in message(Stub(Completed(2, b"Error: boom", b"", overflow=False)))
    assert message(Stub(Completed(2, b"", b"", overflow=False))).endswith("no details")


@pytest.mark.parametrize(
    "secret",
    [
        TOKEN,
        "Bearer abc.def-ghi_jkl",
        "Basic YWRhOnNlY3JldA==",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl",
        'token="hunter2-secret"',
        "password: hunter2-secret",
        "access_token=hunter2-secret",
        '{"refresh_token":"hunter2-secret"}',
        "apiToken: hunter2-secret",
        "api_key=hunter2-secret",
        "client_secret=hunter2-secret",
        "X-Session: hunter2-secret",
        "https://wilma:hunter2-secret@acme.atlassian.net/rest",
    ],
)
def test_credentials_in_acli_errors_are_redacted(secret):
    text = message(failing(f"Error: request with {secret} failed\n"))
    for part in ("ATATT3xFfGF0x", "abc.def-ghi_jkl", "YWRhOnNlY3JldA", "eyJhbGci", "hunter2"):
        assert part not in text


def test_documents_printed_back_to_back_are_all_read():
    assert documents(' [1] \n{"a": 2}[3]\n') == [[1], {"a": 2}, [3]]


@pytest.mark.parametrize("text", ["", "  \n", "[1] trailing", "{"])
def test_text_without_documents_or_with_anything_else_is_refused(text):
    with pytest.raises(ValueError):  # noqa: PT011 - json's own errors, whatever their message
        documents(text)


def test_output_that_is_not_utf8_is_a_clear_error():
    assert "is not JSON" in message(Stub(Completed(0, b"\xff\xfe[]", b"", overflow=False)))


def test_redaction_comes_before_the_cut():
    # The token starts just before the cut: cutting first would leave its start behind.
    text = said(f"Error: {'a' * (MESSAGE_CHARS - 10)} {TOKEN} {'b' * 50}".encode())
    assert "ATAT" not in text
    assert text.endswith("…")
    assert len(text) == MESSAGE_CHARS + 1


def test_escape_sequences_controls_and_bidi_are_removed_from_acli_errors():
    text = said("\x1b[31m✗ Error:\x1b[0m bad\x07 \u202eword\x1b]8;;https://x.example\x07link\x1b]8;;\x07\r\n".encode())
    assert text == "bad word" + "link"


def test_redact_keeps_the_names_of_what_it_hides():
    assert redact("Authorization: Bearer abc123") == "Authorization: *** ***"
    assert redact("token=abc123 rest") == "token=*** rest"
