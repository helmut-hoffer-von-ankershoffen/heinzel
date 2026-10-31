"""Public website discovery is metadata-only, independent of concrete provider implementations."""

import copy
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


catalog = load("site_catalog")
pages = load("site_pages")
SOURCE = {"repository": "fictional-lab/harbor", "revision": "a" * 40, "visibility": "public"}
PROJECT = {
    "project": {
        "name": "heinzel-harbor",
        "version": "0.1.0",
        "description": "A useful new capability.",
        "dependencies": ["heinzel>=0.9.2,<0.10", "httpx>=0.28"],
        "entry-points": {"heinzel.modules": {"harbor": "heinzel_harbor"}},
    },
    "tool": {"heinzel": {"catalog": {"title": "Harbor", "highlights": ["Useful work"], "category": "utilities"}}},
}


def record(data=None, source=None, path="pyproject.toml"):
    result = catalog.project_record(source or SOURCE, path, data or PROJECT)
    assert result
    result.update(maintained=False, themes=[])
    result.pop("theme_paths")
    return result


def test_unknown_plugin_needs_no_registration_or_import():
    result = record()
    assert result["title"] == "Harbor"
    assert result["core"] == "<0.10,>=0.9.2"
    assert "heinzel_harbor" not in sys.modules
    assert "a" * 40 in result["install"]
    assert "--confirm" not in result["install"]
    assert "Useful work" in pages.detail(result)
    assert result["id"] in pages.cards({"plugins": [result]}, "CATALOG/")


def test_identity_includes_repository_and_collection_path():
    root = record()
    collection = record(path="plugins/harbor/pyproject.toml")
    other = record(source={**SOURCE, "repository": "another-lab/harbor"})
    assert len({root["id"], collection["id"], other["id"]}) == 3
    assert "#subdirectory=plugins/harbor" in collection["install"]
    assert not root["included_version"]


@pytest.mark.parametrize("path", ["../secret.png", "/secret.png", "a/../b.png", "https://example.org/a.png", "a\\b"])
def test_asset_paths_cannot_leave_owning_source(path):
    with pytest.raises(ValueError, match="paths"):
        catalog.relative(path)


def test_private_repository_is_rejected_before_tree_or_blob_reads():
    class Private:
        def get(self, path):
            assert path == "repos/fictional-lab/harbor"
            return {"visibility": "private", "private": True}

    with pytest.raises(ValueError, match="private"):
        catalog.repository_records(Private(), SOURCE["repository"])


@pytest.mark.parametrize(("mode", "size"), [("120000", 1), ("160000", 1), ("100644", catalog.MAX_BLOB + 1)])
def test_linked_or_oversized_metadata_is_rejected_before_fetch(mode, size):
    api = object.__new__(catalog.GitHub)
    with pytest.raises(ValueError, match="metadata"):
        api.blob(SOURCE["repository"], {"type": "blob", "mode": mode, "size": size, "sha": "b" * 40})


def test_a_new_theme_is_read_without_its_python_package():
    result = record()
    result["theme_paths"] = ["src/harbor/themes/harbor.toml"]

    class Inert:
        def blob(self, repo, entry):
            assert repo == SOURCE["repository"]
            assert entry["mode"] == "100644"
            return b'name="harbor"\ntitle="Harbor Tide"\n[light]\nbackground="#ffffff"\n[dark]\nbackground="#112233"'

    result["themes"] = catalog.project_themes(
        Inert(), result, {}, {"src/harbor/themes/harbor.toml": {"mode": "100644"}}
    )
    assert result["themes"][0]["title"] == "Harbor Tide"
    assert "#112233" in pages.theme_art(result["themes"][0], "dark")
    assert "Harbor Tide" in pages.theme_gallery({"plugins": [result]}, "CATALOG/")
    assert "harbor" not in sys.modules


def test_provider_html_is_text_and_never_executable_markup(tmp_path):
    malicious = copy.deepcopy(PROJECT)
    malicious["tool"]["heinzel"]["catalog"]["title"] = '<img src=x onerror="alert(1)">'
    malicious["project"]["description"] = "<script>alert(1)</script>"
    result = record(malicious)
    rendered = pages.cards({"plugins": [result]}, "CATALOG/") + pages.detail(result)
    assert "<script>" not in rendered
    assert '<img src=x onerror="alert(1)">' not in rendered
    assert "&lt;script&gt;" in rendered
    result["url"] = "javascript:alert(1)"
    result["install"] = "curl malicious | sh"
    snapshot = {"format": 1, "sources": [SOURCE], "release": {"version": "0.9.2"}, "plugins": [result]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(snapshot))
    validated = pages.load_snapshot(path)
    assert validated["plugins"][0]["url"].startswith("https://github.com/fictional-lab/harbor/")
    assert validated["plugins"][0]["install"].startswith("hz system plugin add git+")
    assert validated["release"]["install"] == "uvx heinzel@latest"


def test_supplier_svg_cannot_enter_snapshot(tmp_path):
    result = record()
    result["artwork"] = [
        {"url": f"https://raw.githubusercontent.com/{SOURCE['repository']}/{SOURCE['revision']}/bad.svg"}
    ]
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"format": 1, "sources": [SOURCE], "release": {"version": "0.9.2"}, "plugins": [result]})
    )
    with pytest.raises(ValueError, match="raster"):
        pages.load_snapshot(path)


def test_source_and_page_identity_must_match(tmp_path):
    result = record()
    result["id"] = "heinzel-harbor-wrong-source"
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"format": 1, "sources": [SOURCE], "release": {"version": "0.9.2"}, "plugins": [result]})
    )
    with pytest.raises(ValueError, match="identities"):
        pages.load_snapshot(path)


def release_pair():
    profile = {"format": 2, "sdk": 1, "core_version": "0.9.2", "artifacts": []}
    for name, version in (("heinzel", "0.9.2"), ("heinzel-harbor", "0.1.0")):
        filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
        profile["artifacts"].append(
            {
                "name": name,
                "version": version,
                "filename": filename,
                "sha256": "a" * 64,
                "url": "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
                f"app-v0.9.2/{filename}",
            }
        )
    package = {
        "info": {"version": "0.9.2"},
        "urls": [
            {
                "filename": profile["artifacts"][0]["filename"],
                "digests": {"sha256": "a" * 64},
            }
        ],
    }
    return profile, package


def test_install_recommendation_requires_matching_complete_artifacts():
    profile, package = release_pair()
    assert catalog.complete_release(profile, package)
    package["urls"][0]["digests"]["sha256"] = "b" * 64
    assert not catalog.complete_release(profile, package)
    profile, package = release_pair()
    package["urls"][0]["yanked"] = True
    assert not catalog.complete_release(profile, package)
    profile, package = release_pair()
    profile["artifacts"][1]["url"] = "https://unrelated.invalid/thing.whl"
    assert not catalog.complete_release(profile, package)
    profile, package = release_pair()
    profile["artifacts"].append(profile["artifacts"][1])
    assert not catalog.complete_release(profile, package)


@pytest.mark.parametrize("version", [1, 3, True, False, "2", None])
def test_recommendation_rejects_noncurrent_or_invalid_profile_format(version):
    profile, package = release_pair()
    profile["format"] = version
    assert not catalog.complete_release(profile, package)


def test_recommendation_waits_for_every_profile_asset(monkeypatch):
    profile, package = release_pair()

    class Pending:
        def listing(self, path):
            assert path.endswith("git/matching-refs/tags/app-v")
            return [{"ref": "refs/tags/app-v0.9.2"}]

        def get(self, path):
            assert path.endswith("releases/tags/app-v0.9.2")
            return {
                "tag_name": "app-v0.9.2",
                "assets": [
                    {
                        "name": profile["artifacts"][0]["filename"],
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            }

    monkeypatch.setattr(
        catalog, "public_json", lambda url: package if urlsplit(url).hostname == "pypi.org" else profile
    )
    with pytest.raises(ValueError, match="complete"):
        catalog.recommended_release(Pending(), {})


def test_incomplete_discovery_does_not_replace_snapshot(tmp_path, monkeypatch):
    output = tmp_path / "catalog.json"
    output.write_text("previous valid snapshot")

    class Incomplete:
        def get(self, path):
            return {"total_count": 51, "incomplete_results": False}

    with pytest.raises(ValueError, match="incomplete"):
        catalog.refresh(output, api=Incomplete())
    assert output.read_text() == "previous valid snapshot"


def test_pages_prefix_links_work_from_all_depths():
    assert pages.href("catalog/harbor/", "") == "catalog/harbor/"
    assert pages.href("catalog/harbor/", "CATALOG/") == "../catalog/harbor/"
    assert pages.href("catalog.json", "guides/install/") == "../../catalog.json"


def test_publication_is_fresh_serialized_and_never_records_providers():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs.yml").read_text())
    assert workflow[True]["schedule"] == [{"cron": "17 * * * *"}]
    assert "production" in workflow["concurrency"]["group"]
    assert "github.ref" not in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    site = workflow["jobs"]["site"]
    checkout = next(s for s in site["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event_name == 'pull_request' && github.sha || 'main' }}"
    verify = next(s for s in site["steps"] if s.get("run") == "make docs-publish-check")
    assert verify["env"]["REFRESH_PUBLIC"] == "${{ github.event_name != 'pull_request' }}"
    assert "inputs.record == true" in workflow["jobs"]["record"]["if"]
    assert "pull_request" in workflow["jobs"]["pages"]["if"]


def test_refresh_adds_unknown_public_sources_but_never_private_or_spoofed_default_badges(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "catalog-sources.toml").write_text(
        'composition_repository="fictional-lab/composition"\nrepositories=["fictional-lab/composition"]\n'
    )
    declaration = (
        b'[project]\nname="heinzel-harbor"\nversion="0.1.0"\n'
        b'[project.entry-points."heinzel.modules"]\nharbor="not_imported"\n'
        b'[tool.heinzel.plugin]\nprofiles=["standard"]\n'
    )

    class Sources:
        def get(self, path):
            if path.startswith("search/"):
                return {
                    "total_count": 2,
                    "items": [
                        {"full_name": "new-lab/unknown"},
                        {"full_name": "private-lab/hidden"},
                    ],
                }
            if path == "repos/private-lab/hidden":
                return {"visibility": "private", "private": True}
            if "/commits/" in path:
                return {"sha": "a" * 40}
            if "/git/trees/" in path:
                return {"tree": [{"path": "pyproject.toml"}]}
            return {"visibility": "public", "private": False, "default_branch": "main"}

        def blob(self, repo, entry):
            return declaration

    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    monkeypatch.setattr(catalog, "SNAPSHOT", docs / "absent.json")
    monkeypatch.setattr(
        catalog,
        "recommended_release",
        lambda *_: {
            "version": "0.9.2",
            "artifacts": [{"name": "heinzel-harbor", "version": "0.1.0"}],
        },
    )
    output = docs / "snapshot.json"
    result = catalog.refresh(output, api=Sources())
    assert len(result["plugins"]) == 2
    assert len({p["id"] for p in result["plugins"]}) == 2
    own, external = sorted(result["plugins"], key=lambda p: p["source"]["repository"])
    assert own["included_version"] == "0.1.0"
    assert external["included_version"] == ""
    assert external["maintained"] is False
    assert "private-lab" not in output.read_text()
    assert "not_imported" not in sys.modules
    assert result["notices"]


def test_complete_app_is_found_even_after_hundreds_of_independent_plugin_releases(monkeypatch):
    profile, package = release_pair()

    class IndependentReleases:
        def listing(self, path):
            # Discovery is scoped to app tags; plugin release volume can never hide the app.
            assert path.endswith("git/matching-refs/tags/app-v")
            return [{"ref": f"refs/tags/app-v0.10.{n}"} for n in range(20)]

        def get(self, path):
            if not path.endswith("app-v0.9.2"):
                return {"draft": True}
            # Even a fallback outside the newest100 releases gets its own complete asset list.
            assert path.endswith("releases/tags/app-v0.9.2")
            return {
                "tag_name": "app-v0.9.2",
                "assets": [{"name": a["filename"], "digest": "sha256:" + a["sha256"]} for a in profile["artifacts"]],
            }

    monkeypatch.setattr(
        catalog, "public_json", lambda url: package if urlsplit(url).hostname == "pypi.org" else profile
    )
    result = catalog.recommended_release(IndependentReleases(), {"version": "0.9.2"})
    assert result["version"] == "0.9.2"


def test_capability_and_theme_search_are_not_dependent_on_descriptive_copy():
    project = record()
    project["provides"] = ["tides:read@1"]
    project["requires"] = ["forecast:fetch@1"]
    project["themes"] = [{"name": "twilight-tide", "title": "Evening Harbor", "palette": {}}]
    rendered = pages.cards({"plugins": [project]}, "CATALOG/")
    from html.parser import HTMLParser

    class Cards(HTMLParser):
        search = ""

        def handle_starttag(self, tag, attrs):
            if tag == "article":
                self.search = dict(attrs)["data-search"]

    parser = Cards()
    parser.feed(rendered)
    for query in ("tides:read@1", "forecast:fetch@1", "twilight-tide", "evening harbor"):
        assert query in parser.search


@pytest.mark.parametrize("mismatch", [False, True])
def test_all_published_sources_are_public_and_consistent_with_projects(tmp_path, mismatch):
    source = {**SOURCE, "repository": "another-lab/harbor"} if mismatch else {**SOURCE, "visibility": "private"}
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"format": 1, "sources": [source], "release": {"version": "0.9.2"}, "plugins": [record()]})
    )
    with pytest.raises(ValueError, match="public"):
        pages.load_snapshot(path)


@pytest.mark.parametrize("mutation", ["sdk", "version", "wheel", "unknown", "limit"])
def test_install_cta_rejects_profiles_the_actual_installer_would_refuse(mutation):
    profile, package = release_pair()
    if mutation == "sdk":
        del profile["sdk"]
    elif mutation == "version":
        del profile["artifacts"][1]["version"]
    elif mutation == "wheel":
        artifact = profile["artifacts"][1]
        artifact["filename"] = "heinzel_different-0.1.0-py3-none-any.whl"
        artifact["url"] = artifact["url"].rsplit("/", 1)[0] + "/" + artifact["filename"]
    elif mutation == "unknown":
        profile["extra"] = "unsupported schema"
    else:
        profile["artifacts"] *= 33
    assert not catalog.complete_release(profile, package)


def test_website_ready_profile_is_accepted_by_the_actual_installer(tmp_path):
    from heinzel.heads.install import _selected_profile

    profile, package = release_pair()
    assert catalog.complete_release(profile, package)
    path = tmp_path / "standard.json"
    path.write_text(json.dumps(profile))
    parsed = _selected_profile(str(path))
    assert parsed.core_version == "0.9.2"
    assert len(parsed.artifacts) == 2


def test_oversized_optional_collection_is_omitted_before_any_of_its_blobs_are_read(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "catalog-sources.toml").write_text(
        'composition_repository="fictional-lab/composition"\nrepositories=["fictional-lab/composition"]\n'
    )
    reads = []

    class Sources:
        def get(self, path):
            if path.startswith("search/"):
                return {"total_count": 1, "items": [{"full_name": "another-lab/oversized"}]}
            if "/commits/" in path:
                return {"sha": "a" * 40}
            if "/git/trees/" in path:
                paths = (
                    [f"plugins/plugin-{i}/pyproject.toml" for i in range(600)]
                    if "oversized" in path
                    else ["pyproject.toml"]
                )
                return {"tree": [{"path": p} for p in paths]}
            return {"visibility": "public", "private": False, "default_branch": "main"}

        def blob(self, repo, entry):
            reads.append(repo)
            return b'[project]\nname="heinzel-harbor"\n[project.entry-points."heinzel.modules"]\nharbor="not_imported"'

    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    monkeypatch.setattr(catalog, "SNAPSHOT", docs / "absent.json")
    monkeypatch.setattr(catalog, "recommended_release", lambda *_: {"version": "0.9.2", "artifacts": []})
    result = catalog.refresh(docs / "snapshot.json", api=Sources())
    assert len(result["plugins"]) == 1
    assert reads == ["fictional-lab/composition"]
    assert result["notices"]
    assert all(source["repository"] != "another-lab/oversized" for source in result["sources"])


def test_source_read_and_aggregate_budgets_cannot_be_exceeded():
    class Blobs:
        reads = 0

        def blob(self, repo, entry):
            self.reads += 1
            return b"inert metadata"

    api = Blobs()
    reader = catalog.SourceBlobs(api)
    for _ in range(catalog.MAX_SOURCE_BLOBS):
        reader.blob("fictional-lab/harbor", {})
    with pytest.raises(ValueError, match="budget"):
        reader.blob("fictional-lab/harbor", {})
    assert api.reads == catalog.MAX_SOURCE_BLOBS
    with pytest.raises(ValueError, match="aggregate"):
        catalog.require_capacity([{}] * catalog.MAX_PROJECTS, [{}])


def test_theme_artwork_and_palette_are_both_showcased():
    project = record()
    url = f"https://raw.githubusercontent.com/{SOURCE['repository']}/{SOURCE['revision']}/artwork.webp"
    project["artwork"] = [{"url": url, "alt": "Original Harbor artwork"}]
    project["themes"] = [{"name": "tide", "title": "Harbor Tide", "palette": {}}]
    for rendered in (pages.detail(project), pages.theme_gallery({"plugins": [project]}, "CATALOG/")):
        assert url in rendered
        assert "Original Harbor artwork" in rendered
        assert "-light.svg" in rendered
        assert "-dark.svg" in rendered


@pytest.mark.parametrize("surface", ["ui", "cli"])
@pytest.mark.parametrize("themed", [False, True])
def test_runtime_screenshots_have_labeled_full_size_links_for_every_plugin_kind(surface, themed):
    data = copy.deepcopy(PROJECT)
    data["tool"]["heinzel"]["catalog"]["screenshots"] = [
        {"path": "docs/assets/runtime.png", "surface": surface, "caption": "Harbor <live> & ready"}
    ]
    project = record(data, path="plugins/harbor/pyproject.toml")
    if themed:
        project["themes"] = [{"name": "harbor-tide", "title": "Harbor Tide", "palette": {}}]
    pages.validate_presentation(project)
    rendered = pages.detail(project)
    assert "## Screenshots" in rendered
    assert ("User interface" if surface == "ui" else "Command line") in rendered
    assert 'aria-roledescription="carousel"' in rendered
    assert 'aria-roledescription="slide"' in rendered
    assert "Harbor &lt;live&gt; &amp; ready" in rendered
    assert "/plugins/harbor/docs/assets/runtime.png" in rendered
    assert "Open full-size" in rendered
    if surface == "ui":
        assert project["screenshots"][0]["url"] in pages.cards({"plugins": [project]}, "CATALOG/")


@pytest.mark.parametrize("mutation", ["surface", "path", "svg", "limit"])
def test_invalid_runtime_capture_declarations_are_rejected(mutation):
    data = copy.deepcopy(PROJECT)
    shots = [{"path": "docs/runtime.png", "surface": "ui"}]
    if mutation == "surface":
        shots[0]["surface"] = "script"
    elif mutation == "path":
        shots[0]["path"] = "../other-project/runtime.png"
    elif mutation == "svg":
        shots[0]["path"] = "docs/runtime.svg"
    else:
        shots *= 9
    data["tool"]["heinzel"]["catalog"]["screenshots"] = shots
    with pytest.raises(ValueError, match=r"Screenshots|paths"):
        record(data)


def test_packaged_youtube_soundscapes_are_discovered_without_execution():
    project = record()
    project["soundscape_paths"] = []
    path = "src/heinzel_harbor/soundscapes/tide.toml"

    class Inert:
        def blob(self, repo, entry):
            return b'name="tide"\ntitle="Harbor Tide"\nkind="stream"\n[stream]\nprovider="youtube"\nvideo="abcdefghijk"'

    project["soundscapes"] = catalog.project_soundscapes(Inert(), project, {}, {path: {}})
    pages.validate_presentation(project)
    rendered = pages.detail(project)
    assert "Harbor Tide" in rendered
    assert "https://www.youtube.com/watch?v=abcdefghijk" in rendered
    assert 'data-soundscape="abcdefghijk"' in rendered
    assert "<iframe" not in rendered
    assert "youtube-nocookie.com" not in rendered


@pytest.mark.parametrize("video", ["javascript:alert(1)", "https://evil.invalid/embed", 'abcd" onload="x', "short"])
def test_soundscape_identity_is_validated_on_refresh_and_offline_render(video):
    assert (
        catalog.soundscape_data("sound.toml", {"kind": "stream", "stream": {"provider": "youtube", "video": video}})
        is None
    )
    project = record()
    project["soundscapes"] = [{"title": "Tide", "video": video}]
    with pytest.raises(ValueError, match="YouTube"):
        pages.validate_presentation(project)


def test_offline_screenshot_cannot_claim_another_collection_project():
    project = record(path="plugins/harbor/pyproject.toml")
    project["screenshots"] = [
        {
            "url": f"https://raw.githubusercontent.com/{SOURCE['repository']}/{SOURCE['revision']}/plugins/other/cli.png",
            "surface": "cli",
            "caption": "Not ours",
        }
    ]
    with pytest.raises(ValueError, match="owning project"):
        pages.validate_presentation(project)


@pytest.mark.parametrize("layout", ["default", "custom", "declared"])
def test_repository_discovery_carries_soundscape_toml_into_public_records(layout):
    path = "src/heinzel_harbor/soundscapes/tide.toml"
    configuration = ""
    if layout == "custom":
        path = "src/harbor_content/soundscapes/tide.toml"
        configuration = '\n[tool.hatch.build.targets.wheel]\npackages=["src/harbor_content"]\n'
    elif layout == "declared":
        path = "content/tide.toml"
        configuration = '\n[tool.heinzel.catalog]\nsoundscapes=["content/tide.toml"]\n'
    declaration = (
        '[project]\nname="heinzel-harbor"\n'
        '[project.entry-points."heinzel.modules"]\nharbor="never_imported"\n' + configuration
    ).encode()

    class Public:
        def get(self, endpoint):
            if "/commits/" in endpoint:
                return {"sha": SOURCE["revision"]}
            if "/git/trees/" in endpoint:
                return {"tree": [{"path": "pyproject.toml"}, {"path": path}]}
            return {"visibility": "public", "private": False, "default_branch": "main"}

        def blob(self, repo, entry):
            if entry["path"] == "pyproject.toml":
                return declaration
            return b'name="tide"\ntitle="Harbor Tide"\nkind="stream"\n[stream]\nprovider="youtube"\nvideo="abcdefghijk"'

    _, projects = catalog.repository_records(Public(), SOURCE["repository"])
    assert len(projects) == 1
    assert projects[0]["soundscapes"] == [
        {
            "name": "tide",
            "title": "Harbor Tide",
            "video": "abcdefghijk",
            "pairs_with": [],
            "path": path,
        }
    ]
    assert "never_imported" not in sys.modules


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "guides/install.md",
        "CLI_REFERENCE.md",
        "adr/0001-record.md",
        "ARCHITECTURE.md",
        "some-future-page.md",
    ],
)
def test_every_page_gets_a_small_contextual_character_group(path):
    helpers = pages.page_helpers(path, "reference/")
    assert 1 <= len(helpers) <= 5
    assert all(helper["quote"] and helper["detail"] and helper["link"] for helper in helpers)


def test_future_plugin_pages_get_feature_specific_explainers_without_registration():
    plugin = record()
    plugin["highlights"] = ["A fictional harbor's next useful action"]
    helpers = pages.page_helpers("catalog/new-plugin/index.md", "catalog/new-plugin/", plugin)
    assert len(helpers) == 2
    assert helpers[0]["detail"] == plugin["highlights"][0]
    assert helpers[0]["link"] == "#screenshots"
    assert "before installing" in helpers[1]["detail"]


def test_swipe_gallery_preserves_every_owner_scene_in_order_with_accessible_controls():
    project = record()
    project["screenshots"] = [
        {"url": "https://example.com/overview.png", "surface": "ui", "caption": "Overview"},
        {"url": "https://example.com/detail.png", "surface": "ui", "caption": "Expanded detail"},
        {"url": "https://example.com/command.png", "surface": "cli", "caption": "Actual command"},
    ]
    rendered = pages.screenshot_gallery(project)
    assert rendered.index("overview.png") < rendered.index("detail.png") < rendered.index("command.png")
    assert rendered.count('aria-roledescription="slide"') == 3
    assert 'aria-label="3 of 3"' in rendered
    assert 'aria-label="Previous screenshot"' in rendered
    assert 'aria-label="Next screenshot"' in rendered
    assert "data-gallery-controls hidden" in rendered
    assert 'tabindex="0"' in rendered
    assert "User interface" in rendered
    assert "Command line" in rendered
    assert "Open full-size" in rendered


def test_gallery_empty_state_does_not_offer_nonfunctional_carousel_controls():
    project = record()
    project["screenshots"] = []
    rendered = pages.screenshot_gallery(project)
    assert "No screenshots published yet" in rendered
    assert "data-gallery-track" not in rendered


def test_homepage_product_tour_uses_three_real_owners_with_escaped_captions():
    plugins = []
    for name, scene in (
        ("heinzel-clock", "world-clock"),
        ("heinzel-acme", "company-workflow"),
        ("heinzel-acme-payments-checkout", "draft-in-terminal"),
    ):
        data = copy.deepcopy(PROJECT)
        data["project"]["name"] = name
        project = record(data)
        project["screenshots"] = [
            {"url": f"https://example.com/{scene}.png", "surface": "ui", "caption": 'Demo <team> & "review"'}
        ]
        plugins.append(project)
    rendered = pages.home_gallery({"plugins": plugins}, "")
    assert rendered.count("data-heinzel-helper") == 3
    assert rendered.count("<figure>") == 3
    assert "&lt;team&gt; &amp; &quot;review&quot;" in rendered
    assert all(f"/{name}.png" in rendered for name in ("world-clock", "company-workflow", "draft-in-terminal"))
    assert "fictional demo runner" in rendered
    assert 'referrerpolicy="no-referrer"' in rendered
    assert "aspect-ratio" not in rendered


def test_homepage_does_not_substitute_settings_for_a_missing_feature_scene():
    project = record()
    project["name"] = "heinzel-clock"
    project["screenshots"] = [{"url": "https://example.com/settings.png", "surface": "ui", "caption": "Settings"}]
    assert "settings.png" not in pages.home_gallery({"plugins": [project]}, "")


def test_homepage_product_tour_survives_markdown_section_rendering():
    from html.parser import HTMLParser

    import markdown

    plugins = []
    for name, scene in (
        ("heinzel-clock", "world-clock"),
        ("heinzel-acme", "company-workflow"),
        ("heinzel-acme-payments-checkout", "draft-in-terminal"),
    ):
        project = record()
        project["name"] = name
        project["screenshots"] = [
            {"url": f"https://example.com/{scene}.png", "surface": "ui", "caption": "Fictional demonstration"}
        ]
        plugins.append(project)
    content = pages.home_gallery({"plugins": plugins}, "")
    rendered = markdown.markdown(f'<section markdown="1">\n\n{content}\n\n</section>', extensions=["md_in_html"])

    class Stories(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.articles = []
            self.figures = []

        def handle_starttag(self, tag, attrs):
            classes = dict(attrs).get("class", "")
            if tag == "article" and classes == "hz-product-story":
                self.articles.append(self.stack[-1])
            if tag == "figure":
                self.figures.append(self.stack[-1])
            if tag not in {"img", "br", "hr", "input", "source"}:
                self.stack.append((tag, classes))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    parser = Stories()
    parser.feed(rendered)
    assert parser.articles == [("div", "hz-product-stories")] * 3
    assert parser.figures == [("article", "hz-product-story")] * 3
