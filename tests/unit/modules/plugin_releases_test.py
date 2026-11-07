"""Newer stable plugin releases from PyPI and GitHub release tags, for automatic updates (ADR 0116, ADR 0137)."""

import base64
from datetime import UTC, datetime

import pytest

from heinzel.kernel import CorePin, Installation, PluginRelease
from heinzel.modules.system._plugin_releases import PyPIPluginReleases, closure

pytestmark = pytest.mark.unit
UPLOAD = {"upload_time_iso_8601": "2026-10-01T08:00:00.000000Z", "yanked": False}


class Receipt(Installation):
    def __init__(self, pins, versions):
        self.pins, self.versions = pins, versions

    def plugin_pins(self):
        return self.pins

    def installed_versions(self, names):
        return {name: self.versions[name] for name in names if name in self.versions}


def test_only_index_plugins_with_a_newer_stable_release_allowed_by_their_specifier_count():
    pins = [
        CorePin("index", "heinzel-newer"),
        CorePin("index", "heinzel-current"),
        CorePin("index", "heinzel-capped<2"),
        CorePin("index", "heinzel-pre"),
        CorePin("ref", "heinzel-git @ git+https://example.test/x@main", ref="main"),
    ]
    versions = {"heinzel-newer": "1.0.0", "heinzel-current": "1.2.0", "heinzel-capped": "1.0.0", "heinzel-pre": "1.0"}
    latest = {"heinzel-newer": "1.1.0", "heinzel-current": "1.2.0", "heinzel-capped": "2.0.0", "heinzel-pre": "2.0b1"}
    asked = []

    def fetch(url, timeout):
        asked.append(url)
        return {"releases": {"1.0.0": [UPLOAD], latest[url.split("/")[-2]]: [UPLOAD]}}

    releases = PyPIPluginReleases(Receipt(pins, versions), fetch)
    assert releases.newer(5.0) == {"heinzel-newer": "1.1.0"}
    assert all(url.startswith("https://pypi.org/pypi/heinzel-") for url in asked)
    assert len(asked) == 4  # the git plugin is never asked about


def test_no_index_plugins_asks_nothing():
    def fetch(url, timeout):
        raise AssertionError(url)

    assert PyPIPluginReleases(Receipt([], {}), fetch).newer(5.0) == {}


def test_a_yanked_release_is_never_offered_and_its_age_is_its_last_upload():
    def fetch(url, timeout):
        return {
            "releases": {
                "1.0.0": [UPLOAD],
                "1.1.0": [
                    {"upload_time_iso_8601": "2026-11-01T10:00:00Z", "yanked": False},
                    {"upload_time_iso_8601": "2026-11-01T09:00:00Z", "yanked": False},
                ],
                "1.2.0": [{"upload_time_iso_8601": "2026-11-02T08:00:00Z", "yanked": True}],
                "1.3.0b1": [UPLOAD],
                "1.4.0": [],
            },
        }

    releases = PyPIPluginReleases(Receipt([CorePin("index", "heinzel-x")], {"heinzel-x": "1.0.0"}), fetch)
    assert releases.newer_releases(5.0) == {
        "heinzel-x": PluginRelease("1.1.0", datetime(2026, 11, 1, 10, 0, tzinfo=UTC)),  # the last upload
    }


def test_one_release_is_as_old_as_its_last_file_and_a_yanked_one_is_none():
    asked = []

    def fetch(url, timeout):
        asked.append(url)
        return {
            "urls": [
                {"upload_time_iso_8601": "2026-10-01T08:00:00Z", "yanked": False},
                {"upload_time_iso_8601": "2026-11-02T08:00:00Z", "yanked": "x" in url},  # added today
            ]
        }

    releases = PyPIPluginReleases(Receipt([], {}), fetch)
    assert releases.release("heinzel", "1.2.3", 5.0) == PluginRelease("1.2.3", datetime(2026, 11, 2, 8, tzinfo=UTC))
    assert asked == ["https://pypi.org/pypi/heinzel/1.2.3/json"]
    assert releases.release("heinzel-x", "1.0.0", 5.0) is None


# Plugins pinned to a GitHub commit, followed by their release tags (ADR 0137) -----------------------------------------

REPO = "https://github.com/acme-example/heinzel-plugins"
API = "repos/acme-example/heinzel-plugins"
OLD = "a" * 40
NEW = {"heinzel-weather": "b" * 40, "heinzel-maps": "d" * 40, "heinzel-clock": "e" * 40}
TAGGED = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)


def tagged_pin(name):
    return CorePin(
        "commit",
        f"{name} @ git+{REPO}@{OLD}#subdirectory=plugins/{name}",
        ref=OLD,
        name=name,
        git=REPO,
        subdirectory=f"plugins/{name}",
    )


def project(name, version, dependencies=()):
    deps = ", ".join(f'"{d}"' for d in dependencies)
    text = f'[project]\nname = "{name}"\nversion = "{version}"\ndependencies = [{deps}]\n'
    return {"type": "file", "content": base64.b64encode(text.encode()).decode()}


class GitHub:
    """GitHub's API as the getter reads it: answers by path, `LookupError` for anything it doesn't have."""

    def __init__(self, tags, projects=None, dates=None):
        self.answers, self.asked = {}, []
        for name, versions in tags.items():
            self.answers[f"{API}/git/matching-refs/tags/{name}/v"] = [
                {"ref": f"refs/tags/{name}/v{v}", "object": {"sha": sha, "type": "commit"}} for v, sha in versions
            ]
            for version, sha in versions:
                self.answers[f"{API}/git/ref/tags/{name}/v{version}"] = {"object": {"sha": sha, "type": "commit"}}
                when = (dates or {}).get(sha, TAGGED)
                self.answers[f"{API}/commits/{sha}"] = {"sha": sha, "commit": {"committer": {"date": when.isoformat()}}}
        for (name, sha), answer in (projects or {}).items():
            self.answers[f"{API}/contents/plugins/{name}/pyproject.toml?ref={sha}"] = answer

    def __call__(self, path):
        self.asked.append(path)
        if path not in self.answers:
            raise LookupError(path)
        return self.answers[path]


def weather(dependencies=(), version="0.4.1"):
    return {("heinzel-weather", NEW["heinzel-weather"]): project("heinzel-weather", version, dependencies)}


def test_a_plugin_pinned_to_a_github_commit_is_offered_its_newest_stable_tag_with_the_commit_it_names():
    github = GitHub(
        {
            "heinzel-weather": [
                ("0.4.0", OLD),
                ("0.4.1", NEW["heinzel-weather"]),
                ("0.5.0rc1", "f" * 40),  # a pre-release is never offered
                ("0.5.0.dev3", "f" * 40),
            ]
        }
    )
    receipt = Receipt([tagged_pin("heinzel-weather")], {"heinzel-weather": "0.4.0"})
    releases = PyPIPluginReleases(receipt, github=github)
    assert releases.newer_releases(5.0) == {
        "heinzel-weather": PluginRelease("0.4.1", TAGGED, commit=NEW["heinzel-weather"]),  # as old as its commit
    }
    assert github.asked[0] == f"{API}/git/matching-refs/tags/heinzel-weather/v"  # one listing of its own tags
    assert len(github.asked) == 2  # and the commit of the newest one: bounded


def test_an_annotated_tag_is_as_old_as_the_later_of_its_commit_and_itself():
    github = GitHub({})
    sha, tag = NEW["heinzel-weather"], "9" * 40
    github.answers[f"{API}/git/matching-refs/tags/heinzel-weather/v"] = [
        {"ref": "refs/tags/heinzel-weather/v0.4.1", "object": {"sha": tag, "type": "tag"}}
    ]
    github.answers[f"{API}/git/tags/{tag}"] = {
        "object": {"sha": sha, "type": "commit"},
        "tagger": {"date": "2026-11-06T08:00:00Z"},  # tagged yesterday on an older commit
    }
    github.answers[f"{API}/commits/{sha}"] = {"commit": {"committer": {"date": TAGGED.isoformat()}}}
    receipt = Receipt([tagged_pin("heinzel-weather")], {"heinzel-weather": "0.4.0"})
    found = PyPIPluginReleases(receipt, github=github).newer_releases(5.0)
    assert found == {"heinzel-weather": PluginRelease("0.4.1", datetime(2026, 11, 6, 8, tzinfo=UTC), commit=sha)}


def test_git_plugins_on_a_branch_or_another_host_and_untagged_ones_are_never_offered_and_stay_by_hand():
    pins = [
        CorePin("ref", f"heinzel-crm @ git+{REPO}@main", ref="main", name="heinzel-crm", git=REPO),
        CorePin("commit", f"heinzel-x @ git+https://example.test/x@{OLD}", ref=OLD, git="https://example.test/x"),
        tagged_pin("heinzel-maps"),  # its repository has no tags of its own
    ]
    github = GitHub({"heinzel-maps": []})
    releases = PyPIPluginReleases(
        Receipt(pins, {"heinzel-crm": "1", "heinzel-x": "1", "heinzel-maps": "1"}), github=github
    )
    news = releases.announceable(5.0, lambda release: True)
    assert (news.releases, news.followed) == ({}, [])
    assert github.asked == [f"{API}/git/matching-refs/tags/heinzel-maps/v"]  # nobody else is asked


def test_a_tag_too_new_is_not_announced_but_the_plugin_is_followed():
    github = GitHub({"heinzel-weather": [("0.4.1", NEW["heinzel-weather"])]}, weather())
    receipt = Receipt([tagged_pin("heinzel-weather")], {"heinzel-weather": "0.4.0"})
    news = PyPIPluginReleases(receipt, github=github, requires=lambda name: []).announceable(5.0, lambda r: False)
    assert (news.releases, news.held, news.followed) == ({}, [], ["heinzel-weather"])


def test_a_tagged_commit_must_say_the_version_of_its_tag():
    github = GitHub({"heinzel-weather": [("0.4.1", NEW["heinzel-weather"])]}, weather(version="0.4.0"))
    receipt = Receipt([tagged_pin("heinzel-weather")], {"heinzel-weather": "0.4.0"})
    news = PyPIPluginReleases(receipt, github=github, requires=lambda name: []).announceable(5.0, lambda r: True)
    assert news.releases == {}
    assert news.held == ["heinzel-weather 0.4.1 waits: its tag names a commit that doesn't say that version."]


def test_the_tag_is_asked_again_for_the_commit_it_names_now():
    github = GitHub({"heinzel-weather": [("0.4.1", NEW["heinzel-weather"])]})
    releases = PyPIPluginReleases(Receipt([tagged_pin("heinzel-weather")], {}), github=github)
    assert releases.tag_commit("heinzel-weather", "0.4.1", 5.0) == NEW["heinzel-weather"]
    github.answers[f"{API}/git/ref/tags/heinzel-weather/v0.4.1"] = {"object": {"sha": "7" * 40, "type": "commit"}}
    assert releases.tag_commit("heinzel-weather", "0.4.1", 5.0) == "7" * 40  # moved: the caller refuses it
    assert releases.tag_commit("heinzel-weather", "0.9.9", 5.0) is None  # gone
    assert releases.tag_commit("heinzel-other", "0.4.1", 5.0) is None


# Plugins that need plugins --------------------------------------------------------------------------------------------


def three_plugins(*, maps_young=False, maps_tag="0.3.0", maps_needs=(), clock_requires=()):
    """heinzel-weather 0.4.1 needs heinzel-maps>=0.3; heinzel-clock moves on its own."""
    github = GitHub(
        {
            "heinzel-weather": [("0.4.1", NEW["heinzel-weather"])],
            "heinzel-maps": [(maps_tag, NEW["heinzel-maps"])],
            "heinzel-clock": [("1.1.0", NEW["heinzel-clock"])],
        },
        {
            **weather(["heinzel>=1", "heinzel-maps>=0.3"]),
            ("heinzel-maps", NEW["heinzel-maps"]): project("heinzel-maps", maps_tag, maps_needs),
            ("heinzel-clock", NEW["heinzel-clock"]): project("heinzel-clock", "1.1.0"),
        },
        dates={NEW["heinzel-maps"]: datetime(2026, 11, 7, tzinfo=UTC)} if maps_young else None,
    )
    names = ["heinzel-weather", "heinzel-maps", "heinzel-clock"]
    installed = {"heinzel-weather": "0.4.0", "heinzel-maps": "0.2.0", "heinzel-clock": "1.0.0"}
    requires = {"heinzel-weather": ["heinzel-maps>=0.2"], "heinzel-maps": [], "heinzel-clock": list(clock_requires)}
    return PyPIPluginReleases(
        Receipt([tagged_pin(n) for n in names], installed), github=github, requires=lambda name: requires[name]
    )


def old_enough(release):
    return release.uploaded_at is not None and release.uploaded_at < datetime(2026, 11, 1, tzinfo=UTC)


def test_a_plugin_that_needs_another_newer_is_announced_with_it_dependencies_first():
    news = three_plugins().announceable(5.0, old_enough)
    assert list(news.releases) == ["heinzel-clock", "heinzel-maps", "heinzel-weather"]  # maps before weather
    assert news.releases["heinzel-weather"].commit == NEW["heinzel-weather"]
    assert news.held == []


def test_a_plugin_whose_dependency_is_too_new_waits_too_and_unrelated_ones_move():
    news = three_plugins(maps_young=True).announceable(5.0, old_enough)
    assert list(news.releases) == ["heinzel-clock"]  # unrelated: moves all the same
    assert news.held == [
        (
            "heinzel-weather 0.4.1 waits for heinzel-maps: it needs heinzel-maps>=0.3, whose update to 0.3.0 can't "
            "be installed yet."
        )
    ]


def test_a_plugin_whose_dependency_has_no_release_that_fits_waits():
    news = three_plugins(maps_tag="0.2.5").announceable(5.0, old_enough)
    assert sorted(news.releases) == ["heinzel-clock", "heinzel-maps"]
    assert news.held == [
        "heinzel-weather 0.4.1 waits for heinzel-maps: it needs heinzel-maps>=0.3, which would move to 0.2.5."
    ]


def test_a_dependency_an_installed_plugin_does_not_allow_waits_and_so_does_what_needs_it():
    news = three_plugins(clock_requires=["heinzel-maps<0.3"]).announceable(5.0, lambda r: r.version != "1.1.0")
    assert news.releases == {}  # heinzel-clock (too new here) stays and caps heinzel-maps
    assert news.held == [
        "heinzel-maps 0.3.0 waits: heinzel-clock 1.0.0 needs heinzel-maps<0.3.",
        (
            "heinzel-weather 0.4.1 waits for heinzel-maps: it needs heinzel-maps>=0.3, whose update to 0.3.0 can't "
            "be installed yet."
        ),
    ]


def test_the_closure_reads_extras_and_markers_and_ignores_packages_that_are_no_plugins():
    order, held = closure(
        {"a": "1.0", "b": "1.0"},
        {"a": [], "b": []},
        {"a": "2.0", "b": "2.0"},
        {"a": ['b>=2; python_version >= "3"', 'b<1; extra == "old"', "httpx>=0.28", "heinzel>=1"], "b": []},
        {"a": "2.0", "b": "2.0"},
    )
    assert (order, held) == (["b", "a"], [])


def test_a_pypi_plugin_that_needs_a_tagged_one_moves_with_it():
    github = GitHub(
        {"heinzel-maps": [("0.3.0", NEW["heinzel-maps"])]},
        {
            ("heinzel-maps", NEW["heinzel-maps"]): project("heinzel-maps", "0.3.0"),
        },
    )

    def fetch(url, timeout):
        if url.endswith("/heinzel-route/json"):
            return {"releases": {"2.0.0": [UPLOAD]}}
        return {"info": {"requires_dist": ["heinzel-maps>=0.3"]}}

    pins = [CorePin("index", "heinzel-route"), tagged_pin("heinzel-maps")]
    receipt = Receipt(pins, {"heinzel-route": "1.0.0", "heinzel-maps": "0.2.0"})
    news = PyPIPluginReleases(receipt, fetch, github=github, requires=lambda name: []).announceable(5.0, old_enough)
    assert list(news.releases) == ["heinzel-maps", "heinzel-route"]
