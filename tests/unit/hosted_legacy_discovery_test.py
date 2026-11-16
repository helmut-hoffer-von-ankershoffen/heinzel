"""Fake-only tests for the opt-in hosted adapter; no installer, network or service manager."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/acceptance/hosted_legacy_discovery.py"
spec = importlib.util.spec_from_file_location("hosted_discovery", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def environment():
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW": "Focused job",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "macOS",
        "GITHUB_RUN_ATTEMPT": "1",
        "FOCUSED_KIND": "pytest",
        "FOCUSED_SELECTOR": module.SELECTOR,
        "FOCUSED_REPEAT": "1",
        "FOCUSED_STRESS": "false",
        "GITHUB_SHA": "a" * 40,
        "FOCUSED_REF": "validation/probe",
        "GITHUB_REF_NAME": "validation/probe",
    }


def test_exact_hosted_context():
    module.require_hosted(environment(), "darwin")
    with pytest.raises(ValueError, match="requires one unstressed"):
        module.require_hosted(environment(), "linux")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_ACTIONS", "false"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("FOCUSED_REPEAT", "2"),
        ("FOCUSED_STRESS", "true"),
        ("FOCUSED_SELECTOR", "tests/other.py"),
        ("GITHUB_SHA", "a" * 8),
        ("FOCUSED_REF", "other"),
    ],
)
def test_refuses_other_contexts(key, value):
    with pytest.raises(ValueError, match=r"requires one unstressed|exact source SHA|dispatch branch mismatch"):
        module.require_hosted(environment() | {key: value}, "darwin")


@pytest.mark.parametrize(
    ("status", "step", "mode", "expected"),
    [
        ("FAIL", module.FAILURE_STEP, "published", True),
        ("PASS", module.FAILURE_STEP, "published", False),
        ("REFUSED", module.FAILURE_STEP, "published", False),
        ("FAIL", "first update", "published", False),
        ("FAIL", module.FAILURE_STEP, "candidate", False),
    ],
)
def test_only_original_bridge_failure_is_followed(status, step, mode, expected):
    assert (
        module.eligible({"status": status, "first_failure": {"step": step}}, {"update_target": {"mode": mode}})
        is expected
    )


@pytest.mark.parametrize("probe_error", [False, True])
def test_preserves_original_failure_before_probe_and_never_replaces_it(tmp_path, monkeypatch, probe_error):
    record = {"status": "FAIL", "first_failure": {"step": module.FAILURE_STEP, "failure": "original"}}
    order = []
    result = (record, {"synthetic-secret"}, tmp_path / "case")

    def run(*args):
        order.append("original")
        return result

    def save(path, value, secrets):
        assert value is record
        assert secrets == {"synthetic-secret"}
        order.append("saved")
        path.write_text(json.dumps(value))

    def follow(root, out, probe):
        assert json.loads((out / "original-case6.json").read_text()) == record
        assert (out / "probe-intent.json").exists()
        order.append("probe")
        if probe_error:
            msg = "never expose this private detail"
            raise ValueError(msg)

    harness = SimpleNamespace(run_case=run, write_result=save)
    monkeypatch.setattr(module, "follow_up", follow)
    module.attach(harness, tmp_path, object())
    assert harness.run_case("6", tmp_path, {"update_target": {"mode": "published"}}) is not None
    assert record == {"status": "FAIL", "first_failure": {"step": module.FAILURE_STEP, "failure": "original"}}
    assert order == ["original", "saved", "probe"]
    if probe_error:
        assert "private detail" not in (tmp_path / "probe-error.json").read_text()
    with pytest.raises(FileExistsError):
        harness.run_case("6", tmp_path, {"update_target": {"mode": "published"}})
    assert order.count("probe") == 1


def test_probe_preparation_keeps_receipt_and_only_clears_disposable_core_bytecode(tmp_path):
    site = tmp_path / "tools/heinzel/lib/python3.12/site-packages"
    core = site / "heinzel/__pycache__"
    core.mkdir(parents=True)
    cache = core / "example.cpython-312.pyc"
    cache.write_bytes(b"compiled")
    source = site / "heinzel/example.py"
    source.write_text("unchanged")
    receipt = tmp_path / "tools/heinzel/uv-receipt.toml"
    receipt.write_text("untouched")
    (tmp_path / "guard").mkdir()
    (tmp_path / "bin").mkdir()
    for name in ("uv", "uvx", "launchctl", "systemctl"):
        (tmp_path / "guard" / name).write_bytes(b"fixture executable")
    env = module.prepare_probe(tmp_path)
    assert not cache.exists()
    assert source.read_text() == "unchanged"
    assert receipt.read_text() == "untouched"
    assert env["PATH"] == f"{tmp_path}/bin:/usr/bin:/bin"
    assert not {"GITHUB_TOKEN", "GH_TOKEN", "HZ_APP_PROFILE", "UV_CONSTRAINT", "PYTHONPATH"} & env.keys()
    assert (tmp_path / "bin/uv").read_bytes() == b"fixture executable"
    for name in ("launchctl", "systemctl"):
        assert (tmp_path / "bin" / name).read_bytes() == (tmp_path / "guard" / name).read_bytes()
        assert (tmp_path / "bin" / name).stat().st_mode & 0o777 == 0o700


def test_symlinked_core_is_refused(tmp_path):
    site = tmp_path / "tools/heinzel/lib/python3.12/site-packages/heinzel"
    site.mkdir(parents=True)
    target = tmp_path / "private.pyc"
    target.write_bytes(b"kept")
    (site / "escape.pyc").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        module.prepare_probe(tmp_path)
    assert target.read_bytes() == b"kept"


@pytest.mark.parametrize("bad", ["head", "bytes", "symlink", None])
def test_exact_source_verification(tmp_path, monkeypatch, bad):
    sha = "a" * 40
    names = [
        "scripts/acceptance/hosted_legacy_discovery.py",
        "scripts/acceptance/legacy_discovery_probe.py",
        "scripts/acceptance/run_acceptance.py",
        module.SELECTOR,
        ".github/workflows/focused-job.yml",
        "scripts/focused_job.py",
    ]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"committed")
    if bad == "bytes":
        (tmp_path / names[0]).write_bytes(b"changed")
    if bad == "symlink":
        (tmp_path / names[0]).unlink()
        (tmp_path / names[0]).symlink_to(tmp_path / names[1])

    def git(command, **kwargs):
        if command[-2:] == ["rev-parse", "HEAD"]:
            return ("b" * 40 if bad == "head" else sha).encode()
        assert command[-1].startswith(sha + ":")
        return b"committed"

    monkeypatch.setattr(module.subprocess, "check_output", git)
    if bad:
        with pytest.raises(ValueError, match=r"checkout SHA|source differs"):
            module.verify_source(tmp_path, sha)
    else:
        assert set(module.verify_source(tmp_path, sha)) == set(names)
