"""docs/SETTINGS_RESTART.md says, for every setting, whether a change needs a restart; the markers must agree."""

import re
from pathlib import Path

import pytest

from heinzel import Config, CurrentUser, Kernel, Settings
from heinzel.kernel import discover
from heinzel.modules.system.config import ConfigService

pytestmark = pytest.mark.unit

TABLE = Path(__file__).resolve().parents[2] / "docs" / "SETTINGS_RESTART.md"
ROW = re.compile(r"^\| `([^`]+)` \| (yes|no) \| (.+) \|$", re.MULTILINE)


def _settings():
    records = discover(plugins=False)[0]
    kernel = Kernel(Config.empty().settings(Settings, "hz"), records, config=Config.empty())
    with kernel.resolve(CurrentUser).local("cli"):
        return {f"{s.section}.{s.key}": s for s in ConfigService(kernel).report().settings}


def _rows() -> dict[str, tuple[str, str]]:
    return {key: (restart, why) for key, restart, why in ROW.findall(TABLE.read_text())}


def test_every_setting_has_one_row_with_a_reason():
    rows = _rows()
    found = _settings()
    assert sorted(set(found) - set(rows)) == [], "settings missing from docs/SETTINGS_RESTART.md"
    assert sorted(set(rows) - set(found)) == [], "rows for settings that no longer exist"
    assert len(ROW.findall(TABLE.read_text())) == len(rows), "a setting is listed twice"
    assert all(len(why) > 20 for _restart, why in rows.values())


def test_the_table_and_the_x_live_markers_agree():
    rows = _rows()
    for key, setting in _settings().items():
        schema = setting.json_schema or {}
        restart = rows[key][0]
        if schema.get("x-live"):
            assert restart == "no", f"{key} is marked x-live, so the table must say it needs no restart"
        elif restart == "no":
            # Live without the marker only when its own control (not the generic list) edits it and applies it.
            assert schema.get("x-hidden"), f"{key} needs no restart: mark it x-live or hide it behind its own control"
        else:
            assert restart == "yes"
