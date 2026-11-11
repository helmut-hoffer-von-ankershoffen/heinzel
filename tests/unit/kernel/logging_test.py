import json
import logging
from collections.abc import Iterator

import pytest

from heinzel.kernel._logging import (
    REDACTED,
    JsonFormatter,
    TextFormatter,
    correlate,
    correlation_id,
    formatter,
    get_logger,
    log_event,
    record_fields,
    redact,
    step,
)

pytestmark = pytest.mark.unit


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def logger() -> Iterator[tuple[logging.Logger, _Records]]:
    log = get_logger("test.structured")
    records = _Records()
    log.addHandler(records)
    log.setLevel(logging.DEBUG)
    yield log, records
    log.removeHandler(records)
    log.setLevel(logging.NOTSET)


DISK_FULL = "disk full"


def _fail(kind: type[Exception]) -> None:
    message = "boom"
    raise kind(message)


def _clock(*readings: float):
    values = iter(readings)
    return lambda: next(values)


def test_get_logger_names_the_area_under_heinzel():
    assert get_logger("plugin.install").name == "heinzel.plugin.install"
    with pytest.raises(ValueError, match="log area"):
        get_logger("Plugin Install")


def test_an_event_is_its_name_with_fields_as_extra(logger):
    log, records = logger
    log_event(log, "plugin.install.download", plugin="clock", version="1.2.0", path=None)
    [record] = records.records
    assert record.getMessage() == "plugin.install.download"
    assert record_fields(record) == {"event": "plugin.install.download", "plugin": "clock", "version": "1.2.0"}


@pytest.mark.parametrize("name", ["install", "Plugin.install", "plugin install", "plugin.", "plugin..x"])
def test_event_names_are_dotted_lower_case(logger, name):
    with pytest.raises(ValueError, match="event name"):
        log_event(logger[0], name)


def test_fields_that_clash_with_record_attributes_are_refused(logger):
    with pytest.raises(ValueError, match="clash"):
        log_event(logger[0], "plugin.install", module="clock")


def test_a_disabled_level_logs_nothing(logger):
    log, records = logger
    log.setLevel(logging.WARNING)
    log_event(log, "plugin.install.download")
    assert records.records == []


def test_secret_fields_are_redacted_by_key_name_also_nested():
    fields = {"api_key": "k", "GITHUB_TOKEN": "t", "owner_email": "a@b.c", "nested": {"password": "p", "ok": 1}}
    assert redact(fields) == {
        "api_key": REDACTED,
        "GITHUB_TOKEN": REDACTED,
        "owner_email": REDACTED,
        "nested": {"password": REDACTED, "ok": 1},
    }
    assert redact({"list": [{"secret": "s"}, "plain"]}) == {"list": [{"secret": REDACTED}, "plain"]}


def test_logged_secrets_are_redacted(logger):
    log, records = logger
    log_event(log, "connector.sign_in.token", token="ghp_secret", plugin="github")
    assert record_fields(records.records[0])["token"] == REDACTED


def test_a_step_logs_its_start_and_ok_outcome_with_the_duration(logger):
    log, records = logger
    with step(log, "desktop.install.download", clock=_clock(10.0, 10.25), plugin="desktop") as running:
        running.note(version="0.4.0")
    start, end = (record_fields(record) for record in records.records)
    assert start == {
        "event": "desktop.install.download",
        "correlation_id": start["correlation_id"],
        "plugin": "desktop",
        "phase": "start",
    }
    assert end == start | {"phase": "end", "outcome": "ok", "duration_ms": 250, "version": "0.4.0"}


def test_a_failing_step_logs_the_error_outcome_and_raises(logger):
    log, records = logger

    def copy() -> None:
        with step(log, "desktop.install.copy", clock=_clock(1.0, 1.5), path="/Applications") as running:
            running.note(version="0.4.0")
            raise OSError(DISK_FULL)

    with pytest.raises(OSError, match=DISK_FULL):
        copy()
    start, end = records.records
    assert end.levelno == logging.ERROR
    assert record_fields(end) == record_fields(start) | {
        "phase": "end",
        "outcome": "error",
        "duration_ms": 500,
        "error_type": "OSError",
        "error": "disk full",
        "version": "0.4.0",
    }


def test_a_skipped_step_says_why(logger):
    log, records = logger
    with step(log, "plugin.update.fetch", clock=_clock(0.0, 0.0)) as running:
        running.skip("already current")
    assert record_fields(records.records[-1]) | {"correlation_id": None} == {
        "event": "plugin.update.fetch",
        "correlation_id": None,
        "phase": "end",
        "outcome": "skipped",
        "reason": "already current",
        "duration_ms": 0,
    }


def test_steps_in_one_operation_share_its_correlation_id(logger):
    log, records = logger
    with correlate("op-1"), step(log, "update.apply.core", clock=_clock(0.0, 0.0)):
        assert correlation_id() == "op-1"
    assert {record_fields(record)["correlation_id"] for record in records.records} == {"op-1"}
    assert correlation_id() is None


def test_the_outermost_step_starts_a_correlation_id_and_nested_steps_reuse_it(logger):
    log, records = logger
    with step(log, "update.apply.run", clock=_clock(0.0, 0.0)), step(log, "update.apply.core", clock=_clock(0.0, 0.0)):
        pass
    ids = {record_fields(record)["correlation_id"] for record in records.records}
    assert len(ids) == 1
    assert correlation_id() is None
    with correlate() as generated:
        assert generated == correlation_id()
        assert len(generated) == 12


def test_the_json_formatter_writes_one_object_per_line(logger):
    log, records = logger
    log_event(log, "daemon.tick.run", outcome="ok", duration_ms=12, path="/Applications/Heinzelmaennchen.app")
    line = json.loads(JsonFormatter().format(records.records[0]))
    assert line.pop("at").endswith("+00:00")
    assert line == {
        "level": "INFO",
        "logger": "heinzel.test.structured",
        "event": "daemon.tick.run",
        "outcome": "ok",
        "duration_ms": 12,
        "path": "/Applications/Heinzelmaennchen.app",
    }


def test_the_json_formatter_keeps_plain_records_and_tracebacks(logger):
    log, records = logger
    try:
        _fail(ValueError)
    except ValueError:
        log.exception("plain message %s", "x", extra={"secret": "s"})
    line = json.loads(JsonFormatter().format(records.records[0]))
    assert line["event"] == "plain message x"
    assert line["secret"] == REDACTED
    assert "ValueError: boom" in line["traceback"]


def test_the_text_formatter_writes_the_event_and_its_fields(logger):
    log, records = logger
    log_event(log, "plugin.install.deps", plugin="clock", reason="two words", count=2, empty="")
    assert TextFormatter().format(records.records[0]) == (
        'plugin.install.deps plugin=clock reason="two words" count=2 empty=""'
    )
    try:
        _fail(RuntimeError)
    except RuntimeError:
        log_event(log, "plugin.install.deps", level=logging.ERROR, exc_info=True)
    assert "RuntimeError: boom" in TextFormatter().format(records.records[-1])


def test_the_log_format_setting_picks_the_formatter():
    assert isinstance(formatter("json"), JsonFormatter)
    assert isinstance(formatter("text"), TextFormatter)
