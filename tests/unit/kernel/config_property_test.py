"""Properties of configuration loading over generated files: later files win key by key, broken ones say so."""

import json
import tomllib

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from heinzel import Config, HeinzelError

pytestmark = pytest.mark.unit

QUICK = settings(
    max_examples=40,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],  # each example overwrites the same two files
)
KEY = st.from_regex(r"[a-z][a-z0-9_-]{0,7}", fullmatch=True)
SCALAR = (
    st.integers(-(2**63), 2**63 - 1)
    | st.booleans()
    | st.text(st.characters(exclude_categories=("Cc", "Cs")), max_size=12)
)
#: Sections of keys, and sections of sub-tables of keys, as `[a]` and `[a.b]` tables in TOML.
TABLES = st.dictionaries(
    KEY, st.dictionaries(KEY, SCALAR | st.dictionaries(KEY, SCALAR, max_size=3), max_size=4), max_size=4
)


def toml(tables: dict[str, dict[str, object]]) -> str:
    """TOML for two levels of tables; strings without control characters as JSON strings are TOML basic strings."""

    def value(v: object) -> str:
        return ("true" if v else "false") if isinstance(v, bool) else json.dumps(v, ensure_ascii=False)

    lines: list[str] = []
    for name, table in tables.items():
        lines.append(f'["{name}"]')
        lines += [f'"{k}" = {value(v)}' for k, v in table.items() if not isinstance(v, dict)]
        for sub, values in table.items():
            if isinstance(values, dict):
                lines.append(f'["{name}"."{sub}"]')
                lines += [f'"{k}" = {value(v)}' for k, v in values.items()]
    return "\n".join(lines) + "\n"


def merged(low: dict, high: dict) -> dict:
    """What layering two files means: tables merge, everything else in the higher file replaces."""
    result = dict(low)
    for key, value in high.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


@pytest.fixture
def files(monkeypatch, tmp_path):
    """The user file and the explicit file (`HZ_CONFIG`), the lowest and the highest precedence."""
    user, explicit = tmp_path / "home" / "config.toml", tmp_path / "explicit.toml"
    user.parent.mkdir()
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user.parent))
    monkeypatch.setenv("HZ_CONFIG", str(explicit))
    return user, explicit, tmp_path


@QUICK
@given(low=TABLES, high=TABLES)
def test_a_later_file_overrides_single_keys_not_whole_tables(files, low, high):
    user, explicit, cwd = files
    user.write_text(toml(low), encoding="utf-8")
    explicit.write_text(toml(high), encoding="utf-8")

    loaded = Config.load(cwd)

    assert dict(loaded.data) == merged(low, high)
    for section in {*low, *high}:
        assert loaded.section(section) == merged(low, high)[section]


@QUICK
@given(text=st.text(max_size=200))
def test_any_file_loads_or_is_a_heinzel_error_naming_it(files, text):
    user, explicit, cwd = files
    user.unlink(missing_ok=True)
    explicit.write_text(text, encoding="utf-8")
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        with pytest.raises(HeinzelError, match="not valid TOML") as raised:
            Config.load(cwd)
        assert str(explicit) in str(raised.value)
    else:
        assert Config.load(cwd).data == tomllib.loads(text)


@QUICK
@given(tables=TABLES, path=st.lists(KEY, min_size=1, max_size=3))
def test_a_dotted_section_is_the_nested_table_or_empty(tables, path):
    expected: object = tables
    for part in path:
        expected = expected.get(part) if isinstance(expected, dict) else None
    assert Config(files=(), data=tables).section(".".join(path)) == (expected if isinstance(expected, dict) else {})
