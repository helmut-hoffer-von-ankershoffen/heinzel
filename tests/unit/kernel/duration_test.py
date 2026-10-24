from datetime import timedelta

import pytest

from heinzel.kernel import parse_duration

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [("30s", timedelta(seconds=30)), ("5m", timedelta(minutes=5)), ("2h", timedelta(hours=2)), ("1d", timedelta(1))],
)
def test_parses_whole_numbers_with_a_unit(text, expected):
    assert parse_duration(text) == expected


def test_passes_timedeltas_through():
    assert parse_duration(timedelta(minutes=3)) == timedelta(minutes=3)


@pytest.mark.parametrize("text", ["5", "0m", "-5m", "1.5h", "5 m", "5w", ""])
def test_rejects_anything_else(text):
    with pytest.raises(ValueError, match="positive whole number"):
        parse_duration(text)


@pytest.mark.parametrize("value", [10, 1.5, None, True, ["5m"]])
def test_rejects_values_that_are_not_text_with_a_hint(value):
    with pytest.raises(ValueError, match="write `10m`"):
        parse_duration(value)
