from datetime import UTC, datetime, timedelta

import pytest

from heinzel.kernel import MAX_WINDOW, Span, Window, format_duration, parse_duration, parse_window

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


def test_rejects_durations_too_long_for_a_timedelta():
    with pytest.raises(ValueError, match="too long; write something like `10m`"):
        parse_duration("99999999999999d")


def test_a_window_looks_back_from_a_run_or_ahead_of_it():
    run = datetime(2026, 10, 26, 7, tzinfo=UTC)
    week = timedelta(days=7)
    assert parse_window("7d") == parse_window("last 7d") == Span("last", week)
    assert parse_window("7d").at(run) == Window(since=run - week, until=run)
    assert parse_window("next 7d").at(run) == Window(since=run, until=run + week)
    assert (str(parse_window("last 7d")), str(parse_window("next 90m"))) == ("7d", "next 90m")
    assert parse_window("366d").length == MAX_WINDOW
    assert parse_window("8784h").length == MAX_WINDOW


@pytest.mark.parametrize("text", ["367d", "8785h", "0d", "next 0d", "07d", "7D", "last7d", "1w", "\uff17d", "soon", 7])
def test_a_window_is_written_one_way_and_at_most_a_year(text):
    with pytest.raises(ValueError, match="window"):
        parse_window(text)


def test_formats_a_duration_in_its_largest_whole_unit():
    assert [format_duration(timedelta(seconds=s)) for s in (45, 90, 7200, 86400)] == ["45s", "90s", "2h", "1d"]
