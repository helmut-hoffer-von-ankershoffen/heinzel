"""The mechanical part of docs/SETTINGS_UX.md, for every setting of the kernel and the first-party plugins.

What Settings in the web app shows comes from each setting's JSON Schema: its title is the label, its description the
line of help, and its type, `enum`, `format` and `x-` keys pick the control. So a setting the schema says too little
about ends up as a text field nobody wants to type into; these tests keep the schema telling enough.
"""

import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from heinzel import Config, Kernel, ModuleSettings, Settings, SettingsLet
from heinzel.kernel import discover
from heinzel.kernel._bindings import CapabilitySettings
from heinzel.modules.system.config import ConfigService, SettingInfo

pytestmark = pytest.mark.unit

FIRST_PARTY = {
    f"heinzel-{path.name.removeprefix('heinzel-')}" for path in (Path(__file__).parents[2] / "plugins").iterdir()
}

MARKUP = re.compile(r"`|\bADR\b|\[[a-z_]+(\.[a-z_]+)*\]|\{ *[a-z_]+ *=|= *\[")
"""Backticks, ADR numbers and TOML (`[org]`, `{ name = ...}`, `= [9, 18]`): fine in docs, noise in Settings."""

JARGON = re.compile(r"\b(Dirs?|Usd|Ui|Api|Url|Gh|Gws|Acli|Max|Min|Num|Cfg)\b")
"""What pydantic makes of a field name without a title: `Data Dir`, `Ask Max Usd`."""


RECORDS = [record for record in discover()[0] if record.origin.builtin or record.origin.distribution in FIRST_PARTY]
"""The built-in modules and the first-party plugins; importing them reads no configuration."""

MODELS: list[tuple[str, type[ModuleSettings]]] = [
    ("hz", Settings),
    ("capabilities", CapabilitySettings),
    *((r.qualname, let.model) for r in RECORDS for let in r.module.lets if isinstance(let, SettingsLet)),
]
KEYS = [f"{section}.{key}" for section, model in MODELS for key in model.model_fields]

UNITS = {"seconds", "minutes", "hours", "days", "weeks", "usd", "per hour", "hour-of-day"}
FORMATS = {"iana-timezone", "path", "duration", "uri", "email", "password"}
"""The vocabulary docs/SETTINGS_UX.md documents and the web app reads."""


@cache
def _report() -> dict[str, SettingInfo]:
    """Every setting as the config report has it; built inside a test, so after the isolation of HZ_* variables."""
    kernel = Kernel(Config.empty().settings(Settings, "hz"), RECORDS, config=Config.empty())
    return {f"{s.section}.{s.key}": s for s in ConfigService(kernel).report().settings}


@pytest.fixture(params=KEYS)
def setting(request: pytest.FixtureRequest) -> SettingInfo:
    return _report()[request.param]


def _branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """The schema and, for `X | None`, the schema of X: where a type, format or enum may be."""
    inner = [b for b in schema.get("anyOf", []) if isinstance(b, dict) and b.get("type") != "null"]
    return [schema, *inner]


def _has(schema: dict[str, Any], key: str, value: object = None) -> bool:
    return any(key in b and (value is None or b[key] == value) for b in _branches(schema))


def test_first_party_plugins_are_covered():
    sections = {key.rpartition(".")[0] for key in KEYS}
    assert {"hz", "org", "clock", "github", "system.notify"} <= sections
    assert set(KEYS) == set(_report())


def test_every_setting_has_a_schema_a_plain_title_and_a_plain_description(setting):
    schema = setting.json_schema
    assert schema is not None, "no JSON Schema: Settings can only offer a text field"
    if schema.get("deprecated"):
        assert setting.description.startswith("Deprecated"), "say so first; Settings hides it"
        return
    title = schema.get("title", "")
    assert not JARGON.search(title), "give it a title=, the label Settings shows, in words"
    if "_" in setting.key:
        assert title != setting.key.replace("_", " ").title(), "give it a title=, in sentence case"
    assert setting.description.endswith("."), "a sentence"
    assert not MARKUP.search(setting.description), "plain words: no backticks, ADR numbers or TOML"
    assert len(setting.description) <= 320, "one or two sentences; the reference docs have room for more"


def test_known_values_are_in_the_schema_not_only_in_the_prose(setting):
    """A setting whose values are known offers them as choices: a Literal, `enum` or an `x-options` source."""
    schema = setting.json_schema or {}
    if re.search(r"\bone of\b", setting.description, re.IGNORECASE):
        assert _has(schema, "enum") or _has(schema, "x-options"), "known values belong in the schema"


def test_the_name_and_the_schema_agree_on_what_kind_of_value_it_is(setting):
    """Time zones get a picker, paths a Show in Finder, secrets never a text box, amounts a unit."""
    schema = setting.json_schema or {}
    if schema.get("deprecated"):
        return
    key = setting.key
    if re.search(r"time_?zone", key):
        assert _has(schema, "format", "iana-timezone")
    if re.search(r"_dirs?$|_path$|^checkout$", key):
        items = schema.get("items", {})
        assert _has(schema, "format", "path") or items.get("format") == "path"
    if re.search(r"token|password|secret", key):
        assert _has(schema, "writeOnly", value=True), "a secret: SecretStr"
    for pattern, unit in ((r"timeout|_seconds$", "seconds"), (r"_usd$", "usd"), (r"_days$", "days")):
        if re.search(pattern, key):
            assert schema.get("x-unit") == unit, f"say what the number counts: x-unit {unit}"


def test_choices_have_a_label_each_and_units_and_formats_are_known(setting):
    schema = setting.json_schema or {}
    labels = schema.get("x-labels")
    if labels is not None:
        enum = next(b["enum"] for b in _branches(schema) if "enum" in b)
        assert set(labels) == set(enum), "x-labels names exactly the choices"
    if "x-unit" in schema:
        assert schema["x-unit"] in UNITS
    for branch in [*_branches(schema), schema.get("items", {})]:
        if "format" in branch:
            assert branch["format"] in FORMATS
