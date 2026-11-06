"""Release entry points refuse missing summaries before doing release work."""

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[2]
UI_PLANNER = runpy.run_path(str(REPO / "scripts/prepare_release_ui.py"))


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


@pytest.mark.parametrize(
    ("project", "versions", "plan", "calls"),
    [
        ("heinzel", ("0.9.19", "0.9.20"), "known", ["plan", "switch", "check", "bump"]),
        ("heinzel", ("0.9.20", "0.9.21"), "new", ["plan", "switch", "apply", "check", "bump"]),
        ("heinzel", ("0.9.21", "0.9.21"), "new", ["plan", "switch", "apply", "check", "lock"]),
        ("heinzel", ("0.9.20", "0.9.21"), "refused", ["plan"]),
        ("heinzel-example", ("0.9.20", "0.9.21"), "plugin", ["switch", "bump"]),
    ],
)
def test_core_records_the_exact_ui_contract_in_the_release_branch(tmp_path, project, versions, plan, calls):
    current, version = versions
    git = tmp_path / "git"
    git.write_text(
        """#!/bin/sh
case "$1 $2" in
  'status --porcelain'|'fetch --tags') exit 0 ;;
  'rev-parse --abbrev-ref') echo main ;;
  'rev-parse -q') exit 1 ;;
  'tag --merged') echo v0.9.20 ;;
  'switch --quiet') echo switch >> "$CALLS" ;;
  'apply --index') echo apply >> "$CALLS"; exec "$REAL_GIT" apply "$3" ;;
  *) exit 98 ;;
esac
"""
    )
    uv = tmp_path / "uv"
    uv.write_text(
        """#!/bin/sh
case "$*" in
  *'bump-my-version show current_version') echo "$CURRENT" ;;
  *'bump-my-version show --increment patch new_version') echo "$NEXT" ;;
  *'git-cliff --offline --bumped-version') echo v0.9.22 ;;
  'run --frozen --no-sync --no-build python scripts/release_version.py '*)
    shift 6
    exec "$TEST_PYTHON" "$VERSION_SCRIPT" "$@"
    ;;
  'version --color never') printf '%s %s\n' "$PROJECT" "$CURRENT" ;;
  'run --frozen --no-sync --no-build python -I scripts/prepare_release_ui.py '*)
    echo plan >> "$CALLS"
    test "$*" = "run --frozen --no-sync --no-build python -I scripts/prepare_release_ui.py $NEXT" || exit 97
    if [ "$PLAN" = refused ]; then exit 1; fi
    cat planned.patch
    ;;
  'run --frozen --no-sync --no-build python -I scripts/acceptance/run_acceptance.py --check-foreground-ui '*)
    shift 5
    echo check >> "$CALLS"
    exec "$TEST_PYTHON" "$@"
    ;;
  *'bump-my-version bump '*) echo bump >> "$CALLS"; exit 29 ;;
  'lock --quiet') echo lock >> "$CALLS"; exit 29 ;;
  *) exit 99 ;;
esac
"""
    )
    for stub in (git, uv):
        stub.chmod(0o755)
    harness = tmp_path / "scripts/acceptance/run_acceptance.py"
    original = (REPO / "scripts/acceptance/run_acceptance.py").read_text()
    previous = '    "0.9.20": ("ui", "serve"),\n'
    before, opening, remainder = original.partition("FOREGROUND_UI = {\n")
    _, closing, after = remainder.partition("}\n")
    assert opening
    assert closing
    original = before + opening + previous + closing + after
    if project == "heinzel":
        harness.parent.mkdir(parents=True)
        harness.write_text(original)
        (tmp_path / "planned.patch").write_text(UI_PLANNER["policy_patch"](harness, version))
    events = tmp_path / "calls"
    env = dict(
        os.environ,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        INTRO="A release summary",
        CURRENT=current,
        NEXT=version,
        PROJECT=project,
        PLAN=plan,
        CALLS=str(events),
        REAL_GIT=shutil.which("git") or "git",
        TEST_PYTHON=sys.executable,
        VERSION_SCRIPT=str(REPO / "scripts/release_version.py"),
    )
    result = subprocess.run(
        ["bash", str(REPO / "scripts/release.sh"), "auto" if current == version else "patch", "--open-pr"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == (1 if plan == "refused" else 29), result.stderr
    assert events.read_text().splitlines() == calls
    if plan == "new":
        expected = original.replace(previous, previous + f'    "{version}": ("ui", "serve"),\n')
        assert harness.read_text() == expected
    elif project == "heinzel":
        assert harness.read_text() == original


@pytest.mark.parametrize("version", ["0.9.21.dev1", "0.9.021", "v0.9.21"])
def test_ui_policy_patch_requires_a_canonical_final_version(version):
    with pytest.raises(ValueError, match="exact final Core version"):
        UI_PLANNER["policy_patch"](REPO / "scripts/acceptance/run_acceptance.py", version)


def test_ui_policy_patch_refuses_conflicting_existing_version(tmp_path):
    harness = tmp_path / "run_acceptance.py"
    harness.write_text('FOREGROUND_UI = {\n    "0.9.21": ("ui", "open"),\n}\n')
    with pytest.raises(ValueError, match="conflicting foreground UI"):
        UI_PLANNER["policy_patch"](harness, "0.9.21")


@pytest.mark.parametrize("defect", ["alias", "hidden", "hidden-parent", "missing-option", "duplicate"])
def test_ui_policy_probe_refuses_unverified_command_metadata(defect):
    serve = {
        "command": "hz ui open" if defect == "alias" else "hz ui serve",
        "hidden": defect == "hidden",
        "deprecated": False,
        "parameters": [{"opts": ["--port"], "secondary_opts": [] if defect == "missing-option" else ["--no-browser"]}],
    }
    commands = [serve, serve] if defect == "duplicate" else [serve]
    ui = {"command": "hz ui", "hidden": defect == "hidden-parent", "deprecated": False, "commands": commands}
    metadata = {"schema_version": 1, "root": {"commands": [ui]}}
    with pytest.raises(ValueError, match="does not verify the reviewed foreground contract"):
        UI_PLANNER["verify_foreground"](metadata)
