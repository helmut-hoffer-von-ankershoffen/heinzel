"""The state module's local streams: a machine id, a presence line a day, adoption lines per version (`_streams`)."""

import json
from datetime import date
from typing import get_args

import pytest
from pydantic import ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.state._models import STREAMS, AdoptionLine, PresenceLine
from heinzel.modules.system.state._streams import MACHINE, Segment, Streams, problems

pytestmark = pytest.mark.unit

DAY = date(2026, 10, 24)
FORBIDDEN = {"user", "machine", "host", "path", "email", "text", "draft", "cost", "time", "command", "count"}
"""What a line never holds: who and where (its branch names that), content, costs, or anything finer than a day."""


def test_a_machine_id_is_random_made_once_and_kept(tmp_path):
    streams = Streams(tmp_path)

    first = streams.machine()

    assert MACHINE.fullmatch(first)
    assert streams.machine() == first
    assert Streams(tmp_path).machine() == first
    assert (tmp_path / "state" / "machine").stat().st_mode & 0o777 == 0o600


def test_a_damaged_machine_id_is_refused_rather_than_guessed(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "machine").write_text("my-laptop.example\n")

    with pytest.raises(HeinzelError, match="doesn't hold a machine id"):
        Streams(tmp_path).machine()


def test_presence_is_one_line_per_local_day_in_that_months_segment(tmp_path):
    streams = Streams(tmp_path)

    noted = [streams.note_presence(DAY, "0.4.0"), streams.note_presence(DAY, "0.4.0")]
    streams.note_presence(date(2026, 10, 25), "0.4.0")
    streams.note_presence(date(2026, 11, 1), "0.4.1")

    assert noted == [True, False]
    october, november = streams.segments("presence")
    assert october.path == "presence/2026-10.jsonl"
    assert [json.loads(line)["date"] for line in october.data.splitlines()] == ["2026-10-24", "2026-10-25"]
    assert json.loads(november.data) == {"v": 1, "date": "2026-11-01", "hz": "0.4.1", "kind": "active"}


def test_adoption_says_installed_once_then_each_new_version_once(tmp_path):
    streams = Streams(tmp_path)

    lines = [
        streams.note_version(DAY, "0.4.0", "macos"),
        streams.note_version(DAY, "0.4.0", "macos"),
        streams.note_version(date(2026, 11, 2), "0.5.0", "macos"),
        streams.note_version(date(2026, 11, 3), "0.5.0", "macos"),
    ]

    assert [line.kind if line else None for line in lines] == ["installed", None, "upgraded", None]
    assert [s.path for s in streams.segments("adoption")] == ["adoption/2026-10.jsonl", "adoption/2026-11.jsonl"]


def test_a_stream_directory_that_is_a_link_is_never_written_through(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "presence").symlink_to(elsewhere)

    with pytest.raises(HeinzelError, match="symbolic link"):
        Streams(tmp_path).note_presence(DAY, "0.4.0")
    assert not list(elsewhere.iterdir())


def test_a_segment_that_is_a_link_is_never_read(tmp_path):
    streams = Streams(tmp_path)
    streams.note_presence(DAY, "0.4.0")
    secret = tmp_path / "secret"
    secret.write_text("not a stream\n")
    (tmp_path / "state" / "presence" / "2026-09.jsonl").symlink_to(secret)

    assert [s.name for s in streams.segments("presence")] == ["2026-10.jsonl"]


def test_a_line_cut_off_by_a_crash_stays_its_own_line(tmp_path):
    streams = Streams(tmp_path)
    segment = tmp_path / "state" / "presence" / "2026-10.jsonl"
    segment.parent.mkdir(parents=True)
    segment.write_bytes(b'{"v":1,"date":"2026-10-2')

    streams.note_presence(DAY, "0.4.0")

    assert segment.read_bytes().splitlines()[1] == PresenceLine(date=DAY, hz="0.4.0").model_dump_json().encode()


def test_problems_name_every_line_that_may_not_leave_the_machine():
    good = PresenceLine(date=DAY, hz="0.4.0").model_dump_json().encode()
    extra = b'{"v":1,"date":"2026-10-24","hz":"0.4.0","kind":"active","host":"mira-laptop"}'
    elsewhere = PresenceLine(date=date(2026, 9, 30), hz="0.4.0").model_dump_json().encode()
    segment = Segment("presence", "2026-10.jsonl", b"\n".join([good, extra, elsewhere, b"{"]))

    found = problems(segment)

    assert [p.split(":")[1] for p in found[:3]] == ["2", "3", "4"]
    assert "Extra inputs are not permitted" in found[0]
    assert "not in 2026-10" in found[1]
    assert found[-1].endswith("its last line is cut off")
    assert problems(Segment("presence", "2026-10.jsonl", good + b"\n")) == []


@pytest.mark.parametrize("model", STREAMS.values())
def test_no_stream_line_has_a_field_that_could_name_or_measure_a_person(model):
    assert not set(model.model_fields) & FORBIDDEN


def test_a_line_holds_only_its_fields_and_a_known_kind():
    with pytest.raises(ValidationError):
        AdoptionLine.model_validate_json('{"v":1,"date":"2026-10-24","hz":"0.4.0","kind":"uninstalled","os":"macos"}')
    with pytest.raises(ValidationError):
        PresenceLine.model_validate_json('{"v":true,"date":"2026-10-24","hz":"0.4.0","kind":"active"}')
    assert set(get_args(AdoptionLine.model_fields["os"].annotation)) == {"macos", "linux", "other"}
