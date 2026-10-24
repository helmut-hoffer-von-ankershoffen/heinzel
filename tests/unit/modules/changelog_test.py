import json
import urllib.error

import pytest
from rich.console import Console

from heinzel import HeinzelError, __version__
from heinzel.kernel import Installation, Progress
from heinzel.modules.system import ReleaseNotes, _github
from heinzel.modules.system._service import DEFAULT_REPOSITORY, MAX_NOTES, PAGE_SIZE, GitHubReleases, clean
from heinzel.modules.system.changelog import as_markdown, changelog, render

pytestmark = pytest.mark.unit


def test_placeholders_and_brackets_are_shown_verbatim():
    text = "- Name plugins heinzel-<name>, see [link] and `heinzel-<name>` [bold]x[/bold]"
    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        console.print(as_markdown(text))
    assert capture.get().strip() == "• " + text.removeprefix("- ").replace("`", "")


def release(tag: str, **fields: object) -> dict[str, object]:
    return {
        "tag_name": tag,
        "published_at": "2026-10-01T12:00:00Z",
        "html_url": f"https://github.com/org/heinzel/releases/tag/{tag}",
        "body": f"Notes of {tag}.",
        "draft": False,
        "prerelease": False,
    } | fields


class FakeGitHub:
    """Answers each request with the next of `pages` (raising it if it is an exception) and records url and headers."""

    def __init__(self) -> None:
        self.pages: list[object] = [[]]
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, dict(request.header_items()), timeout))
        answer = self.pages[min(len(self.requests), len(self.pages)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer).encode()


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(_github, "_open", fake.open)
    return fake


def test_newer_releases_come_newest_first_without_drafts_prereleases_or_odd_tags(github):
    github.pages = [
        [
            release("v1.10.0"),
            release("v2.0.0rc1", prerelease=False),
            release("v1.9.0", prerelease=True),
            release("v1.8.0", draft=True),
            release("not-a-version"),
            release("v1.2.0", published_at="yesterday", html_url="https://evil.example/x", body=None),
            "no release",
            release("v1.0.0"),
        ]
    ]
    newer = GitHubReleases("org/heinzel").newer_than("1.0.0", 5)
    assert [(r.version, r.date and r.date.isoformat(), r.url, r.notes) for r in newer] == [
        ("1.10.0", "2026-10-01", "https://github.com/org/heinzel/releases/tag/v1.10.0", "Notes of v1.10.0."),
        ("1.2.0", None, None, ""),
    ]
    [(url, headers, timeout)] = github.requests
    assert url == f"https://api.github.com/repos/org/heinzel/releases?per_page={PAGE_SIZE}&page=1"
    assert "Authorization" not in headers  # never a token (ADR 0031)
    assert headers["User-agent"] == f"heinzel/{__version__}"
    assert timeout == 5


def test_pages_are_asked_until_one_reaches_the_installed_version_and_never_more_than_four(github):
    full = [release(f"v9.{n}.0") for n in range(PAGE_SIZE)]
    github.pages = [full, [*full[:-1], release("v0.1.0")]]
    assert len(GitHubReleases("org/heinzel").newer_than("1.0.0", 5)) == PAGE_SIZE
    assert len(github.requests) == 2
    github.requests.clear()
    github.pages = [full]
    GitHubReleases("org/heinzel").newer_than("1.0.0", 5)
    assert len(github.requests) == 4


def test_notes_are_third_party_text_cleaned_of_controls_and_bidi_and_cut_short(github):
    github.pages = [
        [
            release("v2.0.0", body="a\x1b[31mred\x1b]52;c;AAAA\x07\u202eevil\u2066x\u200f\r\nnext\x9b"),
            release("v3.0.0", body="x" * (MAX_NOTES + 10)),
        ]
    ]
    long, short = GitHubReleases("org/heinzel").newer_than("1.0.0", 5)
    assert short.notes == "a[31mred]52;c;AAAAevilx\nnext"
    assert len(long.notes) < MAX_NOTES + 100
    assert long.notes.endswith("read all of it on https://github.com/org/heinzel/releases/tag/v3.0.0)")
    assert clean("\ttab\nline\u061c") == "\ttab\nline"


@pytest.mark.parametrize("answer", [{"message": "Not Found"}, "text"])
def test_an_answer_that_is_no_list_is_an_error(github, answer):
    github.pages = [answer]
    with pytest.raises(TypeError):
        GitHubReleases("org/heinzel").newer_than("1.0.0", 5)


@pytest.fixture
def installation(tmp_path):
    return Installation(Progress(), prefix=tmp_path, uv="uv")


def test_changelog_without_newer_never_asks(github, installation):
    assert changelog(installation).startswith("# Changelog")
    assert github.requests == []


def test_changelog_newer_asks_the_installed_repository(github, installation):
    github.pages = [[release("v99.0.0"), release(f"v{__version__}")]]
    [notes] = changelog(installation, newer=True)
    assert notes.version == "99.0.0"
    assert f"repos/{DEFAULT_REPOSITORY}/releases" in github.requests[0][0]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (urllib.error.URLError("nodename nor servname provided"), "nodename nor servname provided"),
        (urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None), "HTTP Error 403: rate limit exceeded"),  # type: ignore[arg-type]
        (TimeoutError("timed out"), "timed out"),
        (ValueError("the answer is larger than 1000000 bytes"), "larger than"),
    ],
)
def test_changelog_newer_offline_is_one_clear_error(github, installation, error, reason):
    github.pages = [error]
    with pytest.raises(HeinzelError, match="Could not ask GitHub for the releases newer than") as raised:
        changelog(installation, newer=True)
    assert reason in str(raised.value)
    assert f"https://github.com/{DEFAULT_REPOSITORY}/releases" in str(raised.value)


def rendered(result) -> str:
    console = Console(width=100, force_terminal=False)
    with console.capture() as capture:
        console.print(render(result))
    return capture.get()


def test_render_shows_each_newer_release_with_date_page_and_notes_and_link_targets():
    text = rendered(
        [
            ReleaseNotes(
                version="9.0.0",
                date=None,
                notes="- see [the docs](https://example.org/docs)",
                url="https://github.com/org/heinzel/releases/tag/v9.0.0",
            ),
            ReleaseNotes.model_validate({"version": "8.0.0", "date": "2026-10-01", "notes": "", "url": None}),
        ]
    )
    assert f"2 release(s) newer than the installed hz {__version__}" in text
    assert "hz 9.0.0" in text
    assert "https://github.com/org/heinzel/releases/tag/v9.0.0" in text
    assert "https://example.org/docs" in text  # a link shows where it goes
    assert "hz 8.0.0 (2026-10-01)" in text
    assert "No release notes." in text
    assert text.rstrip().endswith("Update with: hz system update")


def test_render_says_when_nothing_is_newer_and_shows_the_changelog_as_before():
    assert rendered([]).strip() == f"hz {__version__} is the latest release; nothing newer to show."
    assert rendered("# Changelog").strip() == "Changelog"
