"""Which sources `hz system plugin add` takes, and what it reads about them before installing (ADR 0047)."""

import base64
import io
import json
import subprocess
import urllib.error

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.plugin import _sources

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("root", ["virtual", "tooling", "absent"])
def test_repository_url_selects_its_metadata_declared_collection_project_at_one_commit(named, root):
    source = _sources.parse(("heinzel-harbor @ git+" if named else "") + "https://github.com/example/collection")
    revision = "a" * 40
    requests = []
    project = (
        '[project]\nname="heinzel-harbor"\nversion="1.0.0"\n[project.entry-points."heinzel.modules"]\nharbor="harbor"\n'
    )

    def get(path):
        requests.append(path)
        if path == "repos/example/collection":
            return {"default_branch": "main", "owner": {"login": "example"}, "private": True}
        if "/commits/" in path:
            return {"sha": revision}
        if path.endswith(f"/plugins?ref={revision}"):
            return [{"type": "dir", "name": "arbitrary-folder"}]
        if "/arbitrary-folder/" in path:
            text = project
        elif root == "absent":
            msg = "No root pyproject"
            raise LookupError(msg)
        elif root == "tooling":
            text = '[project]\nname="collection-tooling"\nversion="1.0.0"\n'
        else:
            text = '[tool.uv.workspace]\nmembers=["plugins/*"]\n'
        # GitHub wraps base64 content with newlines, including a trailing newline.
        return {"type": "file", "content": base64.encodebytes(text.encode()).decode()}

    metadata = _sources.from_github(source, get)
    assert metadata.name == "heinzel-harbor"
    assert metadata.subdirectory == "plugins/arbitrary-folder"
    assert metadata.revision == revision
    assert metadata.private is True
    assert all(path.endswith(f"ref={revision}") for path in requests if "/contents/" in path)


@pytest.mark.parametrize("named", [False, True])
def test_an_ambiguous_collection_requires_a_distribution_instead_of_installing_a_virtual_root(named):
    source = _sources.parse(("heinzel-two @ git+" if named else "") + "https://github.com/example/collection")

    def get(path):
        if path == "repos/example/collection":
            return {"default_branch": "main"}
        if "/commits/" in path:
            return {"sha": "a" * 40}
        if "/contents/plugins?" in path:
            return [{"type": "dir", "name": "one"}, {"type": "dir", "name": "two"}]
        if "/contents/pyproject.toml" in path:
            text = '[tool.uv.workspace]\nmembers=["plugins/*"]\n'
        else:
            folder = path.split("/plugins/")[1].split("/")[0]
            text = (
                f'[project]\nname="heinzel-{folder}"\n[project.entry-points."heinzel.modules"]\n{folder}="{folder}"\n'
            )
        return {"type": "file", "content": base64.b64encode(text.encode()).decode()}

    if named:
        metadata = _sources.from_github(source, get)
        assert (metadata.name, metadata.subdirectory) == ("heinzel-two", "plugins/two")
    else:
        with pytest.raises(HeinzelError, match="no unique plugin"):
            _sources.from_github(source, get)


@pytest.mark.parametrize(
    "entries", [[], [{"type": "symlink", "name": "harbor"}], [{"type": "dir", "name": "../escape"}]]
)
def test_collection_discovery_never_follows_links_or_unsafe_paths(entries):
    source = _sources.parse("https://github.com/example/collection")
    requests = []

    def get(path):
        requests.append(path)
        if path == "repos/example/collection":
            return {"default_branch": "main"}
        if "/commits/" in path:
            return {"sha": "a" * 40}
        if "/contents/plugins?" in path:
            return entries
        assert "/contents/pyproject.toml?" in path
        return {"type": "file", "content": base64.b64encode(b"[tool.uv.workspace]").decode()}

    with pytest.raises(HeinzelError, match="no unique plugin"):
        _sources.from_github(source, get)
    assert len(requests) == 4


def test_github_resolved_metadata_is_read_at_the_same_pinned_commit():
    requests = []
    # Contents resolves an in-repository symlink to this file response at the requested commit.
    resolved = {"type": "file", "content": base64.encodebytes(b'[project]\nname="heinzel-harbor"').decode()}
    project = _sources._github_project_at(
        lambda path: requests.append(path) or resolved, "repos/example/collection", "a" * 40, "plugins/harbor"
    )
    assert project["name"] == "heinzel-harbor"
    assert requests == [f"repos/example/collection/contents/plugins/harbor/pyproject.toml?ref={'a' * 40}"]


@pytest.mark.parametrize("kind", [None, "dir", "symlink"])
def test_metadata_requires_a_bounded_github_file_response(kind):
    response = {"type": kind, "content": base64.b64encode(b"[project]").decode()}
    with pytest.raises(ValueError, match="bounded file response"):
        _sources._github_project_at(lambda _path: response, "repos/example/collection", "a" * 40, None)


@pytest.mark.parametrize(
    ("spec", "kind", "name", "url", "ref", "subdirectory"),
    [
        ("heinzel-crm", "pypi", "heinzel-crm", None, None, None),
        ("Heinzel_CRM>=1,<2", "pypi", "heinzel-crm", None, None, None),
        ("git+https://github.com/o/heinzel-crm", "git", None, "https://github.com/o/heinzel-crm", None, None),
        ("https://GitHub.com/o/heinzel-crm/", "git", None, "https://github.com/o/heinzel-crm", None, None),
        (
            "git+https://github.com/o/heinzel-crm@feat/x",
            "git",
            None,
            "https://github.com/o/heinzel-crm",
            "feat/x",
            None,
        ),
        (
            "heinzel-crm @ git+https://git.example.org/o/mono@v1#subdirectory=plugins/heinzel-crm",
            "git",
            "heinzel-crm",
            "https://git.example.org/o/mono",
            "v1",
            "plugins/heinzel-crm",
        ),
    ],
)
def test_pypi_packages_and_https_git_repositories_are_taken(spec, kind, name, url, ref, subdirectory):  # noqa: PLR0913, PLR0917 - one per column
    source = _sources.parse(spec)
    assert (source.kind, source.name, source.url, source.ref, source.subdirectory) == (
        kind,
        name,
        url,
        ref,
        subdirectory,
    )


@pytest.mark.parametrize(
    ("spec", "why"),
    [
        ("", "it is empty"),
        ("--index-url=https://evil.example", "looks like an option"),
        ("heinzel-x\n--with evil", "control characters"),
        (".", "local paths"),
        ("/home/ada/heinzel-x", "local paths"),
        ("~/heinzel-x", "local paths"),
        ("file:///home/ada/heinzel-x", "local paths"),
        ("C:\\plugins\\x", "local paths"),
        ("git+ssh://git@github.com/o/heinzel-x", "only git over https"),
        ("git+http://github.com/o/heinzel-x", "only git over https"),
        ("git+file:///srv/heinzel-x", "only git over https"),
        ("ssh://git@github.com/o/heinzel-x", "only https URLs"),
        ("git@github.com:o/heinzel-x.git", "only git repositories over https"),
        ("https://ada:t0ken@github.com/o/heinzel-x", "user name or token"),
        ("git+https://t0ken@github.com/o/heinzel-x", "user name or token"),
        ("https://github.com/o/heinzel-x?ref=main", "no query"),
        ("https://example.org/heinzel_x-1.0-py3-none-any.whl", "archives"),
        ("heinzel-x @ https://example.org/heinzel_x-1.0.tar.gz", "not archives"),
        ("git+https://github.com/o/heinzel-x#egg=heinzel-x", "#subdirectory="),
        ("git+https://github.com/o/heinzel-x@v1;rm", "no git ref"),
        ("git+https://github.com/o/mono#subdirectory=../../etc", "no git ref or subdirectory"),
        ("git+https://github.com", "names no repository"),
        ("https://github.com/good/repo/../../evil/x", "dot segments"),
        ("https://github.com/good/%2e%2e/evil", "dot segments or escapes"),
        ("git+https://github.com/o/heinzel-x@-upload-pack=evil", "no git ref"),
        ("git+https://github.com/o/heinzel-x@main.lock", "no git ref"),
        ("git+https://github.com/o/heinzel-x@feat/", "no git ref"),
        ("requests", "named heinzel-<name>"),
        ("heinzel", "hz system update"),
        ("not a requirement!", "no package name"),
    ],
)
def test_everything_else_is_refused_with_what_is_taken(spec, why):
    with pytest.raises(HeinzelError, match="takes a catalog short name") as refused:
        _sources.parse(spec)
    assert why in str(refused.value)


def test_a_github_source_knows_its_owner_and_repository():
    assert _sources.parse("https://github.com/o/heinzel-x.git").github == ("o", "heinzel-x")
    assert _sources.parse("https://git.example.org/o/heinzel-x").github is None
    assert _sources.parse("https://github.com/o/mono/sub").github is None
    assert _sources.parse("heinzel-x").github is None


def test_the_publisher_falls_back_to_the_authors_e_mail_name():
    source = _sources.parse("heinzel-x")
    answer = {"info": {"version": "1", "author_email": '"Ada Example" <ada@example.org>'}, "releases": {"1": [{}]}}
    assert _sources.from_pypi(source, lambda url: answer).publisher == "Ada Example"
    answer["info"]["author_email"] = "ada@example.org"
    assert _sources.from_pypi(source, lambda url: answer).publisher == "ada@example.org"
    del answer["info"]["author_email"]
    metadata = _sources.from_pypi(source, lambda url: answer)
    assert (metadata.publisher, metadata.page) == (None, "https://pypi.org/project/heinzel-x/")


def test_another_pypi_error_is_raised_as_it_came():
    error = urllib.error.HTTPError("https://pypi.org", 503, "Unavailable", {}, io.BytesIO())  # type: ignore[arg-type]

    def get(url):
        raise error

    with pytest.raises(urllib.error.HTTPError):
        _sources.from_pypi(_sources.parse("heinzel-x"), get)
    error.close()


def test_gh_reads_the_api_with_your_login(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "repos/o/missing":
            return subprocess.CompletedProcess(command, 1, "", "gh: Not Found (HTTP 404)")
        if command[-1] == "repos/o/broken":
            return subprocess.CompletedProcess(command, 1, "", "gh: Bad credentials (HTTP 401)")
        return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True}), "")

    monkeypatch.setattr(_sources.shutil, "which", lambda name: "/opt/bin/gh")
    monkeypatch.setattr(_sources.subprocess, "run", run)
    get = _sources.github_getter(None)
    assert get("repos/o/x") == {"ok": True}
    assert calls[0] == ["/opt/bin/gh", "api", "-H", "Accept: application/vnd.github+json", "repos/o/x"]
    with pytest.raises(LookupError, match="Not Found"):
        get("repos/o/missing")
    with pytest.raises(OSError, match="Bad credentials"):
        get("repos/o/broken")


def test_gh_timing_out_is_unreachable(monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(_sources.shutil, "which", lambda name: "/opt/bin/gh")
    monkeypatch.setattr(_sources.subprocess, "run", run)
    with pytest.raises(OSError, match="timed out"):
        _sources.github_getter(None)("repos/o/x")


def test_an_installed_but_unsigned_gh_falls_back_to_public_https(monkeypatch):
    monkeypatch.setattr(_sources.shutil, "which", lambda name: "/opt/bin/gh")
    monkeypatch.setattr(
        _sources.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 4, "", "To get started, run: gh auth login"),
    )
    requests = []
    monkeypatch.setattr(_sources, "get_json", lambda url, *_: requests.append(url) or {"public": True})
    assert _sources.github_getter(None)("repos/example/heinzel-harbor") == {"public": True}
    assert requests == ["https://api.github.com/repos/example/heinzel-harbor"]


def test_without_gh_public_repositories_are_read_over_https(monkeypatch):
    requests = []

    def get_json(url, token, timeout):
        requests.append(url)
        if url.endswith("/missing"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())  # type: ignore[arg-type]
        if url.endswith("/limited"):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO())  # type: ignore[arg-type]
        return {"ok": True}

    monkeypatch.setattr(_sources.shutil, "which", lambda name: None)
    monkeypatch.setattr(_sources, "get_json", get_json)
    get = _sources.github_getter(None)
    assert get("repos/o/x") == {"ok": True}
    assert requests == ["https://api.github.com/repos/o/x"]
    with pytest.raises(LookupError):
        get("repos/o/missing")
    with pytest.raises(urllib.error.HTTPError) as limited:
        get("repos/o/limited")
    limited.value.close()


def test_pypi_is_read_from_its_json_api_only(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(_sources.urllib.request, "urlopen", lambda request, timeout: Response(b'{"info": {}}'))
    assert _sources.pypi_get("https://pypi.org/pypi/heinzel-x/json") == {"info": {}}
    with pytest.raises(ValueError, match="not a PyPI JSON URL"):
        _sources.pypi_get("https://evil.example/pypi/heinzel-x/json")
    monkeypatch.setattr(_sources, "MAX_BYTES", 3)
    with pytest.raises(ValueError, match="larger than"):
        _sources.pypi_get("https://pypi.org/pypi/heinzel-x/json")


def test_git_asks_gh_only_when_gh_is_installed(monkeypatch):
    monkeypatch.setattr(_sources.shutil, "which", lambda name: None)
    assert _sources.git_credentials({}) == {}
    monkeypatch.setattr(_sources.shutil, "which", lambda name: "/Apps/My Tools/gh")
    assert _sources.git_credentials({"GIT_CONFIG_COUNT": "odd"}) == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_0": "!'/Apps/My Tools/gh' auth git-credential",
    }


def test_a_github_answer_without_a_pyproject_says_what_to_check():
    source = _sources.parse("https://github.com/o/heinzel-x")

    def get(path):
        if path == "repos/o/heinzel-x":
            return {"default_branch": "main", "owner": {"login": "o"}}
        if path.startswith("repos/o/heinzel-x/commits/"):
            return {"sha": "c" * 40}
        msg = "Not Found"
        raise LookupError(msg)

    with pytest.raises(HeinzelError, match="Check the URL"):
        _sources.from_github(source, get)


def test_browser_urls_are_https_without_credentials():
    assert _sources.browser_url(None) is None
    assert _sources.browser_url("http://example.org/x") is None
    assert _sources.browser_url("git+https://ada:t0ken@example.org/o/x.git/") == "https://example.org/o/x"


@pytest.mark.parametrize(
    ("url", "ref", "subdirectory", "expected"),
    [
        ("https://github.com/o/heinzel-x", "abc", None, "https://github.com/o/heinzel-x/tree/abc"),
        ("git+https://github.com/o/heinzel-x.git/", "v1", None, "https://github.com/o/heinzel-x/tree/v1"),
        ("https://ada:t0ken@github.com/o/mono", "abc", "plugins/x/", "https://github.com/o/mono/tree/abc/plugins/x"),
        ("ssh://git@github.com/o/heinzel-x.git", "abc", None, "https://github.com/o/heinzel-x/tree/abc"),
        ("git+ssh://git@github.com:22/o/heinzel-x", None, None, "https://github.com/o/heinzel-x"),
        ("git@github.com:o/heinzel-x.git", "abc", None, "https://github.com/o/heinzel-x/tree/abc"),
        ("https://github.com/o/heinzel-x", None, None, "https://github.com/o/heinzel-x"),
        ("https://ada:t0ken@gitlab.example.org/o/x.git", "abc", None, "https://gitlab.example.org/o/x"),
        ("https://gitlab.example.org/o/mono", "abc", "x", "https://gitlab.example.org/o/mono/tree/abc/x"),
        ("ssh://git@gitlab.example.org/o/x", "abc", None, None),
        ("git@gitlab.example.org:o/x.git", "abc", None, None),
        ("http://github.com/o/x", "abc", None, None),
        (None, "abc", None, None),
        ("", None, None, None),
    ],
)
def test_an_installed_repository_opens_at_the_code_installed(url, ref, subdirectory, expected):
    assert _sources.installed_repository_url(url, ref, subdirectory) == expected
