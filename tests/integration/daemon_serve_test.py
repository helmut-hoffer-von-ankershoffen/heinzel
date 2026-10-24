"""`hz system serve daemon`: the foreground loop, through the CLI head, with a test-only job module."""

import json
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from heinzel import Module
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def kernel(make_kernel, calls):
    module = Module("example", "Jobs for tests.")

    @module.job(schedule="* * * * *")
    def minutely() -> None:
        """Run every minute."""
        calls.append("minutely")

    return make_kernel(builtins=True, example=module)


def test_serve_daemon_prints_each_tick_as_a_json_line(kernel, monkeypatch, calls):
    import heinzel.heads.daemon as head

    real = head.serve

    def twice(kernel_, interval, report):
        assert interval == timedelta(seconds=30)
        real(kernel_, interval, report, ticks=2, sleep=lambda _: None)

    monkeypatch.setattr(head, "serve", twice)
    result = runner.invoke(build(kernel), ["system", "serve", "daemon", "--interval", "30s"])
    assert result.exit_code == 0, result.output
    first, second = (json.loads(line) for line in result.output.splitlines())
    assert [run["job"] for run in first["runs"]] == ["example:minutely"]
    assert second["runs"] == []


def test_serve_daemon_reports_failing_ticks_and_goes_on(kernel, monkeypatch, settings):
    import heinzel.heads.daemon as head

    settings.state_dir.mkdir(parents=True, exist_ok=True)
    (settings.state_dir / "daemon").write_text("not a directory", encoding="utf-8")  # the tick lock can't be taken
    real = head.serve
    monkeypatch.setattr(head, "serve", lambda k, i, report: real(k, i, report, ticks=2, sleep=lambda _: None))
    result = runner.invoke(build(kernel), ["system", "serve", "daemon"])
    assert result.exit_code == 0
    assert result.output.count("Tick failed with a bug") == 2


def test_serve_daemon_rejects_a_malformed_interval(kernel):
    result = runner.invoke(build(kernel), ["system", "serve", "daemon", "--interval", "soon"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_serve_daemon_reports_heinzel_errors_as_failed_ticks(kernel, monkeypatch):
    import heinzel.heads.daemon as head
    from heinzel import HeinzelError

    monkeypatch.setattr(head, "serve", lambda k, i, report: report(HeinzelError("ledger [broken]")))
    result = runner.invoke(build(kernel), ["system", "serve", "daemon"])
    assert result.exit_code == 0
    assert "Tick failed: ledger [broken]" in result.output
