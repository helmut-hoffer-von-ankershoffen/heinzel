"""Release entry points refuse missing summaries before doing release work."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("intro", [None, "", " \t\n "])
@pytest.mark.parametrize("entry", ["script", "release", "release-local", "workflow"])
def test_blank_summary_cannot_start_a_release(tmp_path: Path, intro: str | None, entry: str):
    calls = tmp_path / "calls"
    for name in ("gh", "uv", "git", "mktemp"):
        stub = tmp_path / name
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name}" >> "$CALLS"\nexit 1\n')
        stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", CALLS=str(calls))
    env.pop("INTRO", None)
    # The release target invokes the hook under a parent `make ... INTRO=...`. Do not let GNU make's recursive
    # command-line overrides make this fresh, intentionally argument-free invocation look like it got a summary.
    env.pop("MAKEFLAGS", None)
    env.pop("MAKEOVERRIDES", None)
    if intro is not None:
        env["INTRO"] = intro
    if entry == "workflow":
        workflow = yaml.safe_load((REPO / ".github/workflows/prepare-release.yml").read_text())
        step = workflow["jobs"]["pull-request"]["steps"][0]
        assert step["name"] == "Require a release summary"
        env.setdefault("INTRO", "")
        command = ["bash", "-e", "-c", step["run"]]
    elif entry == "script":
        command = ["bash", str(REPO / "scripts/release.sh"), "patch", "--open-pr"]
    else:
        shutil.copyfile(REPO / "Makefile", tmp_path / "Makefile")
        command = ["make", "--no-print-directory", entry]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode != 0
    assert "is required" in result.stderr
    # Make reads remote names while parsing; it must never dispatch or invoke release tooling.
    assert not calls.exists() or calls.read_text().splitlines() == ["git"]


@pytest.mark.parametrize("entry", ["release", "release-local"])
def test_make_passes_summary_as_data(tmp_path: Path, entry: str):
    shutil.copyfile(REPO / "Makefile", tmp_path / "Makefile")
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS"\nexit 7\n')
    gh.chmod(0o755)
    script = tmp_path / "scripts/release.sh"
    script.parent.mkdir()
    script.write_text('printf "%s\\n" "$INTRO" > "$ARGS"; exit 7\n')
    summary = 'Settings improve. $(shell touch INJECTED) `touch INJECTED` "quoted"'
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", ARGS=str(tmp_path / "args"))
    env.pop("INTRO", None)
    subprocess.run(
        ["make", "--no-print-directory", entry, f"INTRO={summary}"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
        timeout=10,
    )
    expected = f"intro={summary}" if entry == "release" else summary
    assert (tmp_path / "args").read_text().splitlines()[-1] == expected
    assert not (tmp_path / "INJECTED").exists()


def test_dispatch_requires_summary_input():
    workflow = yaml.safe_load((REPO / ".github/workflows/prepare-release.yml").read_text())
    # PyYAML's YAML 1.1 loader treats the unquoted GitHub Actions key `on` as True.
    assert workflow[True]["workflow_dispatch"]["inputs"]["intro"]["required"] is True
