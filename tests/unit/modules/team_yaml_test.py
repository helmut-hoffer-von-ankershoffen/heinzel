"""The strict YAML of a team repository's data files (`team._yaml`): hostile content stays cheap and inert."""

import pytest

from heinzel.modules.team._yaml import MAX_BYTES, YamlError, dump, load

pytestmark = pytest.mark.unit


def test_core_scalars_only_so_on_yes_and_dates_stay_text():
    data = load("on: yes\nsince: 2026-10-24\ncount: 3\nratio: 0.5\nflag: true\nnothing: null\nid: 010\n")
    assert data == {
        "on": "yes",
        "since": "2026-10-24",
        "count": 3,
        "ratio": 0.5,
        "flag": True,
        "nothing": None,
        "id": "010",
    }


def test_an_empty_file_is_an_empty_mapping():
    assert load("") == {}


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("a: &x [1]\nb: *x\n", "anchors and aliases"),
        ("a: !!python/object:os.system x\n", "tag"),
        ("a: !!set {x}\n", "tag"),
        ("a: 1\na: 2\n", "duplicate key"),
        ("? [1]\n: x\n", "keys must be text"),
        ("1: x\n", "keys must be text"),
        ("- a\n", "mapping"),
        ("a: [\n", "doesn't parse"),
        ('a: "\\e[31m"\n', "control characters"),
        ("a: \x1b[31m\n", "control characters"),
        ("a: " + "[" * 40 + "]" * 40 + "\n", "nested deeper"),
        ("a: [" + ",".join(["1"] * 10_001) + "]\n", "more than"),
        ("a: '" + "x" * MAX_BYTES + "'\n", "larger than"),
    ],
)
def test_what_a_data_file_never_needs_is_refused(text, problem):
    with pytest.raises(YamlError, match=problem):
        load(text)


def test_a_control_character_in_a_key_is_refused():
    with pytest.raises(YamlError, match="control"):
        load('"\\u0007": x\n')


def test_dump_keeps_key_order_and_puts_the_header_in_comments():
    text = dump({"b": 1, "a": "2026-10-24"}, header="First\n\nThird")
    assert text.startswith("# First\n#\n# Third\nb: 1\n")
    assert load(text) == {"b": 1, "a": "2026-10-24"}
