"""Exercise the app and widget's shared catalog without launching either."""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.macos_specific,
    pytest.mark.skipif(sys.platform != "darwin", reason="The Apple catalog uses the macOS Swift SDK"),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(75)
def test_mac_extra_large_roster_bounds_order_fallback_and_overflow(tmp_path: Path) -> None:
    probe = tmp_path / "roster-probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            str(ROOT / "apple/Shared/ActivityCatalog.swift"),
            str(ROOT / "apple/Shared/ActivityWidgetData.swift"),
            str(ROOT / "apple/Shared/NativeRoute.swift"),
            str(ROOT / "tests/fixtures/apple-widget-roster.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=55,
    )
    subprocess.run([str(probe)], check=True, timeout=20)


@pytest.mark.timeout(75)
def test_catalog_publishes_complete_scoped_generations_atomically(tmp_path: Path) -> None:
    probe = tmp_path / "catalog-probe"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "6",
            "-target",
            f"{platform.machine()}-apple-macosx15.0",
            str(ROOT / "apple/Shared/ActivityCatalog.swift"),
            str(ROOT / "apple/Shared/ActivityWidgetData.swift"),
            str(ROOT / "apple/Shared/NativeRoute.swift"),
            str(ROOT / "tests/fixtures/apple-activity-catalog.swift"),
            "-o",
            str(probe),
        ],
        check=True,
        timeout=55,
    )
    subprocess.run([str(probe), str(tmp_path / "catalog.sqlite")], check=True, timeout=20)


def test_refresh_keeps_old_requested_pin_in_bounded_artwork_and_all_eligible_choices() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the workspace bridge")
    source = "\n".join(
        (ROOT / "apple/App" / f"{n}.swift").read_text(encoding="utf-8")
        for n in ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    )
    selection = source.split("            const eligible =", 1)[1].split("            const roster =", 1)[0]
    script = (
        """
const assert = require('node:assert/strict');
const sessions = Array.from({length: 125}, (_, n) => ({
  session_id: 'hz-' + String(n).padStart(26, '0'),
  name: n === 2 || n === 124 ? 'Duplicate' : `Heinzel ${n}`,
  state: n === 60 ? 'retired' : 'awake', activity: n === 124 ? 'stuck' : 'idle',
  waiting_count: n === 124 ? 3 : 0, last_approved_at: '2026-01-01T00:00:00Z',
  secret: 'must not enter catalog'
}));
const seconds = value => typeof value === 'string' && Number.isFinite(Date.parse(value))
  ? Date.parse(value) / 1000 : null;
const detailIDs = [sessions[124].session_id];
const eligible ="""
        + selection
        + """
assert.equal(catalog.length, 124);
assert.equal(rich.length, 40);
assert.equal(rich[0].session_id, sessions[124].session_id);
assert.equal(catalog.filter(item => item.name === 'Duplicate').length, 2);
assert.deepEqual(Object.keys(catalog[0]).sort(), ['activity', 'asks', 'id', 'lastApproved', 'name', 'state']);
assert.deepEqual([catalog.at(-1).activity, catalog.at(-1).asks, catalog.at(-1).lastApproved], ['stuck', 3, 1767225600]);
"""
    )
    subprocess.run([node, "-e", script], check=True, timeout=10)


def test_foreground_waiting_count_matches_pending_nonretired_sessions() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the workspace bridge")
    source = "\n".join(
        (ROOT / "apple/App" / f"{n}.swift").read_text(encoding="utf-8")
        for n in ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    )
    expression = source.split("                waiting: ", 1)[1].split(",\n", 1)[0]
    script = """
const assert = require('node:assert/strict');
let sessions = [
  {state:'awake', waiting_count:1}, {state:'waiting', waiting_count:0},
  {state:'retired', waiting_count:2}, {state:'asleep', waiting_count:3}
];
const count = () => EXPR;
assert.equal(count(), 2);
sessions = Array.from({length: 10001}, () => ({state:'awake', waiting_count:1}));
assert.equal(count(), 9999);
""".replace("EXPR", expression)
    subprocess.run([node, "-e", script], check=True, timeout=10)
