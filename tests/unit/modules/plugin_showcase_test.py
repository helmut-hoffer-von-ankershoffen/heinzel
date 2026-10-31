"""Plugin previews read only bounded confirmed-public metadata and never execute providers."""

import base64
from dataclasses import replace

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.plugin._showcase import MAX_ASSET, MAX_IMAGES, MAX_TREE, public_showcase
from heinzel.modules.system.plugin._sources import parse

pytestmark = pytest.mark.unit
SHA = "a" * 40
PROJECT_SHA = "b" * 40
SOUND_SHA = "c" * 40
ROOT = "plugins/heinzel-harbor/"
SPEC = f"heinzel-harbor @ git+https://github.com/fictional-lab/plugins@{SHA}#subdirectory={ROOT.rstrip('/')}"
PROJECT = b"""[project]
name = "heinzel-harbor"
[tool.heinzel.catalog]
title = "Harbor"
[[tool.heinzel.catalog.screenshots]]
path = "docs/ui.png"
surface = "ui"
caption = "The harbor interface"
[[tool.heinzel.catalog.screenshots]]
path = "docs/cli.webp"
surface = "cli"
caption = "The harbor command"
"""
SOUND = b"""name = "tide"
title = "Harbor tide"
kind = "stream"
[stream]
provider = "youtube"
video = "abcdefghijk"
"""


class PublicGitHub:
    def __init__(self):
        self.calls = []
        self.metadata = {"private": False, "visibility": "public", "default_branch": "main"}
        self.project = PROJECT
        self.sound = SOUND
        self.truncated = False
        self.entries = []

    def get(self, path):
        self.calls.append(path)
        base = "repos/fictional-lab/plugins"
        if path == base:
            return self.metadata
        if path.startswith(base + "/commits/"):
            return {"sha": SHA}
        if path == f"{base}/git/trees/{SHA}?recursive=1":
            return {"tree": self.entries, "truncated": self.truncated}
        if path == f"{base}/git/blobs/{PROJECT_SHA}":
            return {"content": base64.b64encode(self.project).decode()}
        if path == f"{base}/git/blobs/{SOUND_SHA}":
            return {"content": base64.b64encode(self.sound).decode()}
        raise AssertionError(path)


@pytest.fixture
def github():
    api = PublicGitHub()
    api.entries = [
        {"path": ROOT + "pyproject.toml", "type": "blob", "mode": "100644", "size": len(PROJECT), "sha": PROJECT_SHA},
        *[
            {"path": ROOT + path, "type": "blob", "mode": "100644", "size": 500, "sha": "d" * 40}
            for path in ("docs/ui.png", "docs/cli.webp")
        ],
        {
            "path": ROOT + "src/heinzel_harbor/soundscapes/tide.toml",
            "type": "blob",
            "mode": "100644",
            "size": len(SOUND),
            "sha": SOUND_SHA,
        },
    ]
    return api


def test_preview_uses_immutable_owner_paths_without_loading_or_installing_the_plugin(github):
    gallery = public_showcase(parse(SPEC), github.get)
    assert gallery.title == "Harbor"
    assert gallery.revision == SHA
    assert [shot.surface for shot in gallery.screenshots] == ["ui", "cli"]
    assert (
        gallery.screenshots[0].url == f"https://raw.githubusercontent.com/fictional-lab/plugins/{SHA}/{ROOT}docs/ui.png"
    )
    assert gallery.soundscapes[0].url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert gallery.soundscapes[0].title == "Harbor tide"
    assert all(path.startswith("repos/fictional-lab/plugins") for path in github.calls)
    assert all("raw.githubusercontent.com" not in path and "youtube" not in path for path in github.calls)


@pytest.mark.parametrize("visibility", [{"private": True, "visibility": "private"}, {"private": False}])
def test_private_or_unverified_repository_stops_before_reading_any_files_or_public_image_urls(github, visibility):
    github.metadata = visibility
    gallery = public_showcase(parse(SPEC), github.get)
    assert gallery.screenshots == gallery.soundscapes == []
    assert gallery.revision is None
    assert github.calls == ["repos/fictional-lab/plugins"]
    assert "raw.githubusercontent.com" not in gallery.model_dump_json()


def test_unsupported_sources_return_an_actionable_notice_without_network():
    def no_network(path):
        pytest.fail(path)

    gallery = public_showcase(parse("heinzel-harbor"), no_network)
    assert not gallery.screenshots
    assert "repository" in gallery.notice


@pytest.mark.parametrize(
    "path", ["../other/ui.png", "/outside/ui.png", "a/../ui.png", "https://example.org/a.png", "a//ui.png"]
)
def test_declared_screenshot_cannot_escape_the_owning_project(github, path):
    github.project = PROJECT.replace(b"docs/ui.png", path.encode())
    with pytest.raises(HeinzelError, match="owning project"):
        public_showcase(parse(SPEC), github.get)


@pytest.mark.parametrize("mutation", ["linked", "directory", "oversized", "svg", "surface", "caption"])
def test_invalid_images_and_unsafe_labels_fail_closed(github, mutation):
    image = github.entries[1]
    if mutation == "linked":
        image["mode"] = "120000"
    elif mutation == "directory":
        image["type"] = "tree"
    elif mutation == "oversized":
        image["size"] = MAX_ASSET + 1
    elif mutation == "svg":
        github.project = PROJECT.replace(b"docs/ui.png", b"docs/ui.svg")
        image["path"] = ROOT + "docs/ui.svg"
    elif mutation == "surface":
        github.project = PROJECT.replace(b'surface = "ui"', b'surface = "script"')
    else:
        github.project = PROJECT.replace(b"The harbor interface", b"x" * 181)
    with pytest.raises(HeinzelError, match=r"raster|captions"):
        public_showcase(parse(SPEC), github.get)


def test_wrong_distribution_identity_is_rejected(github):
    source = replace(parse(SPEC), name="heinzel-another")
    with pytest.raises(HeinzelError, match="match the selected plugin"):
        public_showcase(source, github.get)


@pytest.mark.parametrize("mutation", ["truncated", "limit", "symlink", "bad_sha"])
def test_unbounded_or_linked_metadata_never_reaches_the_blob_reader(github, mutation):
    if mutation == "truncated":
        github.truncated = True
    elif mutation == "limit":
        github.entries *= MAX_TREE + 1
    elif mutation == "symlink":
        github.entries[0]["mode"] = "120000"
    else:
        github.entries[0]["sha"] = "not-a-revision"
    with pytest.raises(HeinzelError, match=r"large|regular"):
        public_showcase(parse(SPEC), github.get)
    assert not any("/git/blobs/" in path for path in github.calls)


def test_invalid_video_identity_does_not_produce_an_external_link(github):
    github.sound = SOUND.replace(b"abcdefghijk", b"javascript:alert(1)")
    assert not public_showcase(parse(SPEC), github.get).soundscapes


def test_too_many_images_are_rejected(github):
    github.project += b'\n[[tool.heinzel.catalog.screenshots]]\npath="docs/ui.png"\nsurface="ui"\n' * MAX_IMAGES
    with pytest.raises(HeinzelError, match="eight screenshots"):
        public_showcase(parse(SPEC), github.get)


@pytest.mark.parametrize("spec", ["harbor", "heinzel-harbor", " harbor "])
def test_service_resolves_short_catalog_names_without_installing_the_provider(github, monkeypatch, spec):
    from types import SimpleNamespace

    from heinzel.modules.system.plugin._service import PluginService

    service = object.__new__(PluginService)
    service.github_api = github.get
    monkeypatch.setattr(
        service,
        "search",
        lambda text: SimpleNamespace(plugins=[SimpleNamespace(name="heinzel-harbor", install_spec=SPEC)]),
    )
    assert service.showcase(spec).screenshots[0].caption == "The harbor interface"


def test_service_reports_malformed_metadata_as_an_actionable_preview_error(github):
    from heinzel.modules.system.plugin._service import PluginService

    service = object.__new__(PluginService)
    service.github_api = github.get
    github.project = b"invalid TOML ["
    with pytest.raises(HeinzelError, match="could not be read"):
        service.showcase(SPEC)
