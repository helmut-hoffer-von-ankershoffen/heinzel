"""The bookmark tree (ADR 0140): operations, moves, undo, the favicon cache and what agents may call."""

import base64
import json
import os
import stat

import pytest
from rich.console import Console

from heinzel import HeinzelError
from heinzel.kernel import OperationLet, exposed, restricts
from heinzel.modules.system import bookmark
from heinzel.modules.system.bookmark import BookmarkService, render
from heinzel.modules.system.bookmark._service import (
    MAX_DEPTH,
    MAX_FAVICON_BYTES,
    MAX_REMOVED,
    bookmark_url,
    favicon_image,
    origin_of,
)

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def data_url(data: bytes = PNG, media: str = "image/png") -> str:
    return f"data:{media};base64,{base64.b64encode(data).decode()}"


@pytest.fixture
def service(make_kernel, settings, tmp_path) -> BookmarkService:
    owned = settings.model_copy(update={"data_dir": tmp_path / "data", "cache_dir": tmp_path / "cache"})
    return BookmarkService(make_kernel(settings_=owned))


def titles(service: BookmarkService) -> list[tuple[int, str]]:
    return [(item.depth, item.title) for item in service.listing().bookmarks]


def test_an_empty_tree_lists_nothing_and_says_how_to_start(service):
    assert service.listing().bookmarks == []
    console = Console(width=120, force_terminal=False)
    with console.capture() as capture:
        console.print(render(service.listing()))
    assert "hz system bookmark add" in capture.get()


def test_add_keeps_order_titles_and_survives_a_new_service(service, make_kernel, settings, tmp_path):
    first = service.add("https://example.com/a", "A", None, None)
    second = service.add("https://example.org/", None, None, first.changed)
    assert titles(service) == [(0, "example.org"), (0, "A")]
    assert second.bookmarks[0].origin == "https://example.org"
    owned = settings.model_copy(update={"data_dir": tmp_path / "data", "cache_dir": tmp_path / "cache"})
    assert titles(BookmarkService(make_kernel(settings_=owned))) == [(0, "example.org"), (0, "A")]
    assert json.loads(service.file.read_text())["version"] == 1


def test_the_same_address_in_a_folder_is_kept_once(service):
    first = service.add("https://example.com/", "One", None, None)
    again = service.add("https://example.com/", "Two", None, None)
    assert again.changed == first.changed
    assert titles(service) == [(0, "One")]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pw@example.com",
        "https://",
        "https://exa mple.com",
        "https://example.com/" + "a" * 2100,
        "https://[bad",
    ],
)
def test_only_plain_https_addresses_are_bookmarked(service, url):
    with pytest.raises(HeinzelError, match=r"https://|characters"):
        service.add(url, None, None, None)
    assert service.listing().bookmarks == []


def test_origins_keep_ports_and_brackets():
    assert origin_of(bookmark_url(" https://Example.com:8443/x ")) == "https://example.com:8443"
    assert origin_of("https://example.com:443/") == "https://example.com"
    assert origin_of("https://[2001:db8::1]/") == "https://[2001:db8::1]"


def test_folders_nest_and_list_depth_first(service):
    work = service.folder("Work", None, None).changed
    inner = service.folder("  ", work, None).changed
    service.add("https://example.com/deep", "Deep", inner, None)
    service.add("https://example.com/top", "Top", None, None)
    assert titles(service) == [(0, "Work"), (1, "Folder"), (2, "Deep"), (0, "Top")]
    listed = service.listing().bookmarks
    assert listed[2].parent == inner
    assert listed[1].parent == work


def test_rename_and_change_address(service):
    entry = service.add("https://example.com/", "Old", None, None).changed
    folder = service.folder("F", None, None).changed
    assert service.edit(entry, "New  name", "https://example.net/").bookmarks[0].url == "https://example.net/"
    assert titles(service)[0] == (0, "New name")
    assert service.edit(entry, " ", None).bookmarks[0].title == "New name"
    with pytest.raises(HeinzelError, match="no address"):
        service.edit(folder, None, "https://example.com/")
    with pytest.raises(HeinzelError, match="https://"):
        service.edit(entry, None, "http://example.com/")
    with pytest.raises(HeinzelError, match="No bookmark or folder"):
        service.edit("b-00000000", "x", None)


def test_move_reorders_and_changes_folder(service):
    a = service.add("https://example.com/a", "A", None, None).changed
    b = service.add("https://example.com/b", "B", None, None).changed
    folder = service.folder("F", None, None).changed
    service.move(b, None, a)
    assert titles(service) == [(0, "B"), (0, "A"), (0, "F")]
    service.move(a, folder, None)
    assert titles(service) == [(0, "B"), (0, "F"), (1, "A")]
    service.move(a, None, None)
    assert titles(service) == [(0, "B"), (0, "F"), (0, "A")]
    assert service.move(a, None, a).changed == a


def test_refused_moves_change_nothing(service):
    a = service.add("https://example.com/a", "A", None, None).changed
    outer = service.folder("Outer", None, None).changed
    inner = service.folder("Inner", outer, None).changed
    before = titles(service)
    with pytest.raises(HeinzelError, match="into itself"):
        service.move(outer, inner, None)
    with pytest.raises(HeinzelError, match="into itself"):
        service.move(outer, outer, None)
    with pytest.raises(HeinzelError, match="not a folder"):
        service.move(outer, a, None)
    with pytest.raises(HeinzelError, match="isn't in that folder"):
        service.move(a, None, inner)
    with pytest.raises(HeinzelError, match="isn't in that folder"):
        service.add("https://example.com/x", None, None, inner)
    assert titles(service) == before


def test_nesting_is_bounded(service):
    parent = None
    for _ in range(MAX_DEPTH - 1):
        parent = service.folder("F", parent, None).changed
    with pytest.raises(HeinzelError, match="nest at most"):
        service.folder("Too deep", parent, None)
    service.add("https://example.com/", "Leaf", parent, None)
    # One rule for every way in: an empty folder moved there is refused like a new one.
    empty = service.folder("Empty", None, None).changed
    with pytest.raises(HeinzelError, match="nest at most"):
        service.move(empty, parent, None)
    service.remove(empty)
    service.remove(parent)
    assert service.restore(empty).changed == empty
    service.restore(parent)
    tall = service.folder("Tall", None, None).changed
    service.folder("Child", tall, None)
    with pytest.raises(HeinzelError, match="nest at most"):
        service.move(tall, parent, None)


def test_remove_then_restore_puts_it_back_where_it_was(service):
    folder = service.folder("F", None, None).changed
    service.add("https://example.com/1", "One", folder, None)
    two = service.add("https://example.com/2", "Two", folder, None).changed
    service.add("https://example.com/3", "Three", folder, None)
    removed = service.remove(two)
    assert removed.restorable == [two]
    assert titles(service) == [(0, "F"), (1, "One"), (1, "Three")]
    assert service.restore(None).changed == two
    assert titles(service) == [(0, "F"), (1, "One"), (1, "Two"), (1, "Three")]
    with pytest.raises(HeinzelError, match="Nothing to restore"):
        service.restore(None)


def test_a_removed_folder_comes_back_whole_and_an_orphan_lands_at_the_top(service):
    folder = service.folder("F", None, None).changed
    child = service.add("https://example.com/1", "One", folder, None).changed
    service.remove(child)
    service.remove(folder)
    assert titles(service) == []
    assert service.restore(child).changed == child  # its folder is gone
    assert titles(service) == [(0, "One")]
    service.restore(folder)
    assert titles(service) == [(0, "F"), (0, "One")]


def test_only_the_latest_removals_stay_restorable(service):
    ids = [service.add(f"https://example.com/{n}", str(n), None, None).changed for n in range(MAX_REMOVED + 2)]
    for entry in ids:
        service.remove(entry)
    restorable = service.listing().restorable
    assert len(restorable) == MAX_REMOVED
    assert restorable[0] == ids[-1]
    assert ids[0] not in restorable


def test_a_damaged_file_is_named_not_overwritten(service):
    service.file.parent.mkdir(parents=True)
    service.file.write_text("{not json")
    with pytest.raises(HeinzelError, match="can't be read"):
        service.add("https://example.com/", None, None, None)
    service.file.write_text('{"version": 2}')
    with pytest.raises(HeinzelError, match="unknown format"):
        service.listing()
    assert service.file.read_text() == '{"version": 2}'


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 1, "children": [{"id": "b-1", "title": "No kind"}]},
        {"version": 1, "children": [{"kind": "bookmark", "title": "No id", "url": "https://example.com/"}]},
        {"version": 1, "children": [{"id": "b-1", "kind": "bookmark", "title": "No address"}]},
        {"version": 1, "children": [{"id": "b-1", "kind": "folder", "title": "No children"}]},
        {"version": 1, "children": [{"id": "b-1", "kind": "folder", "title": "F", "children": ["text"]}]},
        {"version": 1, "children": [{"id": "b-1", "kind": "note", "title": "Other kind"}]},
        {"version": 1, "children": ["text"]},
        {"version": 1, "children": [], "removed": "text"},
        {"version": 1, "children": [], "removed": [{"node": {"id": "b-1"}, "parent": None, "index": 0}]},
        {"version": 1, "children": [], "removed": [{"parent": None, "index": 0}]},
        ["a list"],
    ],
)
def test_a_damaged_entry_gives_the_format_error_in_every_operation(service, stored):
    service.file.parent.mkdir(parents=True)
    service.file.write_text(json.dumps(stored))
    for call in (
        service.listing,
        lambda: service.add("https://example.com/", None, None, None),
        lambda: service.move("b-1", None, None),
        lambda: service.remove("b-1"),
        lambda: service.restore(None),
    ):
        with pytest.raises(HeinzelError, match="unknown format"):
            call()
    assert json.loads(service.file.read_text()) == stored


def test_the_tree_and_the_favicon_cache_are_the_owners_alone(service, tmp_path):
    def mode(path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    old = os.umask(0o022)
    try:
        service.add("https://example.com/", None, None, None)
        service.set_favicon("https://example.com", data_url())
    finally:
        os.umask(old)
    tree_dir, cache_dir = service.file.parent, tmp_path / "cache/bookmark"
    assert mode(tree_dir) == 0o700
    assert {path.name: mode(path) for path in tree_dir.iterdir()} == {"bookmarks.json": 0o600, "bookmarks.lock": 0o600}
    assert mode(cache_dir) == 0o700
    assert mode(cache_dir / "favicons") == 0o700
    assert [mode(path) for path in (cache_dir / "favicons").iterdir()] == [0o600]


def test_stores_that_were_open_to_others_are_narrowed(service, tmp_path):
    service.file.parent.mkdir(parents=True, mode=0o755)
    service.file.parent.chmod(0o755)
    (tmp_path / "cache/bookmark/favicons").mkdir(parents=True, mode=0o755)
    service.file.write_text('{"version": 1, "children": []}')
    service.file.chmod(0o644)
    service.add("https://example.com/", None, None, None)
    service.set_favicon("https://example.com", data_url())
    assert stat.S_IMODE(service.file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(service.file.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "cache/bookmark/favicons").stat().st_mode) == 0o700


def test_a_favicon_is_cached_per_origin_of_a_bookmark(service):
    service.add("https://example.com/a", "A", None, None)
    service.add("https://example.com/b", "B", None, None)
    other = service.add("https://example.org/", "O", None, None).changed
    listed = service.set_favicon("https://example.com", data_url())
    assert listed.favicons == {"https://example.com": data_url()}
    gif = b"GIF89a" + b"\x01" * 10
    assert service.set_favicon("https://example.com", data_url(gif, "image/gif")).favicons == {
        "https://example.com": data_url(gif, "image/gif")
    }
    service.set_favicon("https://example.org", data_url(b"\x00\x00\x01\x00rest", "image/vnd.microsoft.icon"))
    assert set(service.listing().favicons) == {"https://example.com", "https://example.org"}
    with pytest.raises(HeinzelError, match="only for the origin of a bookmark"):
        service.set_favicon("https://unknown.example", data_url())
    # An origin without a bookmark loses its icon at the next cache write.
    service.remove(other)
    assert set(service.set_favicon("https://example.com", data_url()).favicons) == {"https://example.com"}
    assert len(list((service.file.parents[2] / "cache/bookmark/favicons").iterdir())) == 1


@pytest.mark.parametrize(
    "image",
    [
        "https://icons.example/favicon.png",
        "data:image/svg+xml;base64,PHN2Zy8+",
        "data:image/png,plain",
        "data:image/png;base64,@@@",
        data_url(b"not a png"),
        data_url(b""),
        data_url(PNG + b"\x00" * MAX_FAVICON_BYTES),
    ],
)
def test_a_favicon_must_be_a_small_known_image(service, image):
    service.add("https://example.com/", None, None, None)
    with pytest.raises(HeinzelError, match="A favicon is a PNG"):
        service.set_favicon("https://example.com", image)
    assert service.listing().favicons == {}
    assert favicon_image(data_url())[0] == "image/png"


def test_a_tampered_cache_file_is_not_served(service, tmp_path):
    service.add("https://example.com/", None, None, None)
    service.set_favicon("https://example.com", data_url())
    icon = next((tmp_path / "cache/bookmark/favicons").iterdir())
    icon.write_bytes(b"<script>")
    assert service.listing().favicons == {}


def test_agents_read_and_add_but_never_change_or_remove():
    heads = {let.name: let.heads for let in bookmark.module.lets if isinstance(let, OperationLet)}
    assert set(heads) == {"list", "add", "folder", "edit", "move", "remove", "restore", "favicon"}
    for name in ("list", "add", "folder"):
        assert not restricts(heads[name], "mcp")
        assert restricts(heads[name], "mcp-http")  # no authentication there
        assert heads[name] & {"mcp", "mcp-stdio"}  # what a chat's agent may propose
    for name in ("edit", "move", "remove", "restore", "favicon"):
        assert restricts(heads[name], "mcp")
        assert restricts(heads[name], "mcp-http")
        assert not heads[name] & {"mcp", "mcp-stdio", "mcp-check-in"}
    assert all(restricts(found, "api") for found in heads.values())


def test_each_head_offers_exactly_its_bookmark_operations(make_kernel):
    kernel = make_kernel(builtins=True)

    def offered(head: str) -> set[str]:
        return {item.path[-1] for item in exposed(kernel, head) if item.path[:2] == ("system", "bookmark")}

    assert offered("mcp") == {"list", "add", "folder"}
    assert offered("mcp-http") == set()
    assert offered("api") == set()
    assert offered("cli") == {"list", "add", "folder", "edit", "move", "remove", "restore"}
    assert offered("web") == {"list", "add", "folder", "edit", "move", "remove", "restore", "favicon"}


def test_the_tree_renders_indented(service):
    folder = service.folder("Work", None, None).changed
    service.add("https://example.com/", "Site", folder, None)
    console = Console(width=120, force_terminal=False)
    with console.capture() as capture:
        console.print(render(service.listing()))
    assert "▸ Work" in capture.get()
    assert "  Site" in capture.get()
