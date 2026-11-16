"""Keep the instrumented bridge trace bounded and separate from acceptance."""

import errno
import importlib.util
import json
import stat
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def load(name: str):
    path = Path(__file__).resolve().parents[2] / "scripts" / "acceptance" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"legacy_diagnostic_{name}", path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trace = load("legacy_discovery_trace")
harness = load("run_acceptance")


@pytest.mark.parametrize("trampoline", [False, True])
def test_console_must_match_installed_interpreter_and_entry_point(tmp_path, trampoline):
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "hz"
    expected = tmp_path / "tools/heinzel/bin/python"
    header = f"#!/bin/sh\n'''exec' '{expected}' \"$0\" \"$@\"\n' '''\n" if trampoline else f"#!{expected}\n"
    executable.write_bytes(header.encode() + trace.CONSOLE_BODY)
    assert trace.console(tmp_path, expected) == executable
    with pytest.raises(ValueError, match="entry point"):
        trace.console(tmp_path, binary / "python")
    executable.write_bytes(executable.read_bytes().replace(b"cli()", b"main()"))
    with pytest.raises(ValueError, match="entry point"):
        trace.console(tmp_path, expected)


def test_trace_records_swallowed_http_failure_without_private_fields(tmp_path):
    observer = trace.Trace(tmp_path)
    source = str(tmp_path / "heinzel/modules/system/_update.py")
    namespace = {"error": urllib.error.HTTPError("https://example.invalid/private", 403, "secret", {}, None)}
    exec(  # noqa: S102 - fixed synthetic frame for the real trace callback
        compile("def _asked_latest():\n    try: raise error\n    except Exception: return None", source, "exec"),
        namespace,
    )
    previous = sys.gettrace()
    sys.settrace(observer)
    try:
        for _ in range(20):
            assert namespace["_asked_latest"]() is None
    finally:
        sys.settrace(previous)
        namespace["error"].close()
    trace.sidecar(tmp_path, observer.events)
    report = trace.read_sidecar(tmp_path)
    assert stat.S_IMODE((tmp_path / trace.FILENAME).stat().st_mode) == 0o600
    assert len(report["events"]) == 16
    assert report["events"][:2] == [
        {"operation": "_asked_latest", "event": "http_error", "status": 403},
        {"operation": "_asked_latest", "event": "return", "version": None},
    ]
    assert "secret" not in json.dumps(report)
    assert "private" not in json.dumps(report)
    with pytest.raises(FileExistsError):
        trace.sidecar(tmp_path, [])


def test_diagnostic_is_never_accepted_and_literal_verdict_unmodified():
    common = ["--expect-version", "0.9.50", "--expect-app-version", "0.9.50", "--case", "6"]
    literal = harness.parse_args(common)
    diagnostic = harness.parse_args([*common, "--diagnostic-legacy-discovery"])
    assert harness.verdict(literal, {"6": {"status": "FAIL"}}) == "REJECTED"
    assert harness.verdict(diagnostic, {"6": {"status": "FAIL"}}) == "NOT_ACCEPTANCE_DIAGNOSTIC"
    assert harness.verdict(diagnostic, {"6": {"status": "PASS"}}) == "NOT_ACCEPTANCE_DIAGNOSTIC"
    with pytest.raises(SystemExit):
        harness.parse_args([*common, "--case", "7", "--diagnostic-legacy-discovery"])
    with pytest.raises(ValueError, match="hosted macOS"):
        trace.require_hosted({}, "darwin")


def test_sidecar_refuses_unbounded_or_unexpected_fields(tmp_path):
    row = {"operation": "highest_version", "event": "return", "version": "0.9.50\nextra"}
    trace.sidecar(tmp_path, [row])
    with pytest.raises(ValueError, match="unsafe trace version"):
        trace.read_sidecar(tmp_path)


@pytest.mark.parametrize(
    "writer_error",
    [FileExistsError(), PermissionError(), OSError(errno.ENOSPC, "disk full"), TypeError(), ValueError()],
)
@pytest.mark.parametrize("updater_error", [None, SystemExit(17), RuntimeError("updater failure")])
def test_sidecar_failure_preserves_updater_outcome(monkeypatch, tmp_path, writer_error, updater_error):
    def update(*_args, **_kwargs):
        if updater_error is not None:
            raise updater_error

    def write(*_args):
        raise writer_error

    monkeypatch.setattr(trace.runpy, "run_module", update)
    monkeypatch.setattr(trace, "sidecar", write)
    previous = sys.gettrace()
    try:
        if updater_error is None:
            trace.trace_update(tmp_path, tmp_path)
        else:
            with pytest.raises(type(updater_error)) as caught:
                trace.trace_update(tmp_path, tmp_path)
            assert caught.value is updater_error
    finally:
        sys.settrace(previous)


@pytest.mark.parametrize("diagnostic", [False, True])
def test_update_retains_original_failure_and_uses_literal_command_by_default(tmp_path, diagnostic):
    calls = []

    def run(command, *_args):
        calls.append(command)
        return 0, '{"before":"0.9.43","after":"0.9.43","updated":false}', ""

    case = SimpleNamespace(
        ctx={
            "update_target": {"mode": "published", "version": "0.9.50"},
            "legacy_discovery_trace": {"read_sidecar": trace.read_sidecar} if diagnostic else None,
        },
        notes={"legacy_bridge_verified": True},
        prefix=tmp_path / "tools/heinzel",
        bin=tmp_path / "bin",
        root=tmp_path,
        run=run,
    )
    with pytest.raises(harness.StepFailed, match="updated=False"):
        harness.run_update(case, "bridge", previous=harness.LEGACY_BRIDGE_VERSION)
    if diagnostic:
        assert calls[0][1:4] == ["-I", "-S", "-B"]
        assert case.notes["legacy_discovery_trace"]["unavailable"] is True
    else:
        assert calls == [[str(case.bin / "hz"), "system", "update", "--json"]]
        assert "legacy_discovery_trace" not in case.notes
