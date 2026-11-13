"""The desktop app's icon is the swarm at every size, and the packager is pointed at it (no app is launched)."""

import re
import struct
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "desktop" / "assets"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PIXELS_BY_TYPE = {
    b"ic07": 128,
    b"ic08": 256,
    b"ic09": 512,
    b"ic10": 1024,
    b"ic11": 32,  # 16@2x
    b"ic12": 64,  # 32@2x
    b"ic13": 256,  # 128@2x
    b"ic14": 512,  # 256@2x
}
"""The PNG chunks of an .icns and their edge: 128 to 1024, with every @2x; 16 and 32 are the ARGB ic04 and ic05."""


def chunks(data: bytes) -> dict[bytes, bytes]:
    assert data[:4] == b"icns"
    assert struct.unpack(">I", data[4:8])[0] == len(data)
    found, offset = {}, 8
    while offset < len(data):
        kind, length = data[offset : offset + 4], struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        found[kind] = data[offset + 8 : offset + length]
        offset += length
    return found


def test_icns_holds_every_size_at_1x_and_2x():
    found = chunks((ASSETS / "heinzel.icns").read_bytes())
    for kind, pixels in PIXELS_BY_TYPE.items():
        png = found[kind]
        assert png[:8] == PNG_MAGIC, kind
        assert struct.unpack(">II", png[16:24]) == (pixels, pixels), kind
    assert {b"ic04", b"ic05"} <= found.keys()  # 16 and 32 pixels


def test_windows_and_linux_icons_are_swarm_sized():
    png = (ASSETS / "heinzel.png").read_bytes()
    assert png[:8] == PNG_MAGIC
    assert struct.unpack(">II", png[16:24]) == (512, 512)
    ico = (ASSETS / "heinzel.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind, count) == (0, 1, 6)
    sizes = [ico[6 + 16 * index] or 256 for index in range(count)]
    assert sizes == [16, 32, 48, 64, 128, 256]


def test_packager_uses_the_committed_icon():
    source = (ROOT / "desktop" / "ci" / "package.mjs").read_text(encoding="utf-8")
    assert re.search(r'icon:\s*path\.join\(root,\s*"assets",\s*"heinzel"\)', source)  # the packager adds the extension
    assert (ASSETS / "heinzel.icns").is_file()


def test_icons_script_draws_the_desktop_icons_from_the_swarm():
    script = (ROOT / "scripts" / "notifier.py").read_text(encoding="utf-8")
    assert "_icns(icon, work, DESKTOP_ICON, SWARM)" in script
