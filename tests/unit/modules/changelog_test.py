import json
import urllib.error

import pytest
from rich.console import Console

from heinzel import HeinzelError, __version__
from heinzel.kernel import Installation, Progress
from heinzel.modules.system import ReleaseNotes, _github
from heinzel.modules.system import changelog as changelog_module
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
            release("v1.2.0", published_at="yesterday", html_url="https://evil.example/x", body=None),
            release("v1.10.0"),
            release("v2.0.0rc1", prerelease=False),
            release("v1.9.0", prerelease=True),
            release("v1.8.0", draft=True),
            release("not-a-version"),
            "no release",
            release("v1.9.5"),
            release("1.9.5", body="the same release, tagged twice"),
            release("v1.0.0"),
        ]
    ]
    newer = GitHubReleases("org/heinzel").newer_than("1.0.0", 5)
    assert [(r.version, r.date and r.date.isoformat(), r.url, r.notes) for r in newer] == [
        ("1.10.0", "2026-10-01", "https://github.com/org/heinzel/releases/tag/v1.10.0", "Notes of v1.10.0."),
        ("1.9.5", "2026-10-01", "https://github.com/org/heinzel/releases/tag/v1.9.5", "Notes of v1.9.5."),
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


RLO, LRI, RLM, ZWSP, ZWJ, BOM, LINE_SEPARATOR = map(chr, (0x202E, 0x2066, 0x200F, 0x200B, 0x200D, 0xFEFF, 0x2028))
TAG_A = chr(0xE0041)
"""A Unicode tag character: invisible to people, read by models."""


def test_notes_are_third_party_text_without_controls_or_invisible_characters(github):
    body = f"a\x1b[31mred\x1b]52;c;AAAA\x07{RLO}evil{LRI}x{RLM}\r\nnext\x9b{ZWSP}{BOM}{TAG_A}{LINE_SEPARATOR}!"
    github.pages = [[release("v2.0.0", body=body)]]
    [notes] = GitHubReleases("org/heinzel").newer_than("1.0.0", 5)
    assert notes.notes == "a[31mred]52;c;AAAAevilx\nnext!"


def test_a_run_of_variation_selectors_keeps_only_the_first():
    selectors = "".join(map(chr, (0xFE0F, 0xFE01, 0xFE02, 0xE0100, 0xE01EF)))
    assert clean(f"{chr(0xFE00)}a{selectors}b{chr(0xE0101)}") == f"a{chr(0xFE0F)}b"


def test_clean_keeps_tabs_line_feeds_and_emoji():
    family = f"x{ZWJ}y"
    assert clean(f"\ttab\nline{chr(0x061C)}\r{family}") == f"\ttab\nline\n{family}"


@pytest.mark.parametrize(
    "html_url",
    [
        f"https://github.com/org/heinzel/releases/tag/v2.0.0{RLO}",
        "https://github.com/someone-else/heinzel/releases/tag/v2.0.0",
        "https://github.com/org/heinzel/releases/tag/v2.0.0/../../../evil",
        "https://github.com/org/hein" + chr(0x212A) + "el/releases/tag/v2.0.0",
        None,
    ],
)
def test_only_a_plain_release_page_of_the_repository_is_its_url(github, html_url):
    github.pages = [[release("v2.0.0", html_url=html_url, body="x" * (MAX_NOTES + 10))]]
    [notes] = GitHubReleases("org/heinzel").newer_than("1.0.0", 5)
    assert notes.url is None
    assert notes.notes.endswith("read all of it on the release page)")


def test_long_notes_are_cut_with_a_pointer_outside_any_code_block(github):
    github.pages = [[release("v3.0.0", body="```\n" + "x" * MAX_NOTES)]]
    [notes] = GitHubReleases("org/heinzel").newer_than("1.0.0", 5)
    assert notes.notes == (
        "```\n" + "x" * (MAX_NOTES - 4) + "\n```\n\n"
        "... (cut short; read all of it on https://github.com/org/heinzel/releases/tag/v3.0.0)"
    )


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
        (TypeError("the answer is no list of releases"), "no list"),
    ],
)
def test_changelog_newer_offline_is_one_clear_error(github, installation, error, reason):
    github.pages = [error]
    with pytest.raises(HeinzelError, match="Could not ask GitHub for the releases newer than") as raised:
        changelog(installation, newer=True)
    assert reason in str(raised.value)
    assert f"https://github.com/{DEFAULT_REPOSITORY}/releases" in str(raised.value)


def test_changelog_newer_needs_a_release_version(github, installation, monkeypatch):
    monkeypatch.setattr(changelog_module, "__version__", "not a version")
    with pytest.raises(HeinzelError, match="is no release version"):
        changelog(installation, newer=True)
    assert github.requests == []


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
    assert f"2 releases are newer than the installed hz {__version__}" in text
    assert "hz 9.0.0" in text
    assert "https://github.com/org/heinzel/releases/tag/v9.0.0" in text
    assert "https://example.org/docs" in text  # a link shows where it goes
    assert "hz 8.0.0 (2026-10-01)" in text
    assert "No release notes." in text
    assert text.rstrip().endswith("Update with: hz system update")


def test_render_says_when_nothing_is_newer_and_shows_the_changelog_as_before():
    assert rendered([]).strip() == f"hz {__version__} is the latest release; nothing newer to show."
    assert rendered("# Changelog").strip() == "Changelog"
