"""Heinzel Notifier (ADR 0043): copied from the bundle, verified, replaced when it differs, launched through `open`.

A fake app bundle and a fake runner: no real app is installed and nothing is launched.
"""

import hashlib
import importlib.util
import json
import struct
import subprocess
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from heinzel.modules.system.notify._macos import (
    EXECUTABLE,
    LIFETIME,
    SOURCES,
    NotifierApp,
    bundle_version,
    parse_etime,
    source_version,
    tree_digest,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def fake_app(path: Path, version: str = "abc", binary: bytes = b"\xcf\xfa\xed\xfe") -> Path:
    (path / "Contents" / "MacOS").mkdir(parents=True)
    (path / "Contents" / "MacOS" / "notifier").write_bytes(binary)
    (path / "Contents" / "Info.plist").write_text(
        f'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleVersion</key><string>{version}</string>'
        "</dict></plist>",
        encoding="utf-8",
    )
    return path


class Runner:
    """Answers `open` by writing `said` to the file given with --stdout; records every command."""

    def __init__(self, said: str = "posted", code: int = 0) -> None:
        self.said, self.code = said, code
        self.commands: list[list[str]] = []

    def __call__(self, command, timeout=None):
        command = list(command)
        self.commands.append(command)
        if "--stdout" in command:
            Path(command[command.index("--stdout") + 1]).write_text(f"{self.said}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, self.code, "", "LaunchServices said no")


@pytest.fixture
def bundled(tmp_path) -> Path:
    return fake_app(tmp_path / "wheel" / "HeinzelNotifier.app")


def test_it_installs_a_verified_copy_and_keeps_it_while_it_matches(tmp_path, bundled):
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner())
    assert app.inspect().state == "missing"
    assert app.ensure().state == "installed"
    assert app.path == tmp_path / "home" / "Applications" / "Heinzel Notifier.app"
    assert tree_digest(app.path) == tree_digest(bundled)
    assert (app.path / EXECUTABLE).stat().st_mode & 0o111
    assert app.ensure().state == "current"
    assert app.inspect().state == "current"


def test_a_copy_that_differs_is_replaced(tmp_path, bundled):
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner())
    fake_app(app.path, version="old", binary=b"old")
    assert app.inspect().state == "outdated"
    assert app.ensure().state == "installed"
    assert bundle_version(app.path) == "abc"
    assert not list(app.path.parent.glob(".heinzel-notifier-*"))  # the staging directory is gone


def test_without_a_bundle_an_installed_app_is_still_used(tmp_path):
    app = NotifierApp(tmp_path / "home", bundled=tmp_path / "none.app", runner=Runner())
    assert not app.available
    assert app.ensure().state == "missing"
    fake_app(app.path)
    assert app.available
    assert app.ensure().state == "outdated"


def test_it_posts_through_launchservices_with_arguments_only(tmp_path, bundled):
    runner = Runner()
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=runner)
    assert app.post('" & do shell script "x"', "Read it.", "http://127.0.0.1:8737/doctor") == "posted"
    command = runner.commands[0]
    assert command[:4] == ["/usr/bin/open", "-n", "-g", "-W"]
    assert command[command.index("--args") - 1] == str(app.path)
    assert command[command.index("--args") + 1 :] == [
        "--title",
        '" & do shell script "x"',
        "--message",
        "Read it.",
        "--open",
        "http://127.0.0.1:8737/doctor",
    ]


def test_what_the_app_said_comes_back(tmp_path, bundled):
    def said(runner: Runner) -> str:
        return NotifierApp(tmp_path / "home", bundled=bundled, runner=runner).status()

    assert said(Runner("authorized")) == "authorized"
    assert said(Runner("denied")) == "denied"
    assert said(Runner(code=124)).startswith("waiting: macOS is asking")
    assert said(Runner(code=1)) == "open failed: LaunchServices said no"
    assert said(Runner("")) == "no answer from Heinzel Notifier"


def test_quarantine_is_read_with_xattr(tmp_path, bundled):
    runner = Runner(code=0)
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=runner)
    assert app.quarantined()
    assert runner.commands[0][:3] == ["/usr/bin/xattr", "-p", "com.apple.quarantine"]
    assert not NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner(code=1)).quarantined()


def build_script():
    spec = importlib.util.spec_from_file_location("notifier_build", ROOT / "scripts" / "notifier.py")
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_the_build_script_and_the_runtime_agree_on_the_version():
    script = build_script()
    assert script._version() == source_version(SOURCES)
    assert len(source_version()) == 16
    assert (
        source_version()
        == hashlib.sha256(
            b"".join(
                n.encode() + b"\0" + (SOURCES / n).read_bytes() + b"\0"
                for n in ("Info.plist", "heinzel-swarm.svg", "heinzel.svg", "icon.swift", "main.swift")
            )
        ).hexdigest()[:16]
    )


def test_the_swift_source_opens_only_the_web_app_on_this_machine():
    source = (SOURCES / "main.swift").read_text(encoding="utf-8")
    assert 'url.scheme == "http", url.host == "127.0.0.1", url.port != nil' in source
    assert "Process(" not in source  # it never runs anything
    assert "NSAppleScript" not in source


def test_a_failed_swap_puts_the_old_app_back(tmp_path, bundled, monkeypatch):
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner())
    fake_app(app.path, version="old", binary=b"old")
    original = Path.rename

    def refuse_the_new_one(self: Path, target: Path) -> Path:
        if self.name == "Heinzel Notifier.app" and target == app.path:
            msg = "disk full"
            raise OSError(28, msg)
        return original(self, target)

    monkeypatch.setattr(Path, "rename", refuse_the_new_one)
    assert app.ensure().state == "outdated"
    assert bundle_version(app.path) == "old"


def test_a_symlink_changes_the_digest(tmp_path, bundled):
    before = tree_digest(bundled)
    (bundled / "Contents" / "Resources").mkdir()
    (bundled / "Contents" / "Resources" / "link").symlink_to("/etc/passwd")
    assert tree_digest(bundled) != before


def test_the_app_never_waits_forever_for_the_persons_answer():
    assert 'asyncAfter(deadline: .now() + 60) { finish("waiting", 3) }' in (SOURCES / "main.swift").read_text("utf-8")


def png_size(png: bytes) -> tuple[int, int]:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def test_one_drawing_an_original_one_shows_heinzelmaennchen_everywhere():
    script = build_script()
    assert (script.ARTWORK, script.SWARM) == (SOURCES / "heinzel.svg", SOURCES / "heinzel-swarm.svg")
    # Shapes only: no embedded image or font glyph, so nothing of an emoji font's artwork can ship in them.
    for artwork in (script.ARTWORK, script.SWARM):
        for embedded in (b"<image", b"<text", b"<use", b"<font", b"<style", b"data:", b"href"):
            assert embedded not in artwork.read_bytes()
    for copy, source in script.COPIES.items():
        assert copy.read_bytes() == source.read_bytes(), f"{copy.relative_to(ROOT)} is stale: run make icons"
    # An app icon is one Heinzelmännchen at every size; the swarm is the logo, not an icon.
    assert script.artwork_for(32) == script.ARTWORK
    assert script.artwork_for(512) == script.ARTWORK
    for name, (background, fill) in script.WORDMARKS.items():
        made = (script.THEME_LOGO / name).read_text(encoding="utf-8")
        assert made == script.wordmark(background, fill), f"{name} is stale: run make icons"
    # The drawing it replaced, a red pointed hat, is gone from everything that shows Heinzelmännchen.
    for shown in (*script.COPIES, *(script.THEME_LOGO / name for name in script.WORDMARKS)):
        assert b"M120 292 C160 200" not in shown.read_bytes()


def test_the_web_icons_are_the_sizes_the_manifest_and_the_build_name():
    script = build_script()
    for name, (size, style) in script.WEB_ICONS.items():
        assert style in {"tile", "maskable"}
        assert png_size((script.PUBLIC / name).read_bytes()) == (size, size)
    manifest = json.loads((script.PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    for icon in manifest["icons"]:
        size, style = script.WEB_ICONS[urlsplit(icon["src"]).path.lstrip("/")]
        assert icon["sizes"] == f"{size}x{size}"
        assert (icon["purpose"] == "maskable") == (style == "maskable")
    ico = (script.PUBLIC / "favicon.ico").read_bytes()
    _, kind, count = struct.unpack("<HHH", ico[:6])
    assert (kind, count) == (1, len(script.FAVICON_SIZES))
    for index, size in enumerate(script.FAVICON_SIZES):
        _, _, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", ico[6 + 16 * index : 22 + 16 * index])
        assert png_size(ico[offset : offset + length]) == (size, size)


def test_a_source_checkout_rebuilds_the_bundled_app_only_when_its_sources_changed(tmp_path, bundled):
    script = tmp_path / "notifier.py"
    script.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    current = [False]

    def runner(command, timeout=None):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0 if current[0] or command[-1] != "--check" else 1, "", "")

    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=runner, build_script=script)
    assert app.rebuild() is None
    assert [c[-1] for c in commands] == ["--check", str(script)]
    current[0] = True
    assert app.rebuild() is None  # up to date: no second build
    assert NotifierApp(tmp_path / "h", bundled=bundled, runner=runner, build_script=None).rebuild() is None


def test_a_failed_rebuild_says_why(tmp_path, bundled):
    script = tmp_path / "notifier.py"
    script.write_text("", encoding="utf-8")

    def runner(command, timeout=None):
        return subprocess.CompletedProcess(command, 1, "", "swiftc not found\n")

    assert NotifierApp(tmp_path / "home", bundled=bundled, runner=runner, build_script=script).rebuild() == (
        "swiftc not found"
    )


def png_pixels(data: bytes) -> tuple[int, int, list[tuple[int, ...]]]:
    """Decode tracked 8-bit RGB(A) PNG rows so Linux checks actual icon opacity, not the Swift source's words."""
    width, height = png_size(data)
    channels = {2: 3, 6: 4}[data[25]]
    assert data[24] == 8
    assert data[28] == 0
    compressed = bytearray()
    offset = 8
    while offset < len(data):
        size = int.from_bytes(data[offset : offset + 4])
        if data[offset + 4 : offset + 8] == b"IDAT":
            compressed.extend(data[offset + 8 : offset + 8 + size])
        offset += size + 12
    rows = zlib.decompress(compressed)
    stride = width * channels
    previous = bytearray(stride)
    pixels = []
    for y in range(height):
        at = y * (stride + 1)
        kind = rows[at]
        current = bytearray(rows[at + 1 : at + 1 + stride])
        for x in range(stride):
            left = current[x - channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            if kind == 1:
                predictor = left
            elif kind == 2:
                predictor = up
            elif kind == 3:
                predictor = (left + up) // 2
            elif kind == 4:
                pivot = left + up - corner
                distances = [abs(pivot - value) for value in (left, up, corner)]
                predictor = (left, up, corner)[distances.index(min(distances))]
            else:
                assert kind == 0
                predictor = 0
            current[x] = (current[x] + predictor) % 256
        pixels.extend(tuple(current[x : x + channels]) for x in range(0, stride, channels))
        previous = current
    return width, height, pixels


def test_every_png_and_ico_frame_has_opaque_white_corners_and_a_real_portrait():
    script = build_script()
    images = [(name, (script.PUBLIC / name).read_bytes()) for name in script.WEB_ICONS]
    ico = (script.PUBLIC / "favicon.ico").read_bytes()
    for index, size in enumerate(script.FAVICON_SIZES):
        _, _, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", ico[6 + 16 * index : 22 + 16 * index])
        images.append((f"favicon-{size}", ico[offset : offset + length]))
    for name, data in images:
        width, height, pixels = png_pixels(data)
        assert all(len(pixel) == 3 or pixel[3] == 255 for pixel in pixels), f"{name} has transparent pixels"
        for index in (0, width - 1, (height - 1) * width, width * height - 1):
            assert pixels[index][:3] == (255, 255, 255), f"{name} has a nonwhite corner"
        assert sum(pixel[:3] != (255, 255, 255) for pixel in pixels) > width, f"{name} lost its portrait"
    svg = (script.PUBLIC / "favicon.svg").read_text("utf-8")
    assert '<rect x="47.5" y="95" width="417" height="417" fill="#ffffff"/>' in svg


def test_icon_urls_follow_the_actual_artwork_revision(tmp_path, monkeypatch):
    script = build_script()
    public = tmp_path / "web" / "public"
    public.mkdir(parents=True)
    for name in ("favicon.svg", "favicon.ico", "manifest.webmanifest", *script.WEB_ICONS):
        target = public / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((script.PUBLIC / name).read_bytes())
    page = tmp_path / "web" / "index.html"
    page.write_bytes((ROOT / "web" / "index.html").read_bytes())
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr(script, "PUBLIC", public)
    before = script.icon_revision()
    assert page.read_text("utf-8").count(f"?v={before}") >= 5
    manifest = json.loads((public / "manifest.webmanifest").read_text("utf-8"))
    assert all(icon["src"].endswith(f"?v={before}") for icon in manifest["icons"])
    (public / "favicon.svg").write_bytes((public / "favicon.svg").read_bytes() + b"\n")
    script.stamp_icons()
    after = script.icon_revision()
    assert before != after
    assert f"?v={before}" not in page.read_text("utf-8")
    assert page.read_text("utf-8").count(f"?v={after}") >= 5


LAUNCHCTL = (
    "PID\tStatus\tLabel\n"
    "501\t0\tapplication.dev.heinzel.notifier.30134912.30134919.AAAA\n"
    "502\t0\tapplication.dev.heinzel.notifier.30134912.30134920.BBBB\n"
    "503\t0\tapplication.dev.heinzel.notifier2.1.2.CCCC\n"
    "504\t0\tapplication.com.example.other.1.2.DDDD\n"
    "-\t0\tapplication.dev.heinzel.notifier.30134912.30134921.EEEE\n"
    "505\t0\tcom.apple.Safari\n"
)
"""What `launchctl list` prints: two live notifiers, one dead entry, a look-alike bundle id and strangers."""


def listing(ages: dict[int, str]):
    commands: list[list[str]] = []

    def run(command, timeout=None):
        commands.append(list(command))
        if command[0].endswith("launchctl"):
            return subprocess.CompletedProcess(command, 0, LAUNCHCTL, "")
        pid = int(command[-1])
        return subprocess.CompletedProcess(command, 0 if pid in ages else 1, f" {ages.get(pid, '')}\n", "")

    return run, commands


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("00:07", 7), ("12:30", 750), ("01:02:03", 3723), ("2-01:00:00", 2 * 86400 + 3600), ("", None), ("x", None)],
)
def test_ps_elapsed_time_is_read_in_seconds(text, seconds):
    assert parse_etime(text) == seconds


def test_only_live_instances_with_exactly_this_bundle_id_are_found(tmp_path, bundled):
    run, commands = listing({501: "00:12", 502: "3-04:00:00"})
    found = NotifierApp(tmp_path / "home", bundled=bundled, runner=run).instances()
    assert [(item.pid, item.seconds, item.stale) for item in found] == [(501, 12, False), (502, 273600, True)]
    asked = {command[-1] for command in commands if command[0].endswith("ps")}
    assert asked == {"501", "502"}  # a look-alike id, a stranger and a dead entry are never even looked at


def test_clean_ends_only_what_outlived_any_notification(tmp_path, bundled):
    run, _ = listing({501: "00:12", 502: "3-04:00:00"})
    ended: list[tuple[int, int]] = []
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=run, kill=lambda pid, sig: ended.append((pid, sig)))
    assert app.clean() == 1
    assert [pid for pid, _ in ended] == [502]  # the one being posted right now (12 s old) lives
    assert app.clean(older_than=0) == 2  # told that nothing may live, it ends both
    assert [pid for pid, _ in ended][1:] == [501, 502]


def test_an_instance_that_is_already_gone_is_not_counted(tmp_path, bundled):
    run, _ = listing({502: "3-04:00:00"})

    def gone(pid, sig):
        raise ProcessLookupError

    assert NotifierApp(tmp_path / "home", bundled=bundled, runner=run, kill=gone).clean() == 0


def test_the_swift_app_ends_itself_whatever_hangs():
    source = (SOURCES / "main.swift").read_text(encoding="utf-8")
    assert "_exit(code)" in source  # `exit` can wait on a lock the main thread holds
    assert "exit(" not in source.replace("_exit(", "").replace("leave(", "")
    assert "NSApp.terminate" not in source
    assert "DispatchQueue.global" in source  # the timer isn't the main run loop, which is what may be stuck
    assert "exitAfter(CommandLine.arguments.contains" in source
    # Before any mode returns: `--status` once had no deadline, so a notification center that never answered kept an
    # instance alive forever, one more at every daemon tick.
    assert source.index("exitAfter(CommandLine.arguments.contains") < source.index('contains("--status")')
    assert LIFETIME >= 70  # macOS's question takes up to 60 s, then it posts


def test_an_app_that_ended_before_open_could_wait_still_has_its_answer(tmp_path, bundled):
    class Fast:
        def __call__(self, command, timeout=None):
            Path(command[command.index("--stdout") + 1]).write_text("authorized", encoding="utf-8")
            failure = "Unable to block on applications (initial call to kevent() failed: No such process)"
            return subprocess.CompletedProcess(command, 1, "", failure)

    assert NotifierApp(tmp_path / "home", bundled=bundled, runner=Fast()).status() == "authorized"
    assert NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner(code=1)).status().startswith("open failed")


DUMP = """\
--------------------------------------------------------------------------------
bundle id:                  Heinzel Notifier (0xeec)
path:                       /Users/me/Applications/Heinzel Notifier.app (0x1)
identifier:                 dev.heinzel.notifier
--------------------------------------------------------------------------------
bundle id:                  Heinzel Notifier (0xeed)
path:                       /private/var/T/pytest-of-me/pytest-9/home/Applications/Heinzel Notifier.app (0x2)
identifier:                 dev.heinzel.notifier
--------------------------------------------------------------------------------
bundle id:                  Heinzel Notifier (0xeee)
path:                       /Users/me/Code/worktree/src/HeinzelNotifier.app (0x3)
identifier:                 dev.heinzel.notifier
--------------------------------------------------------------------------------
bundle id:                  Look-alike (0xeef)
path:                       /Applications/Notifier.app (0x4)
identifier:                 dev.heinzel.notifier2
--------------------------------------------------------------------------------
bundle id:                  Other (0xef0)
path:                       /Applications/Other.app (0x5)
identifier:                 com.example.other
"""


def lsregister(commands: list[list[str]]):
    def run(command, timeout=None):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, DUMP if "-dump" in command else "", "")

    return run


def test_every_copy_macos_knows_under_this_exact_bundle_id_is_listed(tmp_path, bundled):
    app = NotifierApp(Path("/Users/me"), bundled=bundled, runner=lsregister([]))
    assert [str(path) for path in app.registered()] == [
        "/Users/me/Applications/Heinzel Notifier.app",
        "/private/var/T/pytest-of-me/pytest-9/home/Applications/Heinzel Notifier.app",
        "/Users/me/Code/worktree/src/HeinzelNotifier.app",
    ]


def test_strays_are_forgotten_and_the_installed_copy_and_strangers_are_left(tmp_path, bundled):
    commands: list[list[str]] = []
    app = NotifierApp(Path("/Users/me"), bundled=bundled, runner=lsregister(commands))
    assert app.unregister_strays() == 2
    forgotten = [command[-1] for command in commands if "-u" in command]
    assert forgotten == [
        "/private/var/T/pytest-of-me/pytest-9/home/Applications/Heinzel Notifier.app",
        "/Users/me/Code/worktree/src/HeinzelNotifier.app",
    ]


def test_a_test_never_launches_the_real_app_unless_it_says_so(tmp_path, bundled, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.delenv("HZ_REAL_NOTIFIER", raising=False)
    refused = NotifierApp(tmp_path / "home", bundled=bundled).status()  # the real runner: nothing is started
    assert refused.startswith("open failed: tests don't launch the real Heinzel Notifier")
    assert NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner("authorized")).status() == "authorized"
