import pytest
from heinzel_github import GhResult, _client
from heinzel_github._client import MAX_PAGES, MAX_SAID, GhClient, redact, said

from heinzel import HeinzelError

pytestmark = pytest.mark.unit

PATH = "repos/acme/widgets/issues"
TOKEN = "ghp_" + "A1b2C3d4" * 5  # shaped like a classic token; made up


def items(fake, host: str = "github.com") -> list:
    client = GhClient(fake, host, 7.0)
    return [item for page in client.pages(PATH, {"state": "all"}) for item in page]


def message(fake, host: str = "github.com") -> str:
    with pytest.raises(HeinzelError) as caught:
        items(fake, host)
    return str(caught.value)


def test_gh_api_is_called_with_an_argument_list_naming_host_method_headers_and_raw_fields(fake_gh):
    fake_gh.serve(PATH, [{"id": 1}])
    assert items(fake_gh) == [{"id": 1}]
    call = fake_gh.calls[0]
    assert call.args == [
        "api",
        "--hostname",
        "github.com",
        "--method",
        "GET",
        "--header",
        "Accept: application/vnd.github+json",
        "--header",
        "X-GitHub-Api-Version: 2022-11-28",
        PATH,
        "--raw-field",
        "state=all",
        "--raw-field",
        "per_page=100",
        "--raw-field",
        "page=1",
    ]
    assert call.host == "github.com"
    assert 6.0 < call.timeout <= 7.0


def test_a_full_page_asks_for_the_next_one(fake_gh):
    fake_gh.serve(PATH, [{"id": i} for i in range(100)], [{"id": 100}])
    assert len(items(fake_gh)) == 101
    assert [c.fields["page"] for c in fake_gh.calls] == ["1", "2"]


def test_more_than_the_page_limit_asks_for_a_shorter_window(fake_gh):
    fake_gh.serve(PATH, *([{"id": i} for i in range(100)] for _ in range(MAX_PAGES)))
    with pytest.raises(HeinzelError, match=f"{MAX_PAGES} or more full pages"):
        items(fake_gh)


def test_not_logged_in_names_gh_auth_login_for_the_host(fake_gh):
    fake_gh.fail(PATH, 4, "To get started with GitHub CLI, please run:  gh auth login\n")
    assert message(fake_gh, "ghe.example.com") == (
        "`gh` is not logged in to ghe.example.com; run `gh auth login --hostname ghe.example.com`, then try again."
    )


def test_a_rejected_login_says_to_log_in_again_or_replace_the_environment_token(fake_gh):
    # What gh prints for a 401: the status, then a hint that names `gh auth login`.
    stderr = "gh: Bad credentials (HTTP 401)\nTry authenticating with:  gh auth login\n"
    fake_gh.fail(PATH, 1, stderr, {"message": "Bad credentials"})
    text = message(fake_gh)
    assert "rejected gh's login for github.com (401: Bad credentials)" in text
    assert "gh auth login --hostname github.com" in text
    assert "GH_TOKEN" in text


def test_too_many_requests_is_a_rate_limit_whatever_it_says(fake_gh):
    fake_gh.fail(PATH, 1, "gh: Slow down (HTTP 429)\n", {"message": "Slow down"})
    assert "rate limit is reached" in message(fake_gh)


def test_not_found_says_to_check_spelling_and_access(fake_gh):
    fake_gh.fail(PATH, 1, "gh: Not Found (HTTP 404)\n", {"message": "Not Found"})
    text = message(fake_gh)
    assert f"GitHub has no {PATH}" in text
    assert "gh auth status --hostname github.com" in text


def test_forbidden_says_which_access_is_needed(fake_gh):
    fake_gh.fail(PATH, 1, "gh: Resource not accessible (HTTP 403)\n", {"message": "Resource not accessible"})
    assert "needs read access to its pull requests and issues" in message(fake_gh)


@pytest.mark.parametrize("status", [403, 429])
def test_a_rate_limit_says_to_wait_and_how_to_see_the_reset(fake_gh, status):
    fake_gh.fail(PATH, 1, f"gh: API rate limit exceeded (HTTP {status})\n", {"message": "API rate limit exceeded"})
    text = message(fake_gh)
    assert "rate limit is reached" in text
    assert "gh api rate_limit --hostname github.com" in text


def test_the_status_is_gh_s_last_one_not_one_github_s_message_quotes(fake_gh):
    stderr = "gh: says (HTTP 401)\nmore text\ngh: Not Found (HTTP 404)\n"
    fake_gh.fail(PATH, 1, stderr, {"message": "says (HTTP 401)"})
    assert f"GitHub has no {PATH}" in message(fake_gh)


def test_a_whole_read_has_one_byte_budget(fake_gh, monkeypatch):
    monkeypatch.setattr(_client, "MAX_READ_BYTES", 5000)
    fake_gh.serve(PATH, *([{"id": i} for i in range(100)] for _ in range(5)))
    with pytest.raises(HeinzelError, match="more than 5000 bytes in all; ask for a shorter window"):
        items(fake_gh)


def test_a_whole_read_has_one_deadline(fake_gh, monkeypatch):
    monkeypatch.setattr(_client, "READ_TIMEOUTS", 0)
    fake_gh.serve(PATH, [])
    with pytest.raises(HeinzelError, match="took more than 0 seconds; ask for a shorter window"):
        items(fake_gh)
    assert fake_gh.calls == []


def test_each_call_gets_at_most_what_is_left_of_the_read(fake_gh):
    fake_gh.serve(PATH, [])
    items(fake_gh)
    assert 0 < fake_gh.calls[0].timeout <= 7.0


def test_another_status_is_shown_with_githubs_message(fake_gh):
    fake_gh.fail(PATH, 1, "gh: Server Error (HTTP 502)\n", {"message": "Server Error"})
    assert message(fake_gh) == f"GitHub answered {PATH} with 502: Server Error."


def test_a_failure_without_a_status_shows_gh_s_first_line(fake_gh):
    fake_gh.fail(PATH, 1, "\nerror connecting to api.github.com\ncheck your internet connection\n")
    text = message(fake_gh)
    assert "failed (exit code 1): error connecting to api.github.com." in text
    assert "check your internet connection" not in text


def test_a_timeout_names_the_setting_that_allows_more_time(fake_gh):
    fake_gh.fail(PATH, None)
    text = message(fake_gh)
    assert "did not finish within 7 seconds" in text
    assert "hz system config set github.timeout 120" in text


def test_an_answer_over_the_limit_is_refused(fake_gh):
    fake_gh.routes[PATH] = [GhResult(0, b"[", "", truncated=True)]
    assert "is larger than" in message(fake_gh)


@pytest.mark.parametrize("body", [b"not json", b'{"message": "an object"}', b"[1, 2]"])
def test_an_answer_that_is_not_a_list_of_objects_is_a_clear_error(fake_gh, body):
    fake_gh.routes[PATH] = [GhResult(0, body, "")]
    assert "GitHub" in message(fake_gh) or "not JSON" in message(fake_gh)


def test_messages_never_carry_a_token_even_where_they_are_cut(fake_gh):
    # The token straddles the cut: redacting after cutting would leave its first part behind.
    text = "x" * (MAX_SAID - 10) + TOKEN
    fake_gh.fail(PATH, 1, "gh: Server Error (HTTP 500)\n", {"message": text})
    shown = message(fake_gh)
    assert TOKEN[:10] not in shown
    assert "ghp_" not in shown


def test_messages_lose_control_and_bidirectional_characters(fake_gh):
    fake_gh.fail(PATH, 1, "gh: Server Error (HTTP 500)\n", {"message": "evil\x1b[31m\u202eoops\u2066"})
    assert message(fake_gh) == f"GitHub answered {PATH} with 500: evil[31moops."


@pytest.mark.parametrize(
    "token",
    [TOKEN, "gho_" + "x" * 36, "ghs_" + "y" * 36, "github_pat_" + "Z9" * 20, "0123456789abcdef" * 2 + "01234567"],
)
def test_redact_hides_every_github_token_shape(token):
    assert redact(f"before {token} after") == "before *** after"


def test_said_has_a_placeholder_for_nothing():
    assert said("\u200b ") == "no details"
