"""CI runs what developers run locally."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import runpy
import signal
import stat
import subprocess
import sys
import time
import tomllib
import zipfile
from copy import deepcopy
from functools import lru_cache
from itertools import product
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from heinzel.testing import scaled

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def test_installed_browser_service_journey_keeps_cold_starts_out_of_browser_shards():
    recipe = "show-browser-allocation:\n\t@printf '%s\\n' '$(E2E_WEB)' '$(E2E_WEB_1)' '$(E2E_WEB_2)' '$(E2E_WEB_3)'"
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", "Makefile", "-f", "-", "show-browser-allocation"],
        cwd=REPO,
        input=recipe + "\n",
        check=True,
        capture_output=True,
        text=True,
        timeout=scaled(30),
    )
    browser, first, second, third = [set(line.split()) for line in result.stdout.splitlines()]
    journey = "tests/e2e/ui_service_browser_test.py"
    assert journey not in browser
    assert journey not in first | second | third
    assert "tests/e2e/ui_service_test.py" not in browser
    assert first.isdisjoint(second | third)
    assert second.isdisjoint(third)
    assert first | second | third == browser
    assert first == {"tests/e2e/web_ui_test.py"}
    assert third == {"tests/e2e/scheduled_connected_test.py"}
    assert second == browser - first - third


_SANDBOX_SCRIPT = REPO / "desktop" / "ci" / "restore_linux_sandbox.py"
_SANDBOX_SPEC = importlib.util.spec_from_file_location("electron_linux_sandbox", _SANDBOX_SCRIPT)
assert _SANDBOX_SPEC is not None
assert _SANDBOX_SPEC.loader is not None
electron_linux_sandbox = importlib.util.module_from_spec(_SANDBOX_SPEC)
_SANDBOX_SPEC.loader.exec_module(electron_linux_sandbox)

WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
# Steps that legitimately don't go through make, by workflow: setup, checks that only exist in CI, and release
# plumbing. Everything else is a make target, so it runs the same way locally.
NOT_MAKE = {  # workflow: {start of the step's command: why}
    "*": {"uv sync": "installs what the job needs"},
    "ci.yml": {
        'version="$(sed -n': "archives a release candidate's desktop apps under its version for Publish to promote",
        "echo 'DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer'": "selects the installed Xcode for macOS CI",
        "python3 -I scripts/release_candidate.py": "records release source",
        "python3 -I scripts/coverage_floor.py": "judges this run's coverage floor, standard library only",
        "python3 -I scripts/sonar_ratings.py": "judges Sonar's overall-code ratings, standard library only",
        "echo 'selected=true'": "selects complete CI directly without an external plan",
        'if [ "$GITHUB_REPOSITORY" != helmut-hoffer-von-ankershoffen/heinzel ]': (
            "restricts scheduled maintenance to canonical main"
        ),
        "uv run --no-build --frozen --script scripts/focused_coverage.py": "runs and measures reviewed test selectors",
        "required=(admission changes lint": "requires every owning focused job",
        "sudo sed -i ": "uses the primary signed Ubuntu archive for focused browser runtime setup",
        "uv run --frozen --no-sync --no-build python scripts/ci_node.py": (
            "exports the already installed locked runtime through GitHub's step-local GITHUB_PATH setup contract"
        ),
        "sudo sed -i 's|http://azure\\.archive\\.ubuntu\\.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' "
        "/etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources": (
            "selects the same signed primary Ubuntu archive as Docs before browser dependency installation"
        ),
        "uv run --no-build --script scripts/main_batch.py": "read-only bounded admission of an explicit main batch",
        "echo 'The validated aggregate base": "records that the final run consumed the aggregate base",
        'echo "Integration CI is recorded': "links the intermediate admission to its exact final run",
        "uv run --no-project --frozen --no-build --python 3.15 python scripts/check_confidentiality.py --require": (
            "the secret must be configured in this repo"
        ),
        "unzip -l ": "asserts what the built wheel contains",
        "bash scripts/bootstrap-pyyaml.sh --wheel-dir": (
            "builds the reviewed PyYAML wheel the plugin tooling finds through UV_FIND_LINKS; installs nothing"
        ),
        'changed="$(git diff --name-only': "decides from the changed paths whether a pull request builds the macOS app",
        "if compgen -G ": "selects the built core candidate or an independently released artifact for source plugins",
        "jq -r 'to_entries[]": "the CI OK gate names each failed job with its verdict, then aggregates the results",
        "uv run --no-build coverage report --format=total": "hands the measured total to the Coverage floor job",
        "uv run --no-build coverage report --sort=-miss": "names the least covered files for the Coverage floor",
        "tar -czf notifier.tar.gz": "packs the app the macOS job built, with its executable bit, for the build job",
        "tar -xzf notifier.tar.gz": "unpacks Heinzel Notifier into the package before the wheel is built",
        'gh run cancel "$RUN_ID"': "a failed cheap check cancels the rest of its own pull request run",
        'case "$EVENT:${LANE:-all}" in': "selects the Linux push lane or the coalesced macOS main lane",
        'gh workflow run ci.yml --repo "$GITHUB_REPOSITORY" --ref main -f lane=macos': (
            "queues the coalesced macOS main run; its concurrency group replaces an older pending one"
        ),
    },
    "focused-job.yml": {
        '[[ "$FOCUSED_REF" == "$GITHUB_REF_NAME" ]]': "refuses a ref input other than the dispatched branch",
        "uv run --no-build --quiet --script scripts/focused_job.py": "validates and runs one dispatched test selection",
    },
    "acceptance-sharded.yml": {
        "uv python install 3.15": "the isolated acceptance study interpreter",
        '"$(uv python find 3.15)" -I scripts/acceptance/': "runs or reduces the isolated acceptance study",
    },
    "acceptance-scenario.yml": {
        "uv python install 3.15": "the interpreter the scenario runs on",
        '"$(uv python find 3.15)" -I scripts/acceptance/': "runs the advisory upgrade scenario on a hosted runner",
        'echo "::warning title=Advisory scenario failed::': "reports an advisory failure without a red check",
    },
    "acceptance.yml": {
        "uv python install 3.15": "the interpreter the harness runs on",
        '"$(uv python find 3.15)" -I scripts/acceptance/': (
            "installs the published core with the public commands on a hosted runner and writes the receipt"
        ),
    },
    "docs.yml": {
        "sudo sed -i ": "selects the primary signed Ubuntu archive before browser dependency installation",
        "sudo apt-get update": "fonts for the terminal recordings, from the runner's packages",
        "uv run --quiet --frozen --no-build --no-install-package pyyaml --group docs playwright install-deps": (
            "Chromium's system libraries, from the runner"
        ),
    },
    "publish.yml": {
        'node desktop/ci/release-macos.mjs "$TAG"': "archives the built app and emits its release identity",
        "uv run --no-project --frozen --no-build --python 3.15 python -I scripts/release_apps.py": (
            "promotes the release candidate's macOS apps by sha256 and tree instead of building them again"
        ),
        'test "v$(uv version --short)" = "$TAG"': "refuses a tag that doesn't match the project version",
        '[[ "$TAG" =~': "binds the exact core tag, checkout, caller source and main ancestry before tooling",
        "uv run --no-project --frozen --no-build --python 3.15 python scripts/pypi_release.py checkout": (
            "verifies the reviewed release source before producing its signed artifact manifest"
        ),
        "uv run --no-project --frozen --no-build --python 3.15 python -I scripts/acceptance/run_acceptance.py "
        "--check-foreground-ui": "checks the Core version contract without running acceptance",
        'if [ "$GITHUB_REPOSITORY" = helmut-hoffer-von-ankershoffen/heinzel ]; then': (
            "requires core's configured confidential terms while preserving the shared plugin publisher"
        ),
        'name="$(uv version': "exports the release's CycloneDX bill of materials",
        "uv run --no-project --frozen --no-build --python 3.15 python scripts/release_notes.py": (
            "writes the release notes"
        ),
        'cp "$BUNDLE"': "attaches the provenance attestation and creates the GitHub release",
        'gh api --method POST "repos/$GITHUB_REPOSITORY/dispatches" -f event_type=core-release-published': (
            "requests a separate Pages build using the producer's existing repository token"
        ),
        "tar -czf notifier.tar.gz": "packs the app the macOS job built, with its executable bit, for the build job",
        "tar -xzf notifier.tar.gz": "unpacks Heinzel Notifier into the package before the wheel is built",
        "unzip -l ": "asserts that the released wheel ships Heinzel Notifier",
    },
    "edge.yml": {
        'commit="$(git rev-parse HEAD)"': "plans the edge release from the commit and the releases so far",
        "tar -czf notifier.tar.gz": "preserves the notifier executable bits and signature across runners",
        "tar -xzf notifier.tar.gz": "restores the notifier built without publishing credentials",
        "uv run --no-build --frozen --script scripts/check_confidentiality.py": "scans the tree with the term list",
        "uv run --no-build --frozen --script scripts/release_channel.py": "stamps and describes the edge release",
        "uv build --package heinzel": "builds only the core project",
        'test -f "dist/heinzel-': "asserts the wheel's version and payload",
        "uv export ": "exports the release's CycloneDX bill of materials",
        'cliff="$(sed': "writes the release notes and the channel pointer with the locked git-cliff",
        'cp "$BUNDLE"': "attaches the provenance attestation and creates the pre-release",
        "gh release view edge-channel": "moves the pointer the opt-in path reads",
    },
    "pypi.yml": {
        '[[ "$TAG" =~': "binds publishing to the exact tag and main ancestry before verification tooling",
        "uv run --no-project --frozen --no-build --python 3.15 python scripts/check_confidentiality.py --require": (
            "requires the configured release term list"
        ),
        'version="${TAG#v}"\ngh release download "$TAG" --repo "$GITHUB_REPOSITORY" --dir release-artifacts': (
            "verifies the original GitHub distributions and their producer, provenance, hashes and complete payloads"
        ),
        "python3 - <<'PY'": "rechecks hashes without checking out or running project code in the OIDC job",
    },
    "release.yml": {
        'test "$REF" = refs/heads/main': "restores an original plugin artifact set with the pinned toolkit",
        'test "$PUBLISH" != true || test "$REF" = refs/heads/main': (
            "runs the owning Make commands' same toolkit checks, build, audit and acceptance without core work"
        ),
        'uv run --no-project --frozen --no-build --python 3.15 python scripts/plugin_tooling.py --toolkit "$TOOLING" '
        "python -m heinzel_plugin_tools.publishing": (
            "publishes the original verified plugin bytes through its protected generic host workflow"
        ),
    },
    "prepare-release.yml": {
        'if [[ ! "$INTRO"': "rejects blank dispatch input before checkout or release work",
        "git config user.name": "opens the release pull request as the bot and hands its tag to the next step",
        "gh workflow run ci.yml": "starts CI on the release pull request, which the bot's token doesn't",
    },
    "source-review.yml": {
        ': "source-review dispatch authority"': "refuses any other ref, actor or commit, before checkout",
        "uv run --no-build --frozen --script scripts/source_review.py": "the guard and the tool-free review",
        ': "source-review final identity"': "rechecks the pull request and the protected ref on a fresh runner",
    },
    "release-tag.yml": {'tag="v$(uv version --short)"': "tags a merged release commit"},
    "claude-review.yml": {
        "gate_paths='^(": "finds the gate paths the pull request touches; no tokens when there are none",
        'auth="$(printf': "fetches the head as git objects only, never checks it out",
        'if [[ "$OUTCOME" != success': "collects the model result; a review that did not run fails",
        'if [[ "$MODE" == pass ]]': "no model call without gate paths; a gate change nobody reviewed fails",
    },
    "update-prs.yml": {
        "gh pr list": "updates auto-merging pull requests with main",
        "threads='query(": "resolves the review threads of closed code-scanning alerts on auto-merging pull requests",
    },
}


def commands(workflow: Path) -> list[str]:
    """The `run:` commands of a workflow's steps, without leading comment lines (such as shellcheck directives)."""
    spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    return [
        _without_leading_comments(step["run"])
        for job in spec["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def _without_leading_comments(script: str) -> str:
    lines = script.strip().splitlines()
    while lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines).strip()


def not_through_make(workflow: Path) -> list[str]:
    """Commands that should be a make target: neither `make ...` nor exempt for this workflow."""
    exempt = (*NOT_MAKE["*"], *NOT_MAKE.get(workflow.name, {}))
    return [run for run in commands(workflow) if not run.startswith(("make ", *exempt))]


def test_finds_commands_not_run_through_make(tmp_path):
    workflow = tmp_path / "publish.yml"
    unreviewed = "uv run --no-project --frozen --no-build --python 3.15 python scripts/unreviewed.py"
    steps = [
        {"run": "make build"},
        {"run": "uv sync"},
        {"run": "bash scripts/check_licenses.sh"},
        {"run": unreviewed},
    ]
    workflow.write_text(yaml.safe_dump({"jobs": {"publish": {"steps": [{"uses": "x"}, *steps]}}}), encoding="utf-8")
    assert not_through_make(workflow) == ["bash scripts/check_licenses.sh", unreviewed]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_run_checks_through_make(workflow):
    """Locally and in CI the same make targets run, so a green `make check` means a green CI and a good release."""
    assert not_through_make(workflow) == [], "run these through a make target, or add them to NOT_MAKE with a reason"


def test_every_main_commit_and_release_pr_run_completes_while_other_pull_requests_cancel_superseded_runs():
    concurrency = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["concurrency"]

    def group(ref: str, sha: str) -> str:
        # Evaluate the one expression shape the group uses: `<cond> && github.sha || github.ref`.
        expr = concurrency["group"]
        assert "github.ref == 'refs/heads/main' && github.sha || github.ref" in expr
        return f"{ref}:{sha}" if ref == "refs/heads/main" else ref

    assert group("refs/heads/main", "a") != group("refs/heads/main", "b")
    assert group("refs/pull/1/merge", "a") == group("refs/pull/1/merge", "b")
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' && !startsWith(github.head_ref, 'release/') }}"
    )


def test_source_plugin_audits_receive_authoritative_repository_visibility():
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    assert workflow["jobs"]["local-plugin-check"]["env"]["PLUGIN_PRIVATE"] == "${{ github.event.repository.private }}"


def test_managed_backend_verification_gates_ci_without_leaking_into_plugin_templates():
    """The source-only native owner is checked by required CI; plugins never install its private Node toolchain."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert "lint" in jobs["ci-ok"]["needs"]
    runs = {step["run"]: step.get("if") for step in jobs["lint"]["steps"] if "run" in step}
    assert list(runs).index("make managed-backend-setup") < list(runs).index("make typecheck")
    assert runs["make managed-backend-static"] == "needs.changes.outputs.full_ci != 'true'"
    assert runs["make managed-backend-check"] == "needs.changes.outputs.full_ci == 'true'"
    for name in ("Makefile", ".github/workflows/ci.yml.jinja"):
        assert "managed-backend" not in (REPO / "template" / name).read_text()


def test_core_lint_does_not_export_an_empty_github_token_to_zizmor():
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["lint"]
    [lint] = [step for step in job["steps"] if step.get("run") == "make lint"]
    for scope in (workflow, job, lint):
        assert {"GH_TOKEN", "GITHUB_TOKEN"}.isdisjoint(scope.get("env", {}))


@pytest.mark.parametrize(("event", "batch_base"), [("pull_request", False), ("push", False), ("push", True)])
@pytest.mark.parametrize("path", ["plugins/heinzel-unforeseen/src/plugin.py", "src/heinzel/kernel/public.py"])
def test_plugin_only_changes_skip_core_work_and_keep_required_plugin_checks(
    tmp_path, monkeypatch, event, path, batch_base
):
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    foreign_index = tmp_path / "foreign-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign_index))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, env=environment, capture_output=True, text=True, check=True, timeout=30
        )

    git("init", "--quiet")
    (tmp_path / "README.md").write_text("fixture")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--quiet", "-m", "chore: init")
    base = git("rev-parse", "HEAD").stdout.strip()
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text("# changed")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--quiet", "-m", "feat: change")
    output = tmp_path / "outputs"
    script = jobs["changes"]["steps"][-1]["run"]
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env={
            **environment,
            "BASE": base,
            "BATCH_BASE": base if batch_base else "",
            "EVENT": event,
            "GITHUB_OUTPUT": str(output),
        },
        check=True,
        timeout=30,
    )
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert not foreign_index.exists()
    plugin_only = path.startswith("plugins/")
    complete = batch_base or event != "pull_request" or not plugin_only  # CI OK demands every job outside a PR
    assert values["core"] == str(complete).lower()
    assert values["plugins"] == str(plugin_only).lower()
    assert values["contracts"] == str(complete).lower()
    if plugin_only and not complete:
        assert values["notifier"] == values["electron"] == "false"
    for name in ("lint", "setup", "audit", "test", "coverage", "perf", "e2e", "build", "core-wheel"):
        assert "needs.changes.outputs.core == 'true'" in jobs[name]["if"]
    assert "local-plugin-check" in jobs["ci-ok"]["needs"]
    assert "needs.changes.outputs.plugins == 'true'" in jobs["local-plugin-plan"]["if"]


def test_plugin_compatibility_does_not_wait_for_core_native_packaging_or_test_matrix():
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]
    assert jobs["core-wheel"]["needs"] == "changes"
    assert jobs["consumer-plan"]["needs"] == ["changes", "core-wheel"]
    assert "build" not in jobs["local-plugin-check"]["needs"]
    assert {"build", "notifier", "core-wheel", "consumer-check"} <= set(jobs["ci-ok"]["needs"])
    upload = next(
        step for step in jobs["core-wheel"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact")
    )
    assert upload["with"]["name"] == "plugin-core"


def test_electron_sandbox_archive_hash_mismatch_is_rejected():
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        electron_linux_sandbox.verified_helper(b"tampered", "45.0.0")


def test_electron_sandbox_extracts_only_regular_helper_from_verified_archive(monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as release:
        helper = zipfile.ZipInfo("chrome-sandbox")
        helper.external_attr = (stat.S_IFREG | 0o755) << 16
        release.writestr(helper, b"helper")
        release.writestr("electron", b"unrelated binary")
    data = archive.getvalue()
    monkeypatch.setitem(electron_linux_sandbox.ARCHIVE_SHA256, "test", hashlib.sha256(data).hexdigest())
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"helper").hexdigest())

    assert electron_linux_sandbox.verified_helper(data, "test") == b"helper"

    with pytest.raises(ValueError, match="no pinned"):
        electron_linux_sandbox.verified_helper(data, "46.0.0")


def test_electron_sandbox_install_creates_only_verified_helper(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as release:
        helper = zipfile.ZipInfo("chrome-sandbox")
        helper.external_attr = (stat.S_IFREG | 0o755) << 16
        release.writestr(helper, b"helper")
    data = archive.getvalue()
    monkeypatch.setitem(electron_linux_sandbox.ARCHIVE_SHA256, "test", hashlib.sha256(data).hexdigest())
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"helper").hexdigest())
    electron_root = tmp_path / "electron"
    electron_root.mkdir()
    (electron_root / "dist").mkdir()
    (electron_root / "package.json").write_text('{"version":"test"}', encoding="utf-8")

    installed = electron_linux_sandbox.install_helper(
        electron_root, open_url=lambda *_args, **_kwargs: io.BytesIO(data)
    )

    assert installed == electron_root / "dist" / "chrome-sandbox"
    assert installed.read_bytes() == b"helper"


def test_electron_sandbox_install_does_not_follow_stale_temp_symlink(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as release:
        helper = zipfile.ZipInfo("chrome-sandbox")
        helper.external_attr = (stat.S_IFREG | 0o755) << 16
        release.writestr(helper, b"helper")
    data = archive.getvalue()
    monkeypatch.setitem(electron_linux_sandbox.ARCHIVE_SHA256, "test", hashlib.sha256(data).hexdigest())
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"helper").hexdigest())
    electron_root = tmp_path / "electron"
    dist = electron_root / "dist"
    dist.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"untouched")
    (dist / "chrome-sandbox.tmp").symlink_to(unrelated)
    (electron_root / "package.json").write_text('{"version":"test"}', encoding="utf-8")

    installed = electron_linux_sandbox.install_helper(
        electron_root, open_url=lambda *_args, **_kwargs: io.BytesIO(data)
    )

    assert installed.read_bytes() == b"helper"
    assert unrelated.read_bytes() == b"untouched"


def test_electron_sandbox_install_rejects_symlink_destination(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as release:
        helper = zipfile.ZipInfo("chrome-sandbox")
        helper.external_attr = (stat.S_IFREG | 0o755) << 16
        release.writestr(helper, b"helper")
    data = archive.getvalue()
    monkeypatch.setitem(electron_linux_sandbox.ARCHIVE_SHA256, "test", hashlib.sha256(data).hexdigest())
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"helper").hexdigest())
    electron_root = tmp_path / "electron"
    dist = electron_root / "dist"
    dist.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"untouched")
    (dist / "chrome-sandbox").symlink_to(unrelated)
    (electron_root / "package.json").write_text('{"version":"test"}', encoding="utf-8")

    with pytest.raises(ValueError, match="symlinked"):
        electron_linux_sandbox.install_helper(electron_root, open_url=lambda *_args, **_kwargs: io.BytesIO(data))

    assert unrelated.read_bytes() == b"untouched"


def test_electron_sandbox_install_bounds_archive_read(tmp_path, monkeypatch):
    monkeypatch.setattr(electron_linux_sandbox, "MAX_ARCHIVE_BYTES", 16)
    electron_root = tmp_path / "electron"
    electron_root.mkdir()
    (electron_root / "package.json").write_text('{"version":"45.0.0"}', encoding="utf-8")
    oversized = io.BytesIO(b"x" * 17)

    with pytest.raises(ValueError, match="size limit"):
        electron_linux_sandbox.install_helper(electron_root, open_url=lambda *_args, **_kwargs: oversized)


def test_electron_sandbox_permissions_use_nofollow_descriptor_operations(tmp_path, monkeypatch):
    electron_root = tmp_path / "electron"
    dist = electron_root / "dist"
    dist.mkdir(parents=True)
    source = dist / "chrome-sandbox"
    source.write_bytes(b"helper")
    source_inode = source.stat().st_ino
    state = {"stage_dir": None, "stage_file": None, "owner": 1000, "group": 1000, "mode": 0o600}
    original_open = os.open
    original_fstat = os.fstat

    def open_file(path, flags, mode=0o600, *, dir_fd=None):
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(".heinzel-electron-sandbox-"):
            state["stage_dir"] = descriptor
        elif path == "chrome-sandbox" and dir_fd == state["stage_dir"]:
            state["stage_file"] = descriptor
        return descriptor

    def fake_fstat(descriptor):
        if descriptor == state["stage_dir"]:
            result = original_fstat(descriptor)
            return SimpleNamespace(
                st_uid=0,
                st_mode=stat.S_IFDIR | 0o700,
                st_dev=result.st_dev,
                st_ino=result.st_ino,
            )
        if descriptor == state["stage_file"]:
            return SimpleNamespace(
                st_uid=state["owner"],
                st_gid=state["group"],
                st_mode=stat.S_IFREG | state["mode"],
                st_size=6,
                st_nlink=1,
                st_ino=source_inode + 100,
            )
        return original_fstat(descriptor)

    def set_owner(descriptor, uid, gid):
        assert descriptor == state["stage_file"]
        assert fake_fstat(descriptor).st_ino != source_inode
        state["owner"], state["group"] = uid, gid

    def set_mode(descriptor, mode):
        assert descriptor == state["stage_file"]
        assert fake_fstat(descriptor).st_ino != source_inode
        state["mode"] = mode

    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"helper").hexdigest())
    monkeypatch.setattr(electron_linux_sandbox.os, "open", open_file)
    monkeypatch.setattr(electron_linux_sandbox.os, "fstat", fake_fstat)
    monkeypatch.setattr(electron_linux_sandbox.os, "fchown", set_owner)
    monkeypatch.setattr(electron_linux_sandbox.os, "fchmod", set_mode)

    electron_linux_sandbox.configure_helper_permissions(electron_root, "test")

    installed = source.stat()
    assert installed.st_ino != source_inode
    assert (state["owner"], state["group"], state["mode"]) == (0, 0, 0o4755)
    assert source.read_bytes() == b"helper"


def test_electron_sandbox_permissions_reject_symlink_parent(tmp_path, monkeypatch):
    electron_root = tmp_path / "electron"
    electron_root.mkdir()
    outside = tmp_path / "outside"
    (outside / "dist").mkdir(parents=True)
    target = outside / "dist" / "chrome-sandbox"
    target.write_bytes(b"untouched")
    (electron_root / "dist").symlink_to(outside / "dist", target_is_directory=True)
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"untouched").hexdigest())

    with pytest.raises(OSError, match=r"(symbolic link|Not a directory)"):
        electron_linux_sandbox.configure_helper_permissions(electron_root, "test")

    assert target.read_bytes() == b"untouched"


def test_electron_sandbox_permissions_reject_symlink_helper(tmp_path, monkeypatch):
    electron_root = tmp_path / "electron"
    dist = electron_root / "dist"
    dist.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_bytes(b"untouched")
    (dist / "chrome-sandbox").symlink_to(target)
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"untouched").hexdigest())

    with pytest.raises(OSError, match=r"(symbolic link|Not a directory)"):
        electron_linux_sandbox.configure_helper_permissions(electron_root, "test")

    assert target.read_bytes() == b"untouched"


def test_electron_sandbox_permissions_reject_replaced_regular_file(tmp_path, monkeypatch):
    electron_root = tmp_path / "electron"
    dist = electron_root / "dist"
    dist.mkdir(parents=True)
    helper = dist / "chrome-sandbox"
    helper.write_bytes(b"attacker")
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"trusted").hexdigest())
    fchown_called = False

    def fchown(*_args):
        nonlocal fchown_called
        fchown_called = True

    monkeypatch.setattr(electron_linux_sandbox.os, "fchown", fchown)

    with pytest.raises(ValueError, match="does not match"):
        electron_linux_sandbox.configure_helper_permissions(electron_root, "test")

    assert not fchown_called


def test_electron_sandbox_permissions_never_changes_source_after_hard_link_race(tmp_path, monkeypatch):
    electron_root = tmp_path / "electron"
    dist = electron_root / "dist"
    dist.mkdir(parents=True)
    helper = dist / "chrome-sandbox"
    helper.write_bytes(b"trusted")
    second_link = tmp_path / "linked-helper"
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"trusted").hexdigest())
    metadata_before = helper.stat()
    metadata_targets = []

    # Fake only the metadata result of privileged operations; the descriptor's
    # inode remains the newly staged inode, separate from the extracted source.
    original_fstat = os.fstat
    staged = set()
    stage_fd = {"dir": None}
    linked_after_stat = False

    def fake_fstat(descriptor):
        nonlocal linked_after_stat
        result = original_fstat(descriptor)
        if not linked_after_stat:
            # Model a same-UID attacker adding a hard link immediately after
            # validation of the extracted source descriptor.
            second_link.hardlink_to(helper)
            linked_after_stat = True
        if descriptor == stage_fd["dir"]:
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o700, st_dev=result.st_dev, st_ino=result.st_ino)
        if descriptor in staged:
            return SimpleNamespace(
                st_uid=0,
                st_gid=0,
                st_mode=stat.S_IFREG | 0o4755,
                st_size=result.st_size,
                st_nlink=result.st_nlink,
                st_ino=result.st_ino,
            )
        return result

    def fchown(descriptor, *_args):
        metadata_targets.append(fake_fstat(descriptor).st_ino)

    def fchmod(descriptor, *_args):
        metadata_targets.append(fake_fstat(descriptor).st_ino)

    monkeypatch.setattr(electron_linux_sandbox.os, "fchown", fchown)
    monkeypatch.setattr(electron_linux_sandbox.os, "fchmod", fchmod)

    original_open = os.open

    def open_file(path, flags, mode=0o600, *, dir_fd=None):
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(".heinzel-electron-sandbox-"):
            stage_fd["dir"] = descriptor
        elif path == "chrome-sandbox" and dir_fd == stage_fd["dir"]:
            staged.add(descriptor)
        return descriptor

    monkeypatch.setattr(electron_linux_sandbox.os, "open", open_file)
    monkeypatch.setattr(electron_linux_sandbox.os, "fstat", fake_fstat)

    electron_linux_sandbox.configure_helper_permissions(electron_root, "test")

    metadata_after = second_link.stat()
    assert (metadata_after.st_uid, metadata_after.st_gid, metadata_after.st_mode) == (
        metadata_before.st_uid,
        metadata_before.st_gid,
        metadata_before.st_mode,
    )
    assert helper.stat().st_nlink == second_link.stat().st_nlink == 1
    assert second_link.stat().st_ino == metadata_before.st_ino
    assert len(metadata_targets) == 2
    assert all(inode != metadata_before.st_ino for inode in metadata_targets)


def test_electron_sandbox_permissions_cleans_up_staging_dir_if_open_fails(tmp_path, monkeypatch):
    electron_root = tmp_path / "electron"
    dist = electron_root / "dist"
    dist.mkdir(parents=True)
    (dist / "chrome-sandbox").write_bytes(b"trusted")
    monkeypatch.setitem(electron_linux_sandbox.HELPER_SHA256, "test", hashlib.sha256(b"trusted").hexdigest())
    original_open = os.open

    def fail_open_after_mkdir(path, flags, mode=0o600, *, dir_fd=None):
        if isinstance(path, str) and path.startswith(".heinzel-electron-sandbox-"):
            message = "simulated staging directory open failure"
            raise OSError(message)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(electron_linux_sandbox.os, "open", fail_open_after_mkdir)

    with pytest.raises(OSError, match="simulated staging directory open failure"):
        electron_linux_sandbox.configure_helper_permissions(electron_root, "test")

    assert list(dist.glob(".heinzel-electron-sandbox-*")) == []


def test_electron_sandbox_ci_restores_and_verifies_setuid_helper():
    makefile = (REPO / "Makefile").read_text()
    target = next(line for line in makefile.splitlines() if "python3 desktop/ci/restore_linux_sandbox.py" in line)
    electron_test = makefile.split("desktop-electron-test:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert electron_test.index("pnpm --dir desktop exec node -p 'require(\"electron\")'") < electron_test.index(
        "pnpm --dir desktop test"
    )
    assert 'if [ ! -f "$$packaged_sandbox" ] || [ -L "$$packaged_sandbox" ]' in makefile
    assert '--configure-file "$$packaged_sandbox"' in makefile
    assert "desktop/dist/Heinzelmaennchen-linux-$$(node -p 'process.arch')/chrome-sandbox" in makefile
    native_target = makefile.split("desktop-electron-native:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert 'HEINZEL_ELECTRON_EXECUTABLE="$$executable"' in native_target
    assert "desktop/dist/Heinzelmaennchen-$$target" in native_target
    sandbox_script = (REPO / "desktop/ci/restore_linux_sandbox.py").read_text()
    assert "os.fchown(descriptor, 0, 0)" in sandbox_script
    assert "os.fchmod(descriptor, 0o4755)" in sandbox_script
    assert "stat -c '%u:%a'" in makefile
    assert "$$packaged_sandbox" in target
    assert "$$electron_root" not in makefile
    package_lock = (REPO / "desktop/pnpm-lock.yaml").read_text()
    assert "@electron-internal/extract-zip@1.0.6" in package_lock
    assert "extract-zip@2.0.1" not in package_lock


def test_every_not_make_exemption_is_used():
    runs = {workflow.name: commands(workflow) for workflow in WORKFLOWS}
    runs["*"] = [run for each in runs.values() for run in each]
    stale = [
        f"{name}: {start}"
        for name, exempt in NOT_MAKE.items()
        for start in exempt
        if not any(run.startswith(start) for run in runs.get(name, []))
    ]
    assert stale == [], "remove stale NOT_MAKE entries"


def test_release_docs_follow_the_successful_producer_without_recording_or_delaying_it():
    release = yaml.safe_load((REPO / ".github/workflows/release-tag.yml").read_text(encoding="utf-8"))
    docs = yaml.safe_load((REPO / ".github/workflows/docs.yml").read_text(encoding="utf-8"))
    assert set(release["jobs"]) == {"tag", "publish"}
    assert docs[True]["repository_dispatch"] == {"types": ["core-release-published"]}
    publish = yaml.safe_load((REPO / ".github/workflows/publish.yml").read_text(encoding="utf-8"))
    release_step, dispatch = publish["jobs"]["publish"]["steps"][-2:]
    assert 'gh release create "$TAG"' in release_step["run"]
    assert dispatch["timeout-minutes"] == 1
    assert dispatch["if"] == "github.repository == 'helmut-hoffer-von-ankershoffen/heinzel'"
    assert (
        dispatch["run"]
        == 'gh api --method POST "repos/$GITHUB_REPOSITORY/dispatches" -f event_type=core-release-published'
    )
    site = docs["jobs"]["site"]
    assert " ".join(site["if"].split()) == (
        "github.event_name != 'repository_dispatch' || "
        "(github.repository == 'helmut-hoffer-von-ankershoffen/heinzel' && "
        "github.ref == 'refs/heads/main' && "
        "github.event.sender.login == 'github-actions[bot]')"
    )
    assert site["steps"][0]["with"]["ref"] == "${{ github.event_name == 'pull_request' && github.sha || 'main' }}"
    assert docs["jobs"]["pages"]["needs"] == "site"
    assert docs[True]["workflow_call"]["inputs"]["site_only"]["default"] is False
    assert docs["jobs"]["record"]["if"] == (
        "inputs.site_only != true && inputs.record == true && github.event_name != 'pull_request'"
    )


# A script named by path needs its executable bit, which copier drops when it follows the template's symlinks into a
# plugin's scripts/. Through an interpreter, a script runs without it.
SCRIPT = re.compile(r"(?<![\w/.-])(?:\./)?scripts/[\w.-]+\.(?:sh|py)\b")
INTERPRETER = re.compile(r"(?:\bbash|\bpython3?(?: -I)?|\buv run(?: -[\w-]+)*(?: --script| python3?))\s+$")


def scripts_run_by_path(run: str) -> list[str]:
    """The scripts a `run:` command executes by path instead of through `bash` or `python`."""
    found = []
    for line in run.splitlines():
        code = line.split(" #", 1)[0]
        found += [m.group() for m in SCRIPT.finditer(code) if not INTERPRETER.search(code[: m.start()])]
    return found


def test_finds_scripts_run_by_path():
    assert scripts_run_by_path('tag="$(scripts/release.sh "$BUMP" | tail -n 1)"') == ["scripts/release.sh"]
    assert scripts_run_by_path("./scripts/x.py --check") == ["./scripts/x.py"]
    assert scripts_run_by_path("sh scripts/x.sh") == ["scripts/x.sh"]
    assert scripts_run_by_path("bash scripts/x.sh && python scripts/y.py") == []
    assert scripts_run_by_path("uv run --frozen python scripts/x.py > notes.md") == []
    assert scripts_run_by_path("uv run --quiet --script scripts/x.py --json") == []
    assert scripts_run_by_path("make check  # see scripts/steps.py") == []


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_run_scripts_through_an_interpreter(workflow):
    """Plugins get these workflows (symlinked, or ci.yml generated by sync_template.py) and their scripts without the
    executable bit, so `scripts/release.sh` fails there with Permission denied; `bash scripts/release.sh` works."""
    by_path = [script for run in commands(workflow) for script in scripts_run_by_path(run)]
    assert by_path == [], "run scripts as `bash scripts/x.sh` or `uv run --script scripts/x.py` (scripts_test.py)"


def test_make_runs_scripts_through_an_interpreter():
    """`make pr` and `make release` are how people run pr.sh and release.sh, in plugins too."""
    recipes = [line for line in (REPO / "Makefile").read_text(encoding="utf-8").splitlines() if line.startswith("\t")]
    assert [script for line in recipes for script in scripts_run_by_path(line)] == []


COMMITS = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["commitizen"]


def conventional(message: str) -> bool:
    """What `cz check` decides (commit-msg hook, `make commits`, CI): an allowed prefix, or the schema_pattern."""
    allowed = message.startswith(tuple(COMMITS["allowed_prefixes"]))
    return allowed or re.match(COMMITS["customize"]["schema_pattern"], message) is not None


@pytest.mark.parametrize(
    "message",
    [
        "feat(cli): add --json to every operation",
        "fix: exit with code 1 on errors",
        "feat(security)!: refuse plugins from unknown hosts",
        "chore(release): v0.2.0",
        "docs(adr): record the public API (#41)",
        "build: bump the dependencies group across 1 directory with 12 updates (#77)",  # Dependabot
        "Merge branch 'main' into feat/x",
        "fix(cli): " + "a" * 62,  # 72 characters
        "feat(kernel): " + "a" * 58 + " (#123)",  # 72 plus what the squash merge appends
        "refactor(heads): share the exposure rules\n\nThe MCP and REST heads had drifted.\n\nRefs: #7",
    ],
)
def test_commit_grammar_accepts(message):
    assert conventional(message)


@pytest.mark.parametrize(
    "message",
    [
        "feat(standards): not a scope",
        "feature: not a type",
        "fix(cli):no space",
        "fix(cli): ",
        "Fix(cli): capitalised type",
        "fix(cli): " + "a" * 63,  # 73 characters
        "feat(kernel): " + "a" * 59 + " (#123)",
        "fix: summary\nbody without a blank line",
    ],
)
def test_commit_grammar_rejects(message):
    assert not conventional(message)


def test_documented_commit_scopes_are_the_enforced_ones():
    standards = (REPO / "docs" / "CODING_STANDARDS.md").read_text(encoding="utf-8")
    documented = re.search(r"Heinzelmännchen's are (.*?)\. A plugin", standards, re.DOTALL)
    enforced = re.search(r"\\\(\(([a-z|-]+)\)\\\)", COMMITS["customize"]["schema_pattern"])
    assert documented
    assert enforced
    assert re.findall(r"`([a-z-]+)`", documented[1]) == enforced[1].split("|")


def jobs_without_timeout(workflow: dict) -> list[str]:
    """Jobs that could run for GitHub's default six hours; a job calling a reusable workflow inherits its timeouts."""
    return [
        name for name, job in workflow.get("jobs", {}).items() if "uses" not in job and "timeout-minutes" not in job
    ]


def test_finds_jobs_without_timeout():
    workflow = {"jobs": {"a": {"runs-on": "x"}, "b": {"runs-on": "x", "timeout-minutes": 5}, "c": {"uses": "./w.yml"}}}
    assert jobs_without_timeout(workflow) == ["a"]


@pytest.mark.parametrize("workflow", sorted((REPO / ".github" / "workflows").glob("*.yml")), ids=lambda p: p.name)
def test_every_job_has_a_timeout(workflow):
    """A hung job fails fast instead of holding a runner for six hours."""
    assert jobs_without_timeout(yaml.safe_load(workflow.read_text(encoding="utf-8"))) == [], "add timeout-minutes"


def handlers_of_review_threads(root: Path) -> list[str]:
    """Top-level directories of the workflows and scripts that read or resolve review threads (`reviewThreads`)."""
    candidates = [*(root / ".github" / "workflows").glob("*.yml"), *(root / "scripts").glob("*.py")]
    return sorted(
        {p.relative_to(root).parts[0] for p in candidates if "reviewThreads" in p.read_text(encoding="utf-8")}
    )


def test_finds_handlers_of_review_threads(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github" / "workflows" / "a.yml").write_text("run: gh pr list", encoding="utf-8")
    assert handlers_of_review_threads(tmp_path) == []
    (tmp_path / "scripts" / "b.py").write_text("QUERY = 'reviewThreads(first: 100)'", encoding="utf-8")
    assert handlers_of_review_threads(tmp_path) == ["scripts"]


def test_required_review_threads_are_surfaced_and_resolved():
    """main requires resolved review threads, and an unresolved bot thread blocks auto-merge without a word. So a
    workflow resolves the threads of fixed or dismissed code-scanning alerts, and `make ci-logs` names the rest."""
    if "resolved review threads" not in (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8"):
        pytest.skip("main does not require resolved review threads")
    assert handlers_of_review_threads(REPO) == [".github", "scripts"]


def test_codeql_runs_security_queries_only():
    """Ruff, mypy and pyright own code quality; CodeQL's quality queries contradict them (a quoted cast, overload
    stubs, PEP 695 type parameters), and tools that disagree are a configuration bug."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8"))
    inits = [
        step.get("with", {})
        for job in spec["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("github/codeql-action/init@")
    ]
    assert inits
    assert all(init.get("queries", "") in {"", "security-extended"} for init in inits), "use security-extended"


def test_the_release_regenerates_derived_files():
    """A version bump changes files that other files are generated from; the release commit must include both."""
    script = (REPO / "scripts" / "release.sh").read_text(encoding="utf-8")
    bump, fix, commit = (
        script.index(marker) for marker in ("bump-my-version bump", "make --no-print-directory fix", "git commit")
    )
    assert bump < fix < commit


def test_the_release_keeps_changelog_introductions():
    """git-cliff regenerates CHANGELOG.md from commits; the prose written under release headings is put back, the
    new release's from INTRO (the Release workflow's `intro` input), and the pull request shows the merged section."""
    script = (REPO / "scripts" / "release.sh").read_text(encoding="utf-8")
    save, regenerate, restore, commit, notes = (
        script.index(marker)
        for marker in (
            'cp CHANGELOG.md "$work/previous.md"',
            "git-cliff --offline --tag",
            (
                "uv run --frozen --no-sync --no-build python scripts/changelog_intros.py "
                '"$work/previous.md" CHANGELOG.md --version "$next"'
            ),
            "git commit",
            'uv run --frozen --no-sync --no-build python scripts/release_notes.py "$tag"',
        )
    )
    assert save < regenerate < restore < commit < notes
    expand = script.index(
        'uv run --frozen --no-sync --no-build python scripts/changelog_combines.py CHANGELOG.md --version "$next"'
    )
    assert restore < expand < commit, "combine entries become their members' entries before the release commit"
    assert '--intro "${INTRO:-}"' in script
    release = yaml.safe_load((REPO / ".github" / "workflows" / "prepare-release.yml").read_text(encoding="utf-8"))
    assert "intro" in release[True]["workflow_dispatch"]["inputs"]  # YAML 1.1 reads the key `on` as True
    step = next(s for s in release["jobs"]["pull-request"]["steps"] if "release.sh" in s.get("run", ""))
    assert not any("gh pr merge" in s.get("run", "") for s in release["jobs"]["pull-request"]["steps"])
    assert step["env"]["INTRO"] == "${{ inputs.intro }}"
    for name in ("changelog_intros", "changelog_combines", "steps"):  # `.py.jinja`: Sonar must not count a copy
        assert (REPO / "template" / "scripts" / f"{name}.py.jinja").resolve() == REPO / "scripts" / f"{name}.py"


def test_pushing_runs_check_fast_through_a_hook():
    """What CI would catch first (format, lint, contracts, generated files, unit tests) fails before the push."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    assert "exec make check-fast" in hooks["check-fast"]["entry"]
    assert hooks["check-fast"]["stages"] == ["pre-push"]
    assert "pre-push" in config["default_install_hook_types"]
    assert "--hook-type pre-push" in (REPO / "Makefile").read_text(encoding="utf-8")
    assert (REPO / "template" / ".pre-commit-config.yaml.jinja").resolve() == REPO / ".pre-commit-config.yaml"


def test_the_template_audit_runs_in_template_test_ci_and_full_preflight():
    """The rendered plugin's audit takes seconds, so `make template-test` runs it unless `AUDIT=0` (offline).

    `make preflight` stays offline unless `PREFLIGHT=full`, like the audit of Heinzelmännchen's own dependencies.
    """
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "\tTEMPLATE_AUDIT=$(if $(filter 0,$(AUDIT)),,1) bash scripts/template-test.sh" in makefile
    assert "template-test" not in (REPO / "template" / "Makefile").read_text(encoding="utf-8")
    assert "TEMPLATE_AUDIT=$(if $(filter full,$(PREFLIGHT)),1) bash scripts/template-test.sh" in makefile
    assert "make audit" in (REPO / "scripts" / "template-test.sh").read_text(encoding="utf-8")
    script = (REPO / "scripts" / "template-test.sh").read_text(encoding="utf-8")
    assert "[[ ! -e uv.lock ]]" in script
    # a fresh render runs the documented `make setup` unlocked: nothing that locks, makes the venv or builds PyYAML
    # may stand between the assertion and the command (the Makefile's install does all of it)
    between = script.split("[[ ! -e uv.lock ]]", 1)[1].split("\nmake setup\n", 1)[0]
    for step in ("uv lock", "uv venv", "bootstrap-pyyaml", "make pyyaml"):
        assert step not in between, f"template-test must not run `{step}` before make setup"
    ci = "\n".join(commands(REPO / ".github" / "workflows" / "ci.yml"))
    assert "make template-test AUDIT=1" in ci
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert workflow["jobs"]["template"]["timeout-minutes"] == 10


def _gate_review():
    return yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))


def _gate_scope_script():
    return next(step["run"] for step in _gate_review()["jobs"]["scope"]["steps"] if step.get("id") == "scope")


def test_gate_review_always_reports_one_check_and_fails_closed():
    spec = _gate_review()
    job = spec["jobs"]["gate-review"]
    assert spec["name"] == job["name"] == "Gate review"
    assert job["needs"] == ["scope", "model"]
    assert job["if"] == "${{ !cancelled() }}"  # runs whatever the model job did, so nothing hangs pending
    script = job["steps"][0]["run"]
    assert 'if [[ "$MODE" == pass ]]' in script
    assert "needs a human or agent review" in script  # a bot, fork or draft gate change
    assert '"$MODEL" != success' in script  # a failed or skipped model run never passes
    scope = _gate_scope_script()
    assert 'SENDER_TYPE" == Bot' in scope
    assert "mode=pass" in scope
    assert spec["jobs"]["model"]["if"] == "needs.scope.outputs.mode == 'review'"


@pytest.mark.parametrize(
    ("enabled", "mode"), [("false", "paused"), ("true", "review"), ("", "refuse"), ("False", "refuse")]
)
def test_gate_review_operator_switch(tmp_path, enabled, mode):
    jobs = _gate_review()["jobs"]
    scope = next(step for step in jobs["scope"]["steps"] if step.get("id") == "scope")
    assert scope["env"]["ENABLED"] == "${{ vars.CLAUDE_REVIEW }}"
    output = tmp_path / "output"
    environment = dict(os.environ, ENABLED=enabled, GITHUB_OUTPUT=str(output), CHANGED_FILES="1")
    script = 'gh() { echo ".github/workflows/ci.yml"; }\n' + scope["run"]
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], env=environment, check=True, timeout=scaled(5))
    assert output.read_text().strip() == f"mode={mode}"


@pytest.mark.parametrize(
    ("scope", "model", "skipped"),
    [("success", "skipped", True), ("failure", "skipped", False), ("success", "failure", False)],
)
def test_gate_review_records_only_a_successful_operator_pause(tmp_path, scope, model, skipped):
    job = _gate_review()["jobs"]["gate-review"]
    judge = next(step for step in job["steps"] if step.get("name") == "Judge the review and comment")
    assert judge["env"]["SCOPE_RESULT"] == "${{ needs.scope.result }}"
    summary = tmp_path / "summary"
    environment = dict(os.environ, MODE="paused", SCOPE_RESULT=scope, MODEL=model, GITHUB_STEP_SUMMARY=str(summary))
    result = subprocess.run(["bash", "-e", "-c", judge["run"]], env=environment, check=False, timeout=scaled(5))
    assert (result.returncode == 0) is skipped
    assert summary.exists() is skipped
    if skipped:
        assert "Claude review skipped by operator (CLAUDE_REVIEW=false)" in summary.read_text()


def test_gate_review_runs_the_base_workflow_and_never_executes_the_head():
    spec = _gate_review()
    assert set(spec[True]) == {"pull_request_target"}
    model = spec["jobs"]["model"]
    checkout = next(step for step in model["steps"] if "actions/checkout" in step.get("uses", ""))
    assert "ref" not in checkout["with"]  # the base branch
    assert checkout["with"]["persist-credentials"] is False
    fetch = next(step["run"] for step in model["steps"] if step.get("name", "").startswith("Fetch the head"))
    assert "refs/pull/${PR}/head:refs/review/head" in fetch
    for job in ("scope", "gate-review"):
        assert not any("actions/checkout" in step.get("uses", "") for step in spec["jobs"][job]["steps"])
    assert "github.event.pull_request.head.sha" not in str(checkout)


def test_gate_review_fails_closed_beyond_the_files_api_limit():
    scope = _gate_scope_script()
    assert "CHANGED_FILES > 3000" in scope
    assert scope.index("CHANGED_FILES > 3000") < scope.index("mode=pass")  # checked before it can pass


def test_gate_review_fetches_the_head_with_basic_auth():
    """A bearer header is not accepted by git over HTTPS (exit 128: could not read Username)."""
    model = _gate_review()["jobs"]["model"]
    fetch = next(step["run"] for step in model["steps"] if step.get("name", "").startswith("Fetch the head"))
    assert "x-access-token:%s" in fetch
    assert "AUTHORIZATION: basic" in fetch
    assert "bearer" not in fetch.replace("not a bearer header", "")
    assert "::add-mask::" in fetch
    assert "+refs/pull/${PR}/head:refs/review/head" in fetch
    assert 'git rev-parse --verify "refs/review/head^{commit}"' in fetch  # the head must have arrived
    assert "checkout" not in fetch


def test_gate_review_lists_both_sides_of_a_rename():
    assert "previous_filename" in _gate_scope_script()


def test_gate_review_guards_the_reviewers_own_instructions():
    gate_paths = next(line for line in _gate_scope_script().splitlines() if line.startswith("gate_paths="))
    for needle in (
        r"\.claude/",
        r"(CLAUDE|AGENTS)\.md$",
        r"\.github/(workflows/",
        "CODEOWNERS",
        "release_",
        "bootstrap_",
    ):
        assert needle in gate_paths
    claude_args = next(
        step["with"]["claude_args"] for step in _gate_review()["jobs"]["model"]["steps"] if step.get("id") == "review"
    )
    assert "--setting-sources user" in claude_args
    assert """--settings '{"disableAllHooks":true}'""" in claude_args
    assert "--disallowedTools" in claude_args
    for tool in ("git diff", "git log", "git show"):
        assert f"Bash({tool}*--output*)" in claude_args  # no file written by a git option
    assert "--model opus" in claude_args


def test_gate_review_passes_only_on_the_exact_last_line():
    script = _gate_review()["jobs"]["gate-review"]["steps"][0]["run"]
    assert '[[ "$VERDICT_LINE" == "VERDICT: APPROVE" ]]' in script
    collect = next(step["run"] for step in _gate_review()["jobs"]["model"]["steps"] if step.get("id") == "result")
    assert "grep -v '^[[:space:]]*$' <<<\"$result\" | tail -n 1" in collect  # the last non-empty line


def test_gate_review_holds_least_privilege():
    spec = _gate_review()
    for name, job in spec["jobs"].items():
        assert "id-token" not in job["permissions"], name
    assert spec["jobs"]["model"]["permissions"] == {"contents": "read"}  # holds the secrets: no write
    assert spec["jobs"]["gate-review"]["permissions"] == {"pull-requests": "write"}
    assert spec["permissions"] == {}


def test_gate_review_reruns_on_reopened_pull_requests():
    assert "reopened" in _gate_review()[True]["pull_request_target"]["types"]


def test_workflows_are_code_owned():
    assert "/.github/workflows/ @helmut-hoffer-von-ankershoffen" in (REPO / ".github/CODEOWNERS").read_text()


def test_core_keeps_tested_pull_request_heads_while_plugins_keep_branch_updates():
    workflow = yaml.safe_load((REPO / ".github/workflows/update-prs.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["update"]["steps"]
    branch_updates = [step for step in steps if "gh pr update-branch" in step.get("run", "")]

    assert len(branch_updates) == 1
    assert branch_updates[0].get("if") == "github.repository != 'helmut-hoffer-von-ankershoffen/heinzel'"
    assert "select(.autoMergeRequest != null and (.isCrossRepository | not))" in branch_updates[0]["run"]
    assert "actions/runs/$run/approve" in branch_updates[0]["run"]


def test_closed_code_scanning_threads_are_still_resolved_in_core():
    workflow = yaml.safe_load((REPO / ".github/workflows/update-prs.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["update"]["steps"]
    cleanup = [step for step in steps if "resolveReviewThread" in step.get("run", "")]

    assert len(cleanup) == 1
    assert "if" not in cleanup[0]
    assert '[ "$state" != open ] || continue' in cleanup[0]["run"]


def test_template_installed_cli_uses_its_own_daemon_unit_directory(tmp_path, tmp_path_factory, monkeypatch):
    source = REPO / "template/tests/e2e/install_test.py.jinja"
    namespace = runpy.run_path(str(source))
    inherited_units = tmp_path / "user-units"
    monkeypatch.setenv("HZ_SYSTEM_DAEMON__UNIT_DIR", str(inherited_units))
    monkeypatch.setenv("PLUGIN_TEST_WHEEL", str(tmp_path / "plugin.whl"))
    monkeypatch.setattr("shutil.which", lambda _name, **_: "/isolated/uv")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    cli = namespace["hz"].__wrapped__(tmp_path_factory)
    cli("system", "plugin", "remove", "example", "--json")

    assert len(calls) == 3
    root = Path(calls[0][1]["UV_TOOL_DIR"]).parent
    for _command, env in calls:
        units = env.get("HZ_SYSTEM_DAEMON__UNIT_DIR")
        assert units == str(root / "units")
        assert units != str(inherited_units)


def test_claude_review_prefers_the_subscription_token():
    """The review step authenticates with CLAUDE_CODE_OAUTH_TOKEN and uses the API key only without one."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    actions = [step for step in spec["jobs"]["model"]["steps"] if "claude-code-action" in step.get("uses", "")]
    assert len(actions) == 1
    for step in actions:
        assert step["with"]["claude_code_oauth_token"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
        assert step["with"]["anthropic_api_key"].startswith("${{ secrets.CLAUDE_CODE_OAUTH_TOKEN == '' && ")


def _template_sync_module():
    import importlib.util

    path = REPO / "scripts" / "sync_template.py"
    spec = importlib.util.spec_from_file_location("sync_template", path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generated_plugin_ci() -> dict:
    text = _template_sync_module().ci_workflow()
    assert "notifier" not in text
    assert "changes" not in text.replace("upstream changes", "")
    return yaml.safe_load(text.replace("[[ package_name ]]", "x"))


def test_plugin_template_does_not_advertise_core_only_e2e_scheduling_hint():
    generated = _template_sync_module().pyproject()
    checked_in = (REPO / "template" / "pyproject.toml.jinja").read_text(encoding="utf-8")
    assert generated == checked_in
    assert "e2e_early:" not in generated
    assert "e2e_early:" in (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_plugin_template_makefile_excludes_all_electron_only_targets():
    """Electron's install, sandbox and native-smoke targets stay out of generated plugin Makefiles."""
    generated = _template_sync_module().makefile()
    checked_in = (REPO / "template" / "Makefile").read_text(encoding="utf-8")
    assert generated == checked_in
    for target in ("desktop-electron-test", "desktop-electron-sandbox", "desktop-electron-native"):
        assert target not in generated
        assert target not in checked_in
    assert "chrome-sandbox" not in generated
    assert "sudo chown root:root" not in generated


def test_plugin_ci_excludes_the_core_linux_user_manager_probe():
    generated = _template_sync_module().ci_workflow()
    core = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "make linux-user-manager-proof" in core
    assert "linux_user_manager_probe.py" not in generated
    assert "Inspect the Linux user manager" not in generated
    assert "linux-user-manager-proof" not in _template_sync_module().makefile()


def test_plugin_ci_has_no_core_only_jobs_and_every_need_exists():
    jobs = _generated_plugin_ci()["jobs"]
    for name, job in jobs.items():
        needs = job.get("needs", [])
        assert "needs" not in job or needs, f"{name} has an empty needs list"
        for need in [needs] if isinstance(needs, str) else needs:
            assert need in jobs, f"{name} needs {need}"
    assert jobs["setup"]["strategy"]["matrix"]["os"] == ["ubuntu-24.04", "macos-latest"]
    assert "if" not in jobs["build"]
    assert jobs["e2e"]["strategy"]["matrix"]["os"] == ["ubuntu-24.04", "macos-latest"]
    assert '"os":"macos-latest","python":"3.15"' in jobs["test"]["strategy"]["matrix"]["include"]


def _macos_include(matrix: dict, event: str, full: bool, *, release: bool = False, lane: str = "") -> list[dict]:
    expression = re.fullmatch(r"\$\{\{ fromJSON\((.*)\) \}\}", matrix["include"].strip())
    assert expression
    context = {
        "github.event_name": event,
        "needs.changes.outputs.lane": lane or ("linux" if event == "push" else "all"),
        "needs.changes.outputs.macos_full": str(full).lower(),
        "needs.changes.outputs.full_ci": str(release).lower(),
    }
    for branch in expression[1].split(" || "):
        value = None
        for term in branch.split(" && "):
            literal = re.fullmatch(r"'([^']*)'", term)
            if literal:
                value = literal[1]
            else:
                condition = re.fullmatch(r"([a-z_.]+) (==|!=) '([^']*)'", term)
                assert condition, term
                equal = context[condition[1]] == condition[3]
                value = equal if condition[2] == "==" else not equal
            if not value:
                break
        if value:
            return json.loads(value)
    message = "No matrix branch matched"
    raise AssertionError(message)


def _matrix_parallel_limit(job: dict, event: str, ref: str, lane: str) -> int:
    expression = re.fullmatch(r"\$\{\{ (.*) \}\}", job["strategy"]["max-parallel"])
    assert expression
    context = {"github.event_name": event, "github.ref": ref, "inputs.lane": lane}
    for branch in expression[1].split(" || "):
        *conditions, limit = branch.split(" && ")
        matched = True
        for text in conditions:
            condition = re.fullmatch(r"([a-z_.]+) == '([^']*)'", text)
            assert condition, text
            matched &= context[condition[1]] == condition[2]
        if matched:
            return int(limit)
    message = "No parallel-limit branch matched"
    raise AssertionError(message)


def test_matrix_caps_apply_only_to_the_main_macos_dispatch():
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]
    scenarios = product(
        ("pull_request", "push", "schedule", "workflow_dispatch"),
        ("refs/heads/main", "refs/heads/feature", "refs/pull/1/merge"),
        ("all", "linux", "macos"),
    )
    for event, ref, lane in scenarios:
        limited = (event, ref, lane) == ("workflow_dispatch", "refs/heads/main", "macos")
        assert _matrix_parallel_limit(jobs["test"], event, ref, lane) == (1 if limited else 256)
        assert _matrix_parallel_limit(jobs["e2e"], event, ref, lane) == (2 if limited else 256)


def test_candidate_and_linux_matrices_fit_without_parallel_throttling():
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]
    test, e2e = jobs["test"], jobs["e2e"]
    matrix = test["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-24.04"]
    assert matrix["levels"] == ["unit", "integration"]
    macos = _macos_include(matrix, "pull_request", full=True, release=True, lane="all")
    assert macos == [{"os": "macos-latest", "python": "3.15", "levels": level} for level in ("unit", "integration")]
    assert e2e["strategy"]["matrix"]["suite"] == ["cli", "supervised", "web-1", "web-2", "web-3"]
    assert '\'["ubuntu-24.04","macos-latest"]\'' in e2e["strategy"]["matrix"]["os"]
    for event, ref, lane in (("pull_request", "refs/pull/1/merge", "all"), ("push", "refs/heads/main", "linux")):
        assert _matrix_parallel_limit(test, event, ref, lane) >= 4
        assert _matrix_parallel_limit(e2e, event, ref, lane) >= 10


def test_matrix_caps_keep_every_workload_and_failure_required():
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]
    for name in ("test", "e2e"):
        job = jobs[name]
        assert job["needs"] == "changes"
        assert job["if"] == (
            "needs.changes.outputs.full_ci == 'true' && "
            "(needs.changes.outputs.core == 'true' && needs.changes.outputs.site_only != 'true')"
        )
        assert job["strategy"]["fail-fast"] is False
        assert "continue-on-error" not in job
        assert "concurrency" not in job
        assert name in jobs["ci-ok"]["needs"]
    assert jobs["test"]["timeout-minutes"] == "${{ matrix.levels == 'integration' && 15 || 10 }}"
    assert jobs["e2e"]["timeout-minutes"] == 15
    assert any(step.get("run") == "make test-parallel" for step in jobs["test"]["steps"])
    assert any(step.get("run") == "make test-e2e SUITE=${{ matrix.suite }}" for step in jobs["e2e"]["steps"])


@pytest.mark.parametrize(
    ("event", "full", "pythons"),
    [
        ("pull_request", False, []),
        ("pull_request", True, []),
        ("push", False, []),  # a push to main runs Linux; its dispatched macOS lane runs the macOS cells
        ("push", True, []),
        ("schedule", False, ["3.15"]),
        ("schedule", True, ["3.15"]),
        ("workflow_dispatch", False, ["3.15"]),
        ("workflow_dispatch", True, ["3.15"]),
    ],
)
def test_macos_cells_shard_by_level_and_ci_ok_waits_for_changes(event, full, pythons):
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    cells = _macos_include(jobs["test"]["strategy"]["matrix"], event, full)
    assert cells == [
        {"os": "macos-latest", "python": python, "levels": level}
        for python in pythons
        for level in ("unit", "integration")
    ]
    recipe = (REPO / "Makefile").read_text(encoding="utf-8").split("\ntest-parallel:", 1)[1].split("\n\n", 1)[0]
    assert "-m '($(TEST_LEVELS)) and not perf'" in recipe
    assert "--session-timeout=420" in recipe  # each shard has the cap the whole suite had
    assert {"changes", "notifier", "build"} <= set(jobs["ci-ok"]["needs"])


def test_electron_ci_runs_when_its_trigger_or_make_target_changes():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    script = next(step["run"] for step in jobs["changes"]["steps"] if step.get("id") == "paths")
    assert "[ -f desktop/package.json ]" in script
    assert "desktop/" in script
    assert "Makefile$" in script
    assert "scripts/sync_template\\.py$" in script
    assert "\\.github/workflows/ci\\.yml$" in script


def test_linux_electron_sandbox_target_configures_only_the_packaged_helper():
    """Configure the packaged app's verified sandbox helper, without assuming a source dist exists."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("desktop-electron-sandbox:", 1)[1].split("\ndesktop-electron-native:", 1)[0]
    assert "desktop/dist/Heinzelmaennchen-linux-" in recipe
    assert '--configure-file "$$packaged_sandbox"' in recipe
    assert 'require.resolve("electron"' not in recipe
    assert 'python3 desktop/ci/restore_linux_sandbox.py "$$electron_root"' not in recipe


@pytest.mark.parametrize("failure", [None, "sync", "wheel"])
def test_e2e_prepares_core_build_before_suite_and_removes_owned_temp(tmp_path, failure):
    """A failed build cannot start pytest; the suite still performs its own candidate build."""
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    trace = tmp_path / "trace"
    uv = tool_dir / "uv"
    steps = tool_dir / "fake-steps"
    for path, text in (
        (
            uv,
            (
                '#!/bin/sh\nprintf "build:%s\\n" "$*" >> "$TRACE"\n'
                'case "$*" in *"scripts/build.py sync") test "$FAIL_PREP" != sync;; *)\n'
                'for last do :; done\nprintf "dir:%s\\n" "$last" >> "$TRACE"\n'
                'test "$FAIL_PREP" != wheel;; esac\n'
            ),
        ),
        (steps, '#!/bin/sh\nprintf "suite:%s\\n" "$@" >> "$TRACE"\n'),
    ):
        assert not path.exists()
        assert not path.is_symlink()
        assert path.parent.resolve().is_relative_to(tmp_path.resolve())
        path.write_text(text)
        assert not path.samefile(os.path.realpath(os.sys.executable))
        assert not path.samefile(os.path.realpath(os.sys._base_executable))
        path.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{tool_dir}:/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
        "TRACE": str(trace),
        "FAIL_PREP": failure or "",
    }
    result = subprocess.run(
        ["make", "--no-print-directory", "test-e2e", f"STEPS={steps}"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    lines = trace.read_text().splitlines()
    assert lines[0] == "build:run --quiet --script scripts/build.py sync"
    if failure != "sync":
        assert lines[1].startswith("build:run --quiet --script scripts/build.py --quiet --wheel --out-dir ")
        assert lines[2].startswith("dir:")
        directory = Path(lines[2].removeprefix("dir:"))
        assert directory.is_relative_to(tmp_path)
        assert not directory.exists()
    assert (result.returncode == 0) is (failure is None)
    assert any(line.startswith("suite:e2e-readme=uv run pytest ") for line in lines) is (failure is None)
    if failure is None:
        [first] = [line for line in lines if line.startswith("suite:e2e-readme=")]
        [second] = [line for line in lines if line.startswith("suite:e2e=")]
        node = "tests/e2e/docs_test.py::test_readme_instructions"
        assert first.endswith(f"-x {node}")
        assert second.endswith(f"-x --deselect={node}")
        workers = "-n 2" if sys.platform == "darwin" else "-n 3"  # macOS runs fewer workers on its 3 vCPUs
        assert first.count(workers) == second.count(workers) == 1
        assert first.count("--session-timeout=600") == second.count("--session-timeout=600") == 1
    generated = _template_sync_module().makefile()
    assert "e2e-prepare" not in generated
    assert generated == (REPO / "template/Makefile").read_text(encoding="utf-8")


@pytest.mark.parametrize("first_fails", [False, True])
def test_e2e_sequence_stops_after_readme_failure(tmp_path, first_fails):
    """Each has its own step, so its own time limit; the remaining nodes run only after the README succeeds."""
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    trace = tmp_path / "trace"
    uv = tool_dir / "uv"
    steps = tool_dir / "steps"
    for path, text in (
        (
            uv,
            (
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$TRACE"\n'
                'case "$*" in *--deselect*) exit 0;; '
                '*docs_test.py::test_readme_instructions*) test "$FIRST_FAIL" != 1;; esac\n'
            ),
        ),
        (steps, '#!/bin/sh\nfor step; do /bin/sh -c "${step#*=}" || exit; done\n'),
    ):
        assert not path.exists()
        assert not path.is_symlink()
        path.write_text(text)
        assert not path.samefile(os.path.realpath(os.sys.executable))
        assert not path.samefile(os.path.realpath(os.sys._base_executable))
        path.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{tool_dir}:/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
        "TRACE": str(trace),
        "FIRST_FAIL": "1" if first_fails else "0",
    }
    result = subprocess.run(
        ["make", "--no-print-directory", "test-e2e", f"STEPS={steps}"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    commands = trace.read_text().splitlines()
    assert commands[0] == "run --quiet --script scripts/build.py sync"
    assert commands[1].startswith("run --quiet --script scripts/build.py --quiet --wheel ")
    assert len(commands) == (3 if first_fails else 4)
    assert "--deselect=" not in commands[2]
    if not first_fails:
        assert "--deselect=tests/e2e/docs_test.py::test_readme_instructions" in commands[3]
    assert (result.returncode != 0) is first_fails


@pytest.fixture(scope="module")
def ci_paths(tmp_path_factory):
    """One immutable workflow/script fixture per worker; every case still executes it with distinct data."""
    return _make_ci_paths(tmp_path_factory, with_contexts=False)


@pytest.fixture(scope="module")
def ci_contexts_paths(tmp_path_factory):
    """The same fixture with the repository's scripts/contexts.py, so the Apple-only and prose-only level L0 applies."""
    return _make_ci_paths(tmp_path_factory, with_contexts=True)


def _make_ci_paths(tmp_path_factory, *, with_contexts):
    jobs = _ci_jobs()
    script = next(step["run"] for step in jobs["changes"]["steps"] if step.get("id") == "paths")
    directory = tmp_path_factory.mktemp("ci-paths")
    if with_contexts:
        (directory / "scripts").symlink_to(REPO / "scripts", target_is_directory=True)
    stubs = directory / "bin"
    stubs.mkdir()
    git = stubs / "git"
    git.write_text('#!/bin/sh\nprintf "%s" "$CHANGED_PATHS"\nexit "$DIFF_EXIT"\n')
    git.chmod(0o700)
    (directory / "desktop").mkdir()
    (directory / "desktop" / "package.json").write_text("{}")
    return script, directory


def _changes_outputs(  # noqa: PLR0913 - independent changed-path, event and release-selection fixture inputs
    tmp_path, ci_paths, event: str, changed: str, git_exit: int = 0, *, release: bool = False
) -> set[str]:
    script, directory = ci_paths
    output = tmp_path / "output"
    subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": f"{directory / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "EVENT": event,
            "FULL_RELEASE": str(release).lower(),
            "BASE": "base",
            "GITHUB_OUTPUT": str(output),
            "CHANGED_PATHS": changed,
            "DIFF_EXIT": str(git_exit),
        },
        cwd=directory,
        check=True,
        timeout=scaled(30),
    )
    return set(output.read_text().splitlines())


@pytest.mark.parametrize(
    ("event", "changed", "core"),
    [
        ("push", "docs/guides/install.md\nREADME.md\n", "false"),
        ("push", "docs/assets/shot.png\n", "false"),
        ("push", "docs/guides/install.md\nCHANGELOG.md\n", "true"),
        ("push", "docs/install.sh\n", "true"),
        ("push", "docs/guides/install.md\nsrc/heinzel/__init__.py\n", "true"),
        ("push", "", "true"),
        ("pull_request", "docs/guides/install.md\n", "true"),
        ("schedule", "docs/guides/install.md\n", "true"),
    ],
)
def test_only_a_prose_push_to_main_skips_the_core_jobs(tmp_path, ci_paths, event, changed, core):
    assert f"core={core}" in _changes_outputs(tmp_path, ci_paths, event, changed)


@pytest.mark.parametrize(
    ("path", "full"),
    [
        ("src/heinzel/modules/team/_render.py\n", "false"),
        ("web/src/components/Footer.tsx\n", "false"),
        ("scripts/main_batch.py\nscripts/sync_template.py\ntests/unit/main_batch_test.py\n", "false"),
        ("src/heinzel/kernel/_exposure.py\n", "false"),
        ("src/heinzel/kernel/look/_contributions.py\n", "false"),
        ("src/heinzel/heads/_tool_install.py\n", "true"),
        ("src/heinzel/heads/web/__init__.py\n", "true"),
        ("src/heinzel/kernel/session/_session_io.py\n", "true"),
        ("src/heinzel/kernel/_new_platform_boundary.py\n", "true"),
        ("src/heinzel/modules/system/config/_reveal.py\n", "true"),
        ("src/heinzel/modules/system/state/_service.py\n", "true"),
        ("scripts/acceptance/run_acceptance.py\n", "true"),
        ("scripts/web_build.py\n", "true"),
        ("scripts/new_tool.py\n", "true"),
        ("template/Makefile\n", "true"),
        ("template/pyproject.toml.jinja\n", "true"),
        ("web/pnpm-lock.yaml\n", "true"),
        ("web/e2e/daemon-install.mjs\n", "true"),
        ("web/package.json\n", "true"),
        ("web/tsconfig.json\n", "true"),
        ("web/src/components/Footer.tsx\nweb/pnpm-lock.yaml\n", "true"),
        (".github/workflows/ci.yml\nweb/package.json\n", "true"),
        ("src/heinzel/heads/web/_supervision.py\n", "true"),
        ("src/heinzel/kernel/install/_installation_mutations.py\n", "true"),
        ("src/heinzel/modules/system/lifecycle/_service.py\n", "true"),
        ("scripts/notifier.py\n", "true"),
        ("scripts/tools.toml\n", "true"),
        ("pyproject.toml\n", "true"),
        ("uv.lock\n", "true"),
        ("web/src/components/Footer.tsx\nuv.lock\n", "true"),
        ("src/heinzel/modules/system/daemon/_service.py\n", "true"),
        ("src/heinzel/modules/system/runner/chat/_policy.py\n", "true"),
        ("src/heinzel/modules/system/notify/macos/app.swift\n", "true"),
        ("desktop/src/main.js\n", "true"),
        ("scripts/install.sh\n", "true"),
        ("src/heinzel/modules/system/update/_update.py\n", "true"),
        ("tests/e2e/uninstall_test.py\n", "true"),
        (".github/workflows/ci.yml\n", "false"),
        ("Makefile\n", "true"),
        ("new/unclassified/path.py\n", "true"),
        ("src/heinzel/modules/team/_render.py\ndesktop/src/main.js\n", "true"),
        ("", "true"),
    ],
)
def test_macos_native_jobs_are_retained_for_platform_and_unknown_paths(tmp_path, ci_paths, path, full):
    outputs = _changes_outputs(tmp_path, ci_paths, "pull_request", path)
    assert f"macos_full={full}" in outputs
    if full == "true":
        assert "electron=true" in outputs


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
@pytest.mark.parametrize("changed", [".github/workflows/ci.yml\n", "web/src/Footer.tsx\n", "", "new/path.py\n"])
def test_non_pr_runs_retain_native_macos_jobs_for_every_scope(tmp_path, ci_paths, event, changed):
    assert "macos_full=true" in _changes_outputs(tmp_path, ci_paths, event, changed)


def test_failed_diff_with_partial_output_still_runs_full_macos(tmp_path, ci_paths):
    outputs = _changes_outputs(tmp_path, ci_paths, "pull_request", "src/heinzel/modules/team/_render.py\n", git_exit=1)
    assert {"macos_full=true", "electron=true", "core=true"} <= outputs


@pytest.mark.parametrize(
    ("event", "changed", "electron"),
    [
        ("push", "src/heinzel/__init__.py\n", "true"),
        ("push", "web/src/components/Footer.tsx\n", "true"),
        ("push", "desktop/src/main.js\n", "true"),
        ("push", "Makefile\n", "true"),
        ("push", "scripts/sync_template.py\n", "true"),
        ("push", ".github/workflows/ci.yml\n", "true"),
        ("push", "", "true"),  # a diff git could not compute
        ("pull_request", "src/heinzel/__init__.py\n", "true"),
        ("pull_request", "desktop/src/main.js\n", "true"),
        ("schedule", "src/heinzel/__init__.py\n", "true"),
        ("workflow_dispatch", "src/heinzel/__init__.py\n", "true"),
    ],
)
def test_electron_runs_on_every_event_but_a_pull_request_and_for_its_paths_there(
    tmp_path, ci_paths, event, changed, electron
):
    assert f"electron={electron}" in _changes_outputs(tmp_path, ci_paths, event, changed)


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
@pytest.mark.parametrize(
    "changed", ["web/src/components/Footer.tsx\n", "plugins/heinzel-x/src/plugin.py\n", "tests/unit/a_test.py\n"]
)
def test_every_selector_key_stays_on_outside_a_pull_request(tmp_path, ci_paths, event, changed):
    """CI OK requires these jobs on push, schedule and dispatch; a skipped one fails the run (main run 45072787338)."""
    outputs = _changes_outputs(tmp_path, ci_paths, event, changed)
    assert {
        "core=true",
        "notifier=true",
        "contracts=true",
        "electron=true",
        "macos_full=true",
        "site_only=false",
    } <= outputs


def test_the_test_matrix_keeps_the_whole_output_of_a_failure():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    assert jobs["test"]["timeout-minutes"] == "${{ matrix.levels == 'integration' && 15 || 10 }}"
    recipe = (REPO / "Makefile").read_text(encoding="utf-8").split("\ntest-parallel:", 1)[1].split("\n\n", 1)[0]
    assert "--session-timeout=420" in recipe
    step = next(step for step in jobs["test"]["steps"] if step.get("run") == "make test-parallel")
    assert step["env"]["VERBOSE"] == "1"


SITE_ONLY_PATHS = [
    "docs/guides/install.md",
    "docs/assets/website/site.css",
    "docs/overrides/home.html",
    "CONTRIBUTING.md",
    "mkdocs.yml",
    "scripts/site_pages.py",
    "scripts/check_docs.py",
    "tests/unit/site_catalog_test.py",
    "tests/unit/docs_style_test.py",
    "tests/unit/documentation_test.py",
]


@pytest.mark.parametrize("path", SITE_ONLY_PATHS)
def test_a_site_pull_request_skips_the_app_jobs(tmp_path, ci_paths, path):
    outputs = _changes_outputs(tmp_path, ci_paths, "pull_request", f"{path}\n")
    assert "site_only=true" in outputs
    assert "electron=false" in outputs


@pytest.mark.parametrize(
    ("event", "changed"),
    [
        ("pull_request", "README.md\n"),  # the end-to-end suite runs its commands
        ("pull_request", "CHANGELOG.md\n"),
        ("pull_request", "docs/install.sh\n"),
        ("pull_request", ".github/workflows/docs.yml\n"),
        ("pull_request", "template/README.md\n"),
        ("pull_request", "docs/TEAMS.md\nsrc/heinzel/__init__.py\n"),
        ("pull_request", ""),
        ("push", "mkdocs.yml\n"),
        ("schedule", "docs/TEAMS.md\n"),
        ("workflow_dispatch", "docs/TEAMS.md\n"),
    ],
)
def test_everything_else_runs_the_app_jobs(tmp_path, ci_paths, event, changed):
    assert "site_only=false" in _changes_outputs(tmp_path, ci_paths, event, changed)


def test_site_only_skips_exactly_the_app_test_jobs_and_ci_ok_waits_for_site_tests():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    for name, lane in (("test", ""), ("coverage", "macos"), ("perf", "macos"), ("e2e", "")):
        assert jobs[name]["if"] == (
            "needs.changes.outputs.full_ci == 'true' && "
            "(needs.changes.outputs.core == 'true' && needs.changes.outputs.site_only != 'true')"
            + (f" && needs.changes.outputs.lane != '{lane}'" if lane else "")
        )
    for name in ("lint", "build"):
        assert "site_only" not in jobs[name]["if"]
    assert jobs["site-tests"]["if"] == (
        "needs.changes.outputs.full_ci == 'true' && (needs.changes.outputs.site_only == 'true')"
        " && needs.changes.outputs.lane != 'macos'"
    )
    assert "site-tests" in jobs["ci-ok"]["needs"]


def test_site_only_agrees_with_the_pre_push_scope_and_runs_its_tests():
    spec = importlib.util.spec_from_file_location("scoped_checks", REPO / "scripts" / "scoped_checks.py")
    assert spec
    assert spec.loader
    scoped = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scoped)
    tooling = [path for path in SITE_ONLY_PATHS if path.startswith(("scripts/", "tests/"))]
    assert scoped.scoped_steps(tooling) is not None
    assert scoped.scoped_steps([path for path in SITE_ONLY_PATHS if path not in tooling]) is not None
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    assert any(step.get("run", "").startswith("make test-site") for step in jobs["site-tests"]["steps"])
    recipe = (REPO / "Makefile").read_text(encoding="utf-8").split("\ntest-site:", 1)[1].split("\n\n", 1)[0]
    assert set(scoped.MARKDOWN_TESTS) <= set(recipe.replace('"', " ").split())


def test_release_versions_ignore_edge_tags_and_stay_under_plugin_caps():
    """An unanchored pattern read `edge-v1.2.3.dev4` as a release; a feature must stay under plugins' `<0.10` caps."""
    cliff = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["git-cliff"]
    assert cliff["git"]["tag_pattern"].startswith("^v")
    assert cliff["bump"]["features_always_bump_minor"] is False
    assert cliff["bump"]["breaking_always_bump_major"] is False


def test_macos_specific_tests_are_a_required_serial_job_on_macos():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    job = jobs["macos-specific"]
    assert (job["name"], job["runs-on"]) == ("macOS-specific tests", "macos-latest")
    assert "macos-specific" in jobs["ci-ok"]["needs"]
    recipe = (REPO / "Makefile").read_text(encoding="utf-8").split("\ntest-macos-specific:", 1)[1].split("\n\n", 1)[0]
    assert "-m macos_specific" in recipe
    assert " -n " not in recipe  # serial: no worker contention for the machine-wide notifier tests
    assert any(step.get("run") == "make test-macos-specific" for step in job["steps"])


@pytest.mark.parametrize(
    ("name", "target"), [("macos-specific", "test-macos-specific"), ("macos-smoke", "test-macos-smoke")]
)
def test_native_face_compile_has_a_required_serial_job_with_a_real_notifier_build(name, target):
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    job = jobs[name]
    assert job["runs-on"] == "macos-latest"
    assert name in jobs["ci-ok"]["needs"]
    assert not job.get("continue-on-error", False)
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert commands.index("make notifier") < commands.index(f"make {target}")
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    assert "-m macos_specific" in recipe
    assert " -n " not in recipe
    assert "test-e2e: e2e-prepare" in makefile


@lru_cache(maxsize=1)
def _parsed_ci_jobs(source: str) -> dict:
    """Parse unchanged CI source once per worker."""
    return yaml.safe_load(source)["jobs"]


def _ci_jobs() -> dict:
    source = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return deepcopy(_parsed_ci_jobs(source))


def test_ci_jobs_use_current_source_and_isolate_nested_mutations(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("jobs: {sample: {steps: [{run: first}]}}")
    _ci_jobs()["sample"]["steps"][0]["run"] = "changed by caller"
    assert _ci_jobs()["sample"]["steps"][0]["run"] == "first"
    workflow.write_text("jobs: {sample: {steps: [{run: second}]}}")
    assert _ci_jobs()["sample"]["steps"][0]["run"] == "second"


@pytest.mark.parametrize(
    ("event", "candidate", "selected"),
    [
        ("schedule", False, {"maintenance", "full", "paths", "lane"}),  # the nightly selects the full workload
        ("pull_request", False, {"release", "full", "paths", "lane"}),
        ("push", False, {"release", "full", "paths", "lane"}),
        ("workflow_dispatch", False, {"release", "full", "paths", "lane"}),
        ("pull_request", True, {"release", "full", "paths", "lane"}),
        ("workflow_dispatch", True, {"release", "full", "paths", "lane"}),
    ],
)
def test_changed_paths_event_policy(event, candidate, selected):
    changes = _ci_jobs()["changes"]
    nonscheduled = "github.event_name != 'schedule'"
    conditions = {
        "github.event_name == 'schedule'": event == "schedule",
        nonscheduled: event != "schedule",
        nonscheduled + " && steps.release.outputs.full_release != 'true'": event != "schedule" and not candidate,
        None: True,
    }
    assert {step["id"] for step in changes["steps"] if "id" in step and conditions[step.get("if")]} == selected
    # checkout and setup-uv run on every event, the schedule included: the paths step reads the checkout
    assert all("if" not in step for step in changes["steps"] if "uses" in step)
    assert changes["outputs"]["scheduled_maintenance"] == "${{ steps.maintenance.outputs.selected }}"
    assert changes["outputs"]["full_release"] == "${{ steps.release.outputs.full_release }}"
    assert changes["outputs"]["full_ci"] == "${{ steps.full.outputs.selected }}"
    assert not any("FOCUSED_CI_PLANS" in str(step) for step in changes["steps"])


@pytest.mark.parametrize(
    ("repository", "ref", "selected"),
    [
        ("helmut-hoffer-von-ankershoffen/heinzel", "refs/heads/main", True),
        ("someone/heinzel", "refs/heads/main", False),
        ("helmut-hoffer-von-ankershoffen/heinzel", "refs/heads/other", False),
    ],
)
def test_scheduled_maintenance_selects_only_canonical_main(tmp_path, repository, ref, selected):
    step = next(step for step in _ci_jobs()["changes"]["steps"] if step.get("id") == "maintenance")
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={"GITHUB_REPOSITORY": repository, "GITHUB_REF": ref, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(30),
    )
    assert (result.returncode == 0) is selected
    if selected:
        assert output.read_text() == "selected=true\n"
    else:
        assert not output.exists()


SCHEDULED_REQUIRED = ("admission", "changes", "lint", "secrets", "audit", "quarantine-registry")


def test_scheduled_jobs_keep_maintenance_separate_from_full_and_focused_tests():
    jobs = _ci_jobs()
    assert set(SCHEDULED_REQUIRED) <= set(jobs["ci-ok"]["needs"])
    for name in ("lint", "audit"):
        assert jobs[name]["needs"] == "changes"
        assert "needs.changes.outputs.scheduled_maintenance == 'true'" in jobs[name]["if"]
    for name, job in jobs.items():
        if name in (*SCHEDULED_REQUIRED, "ci-ok"):
            continue
        condition = job["if"]
        assert condition.startswith(
            (
                "needs.changes.outputs.full_ci == 'true'",
                "needs.changes.outputs.full_release == 'true'",
                "${{ !cancelled() && needs.changes.outputs.full_ci == 'true'",
                "github.event_name == 'pull_request'",
                "failure() && github.event_name == 'pull_request'",
                "github.event_name == 'push' &&",
                "needs.changes.outputs.focused_mode != ''",
                "${{ !cancelled() && needs.local-plugin-plan.outputs.selected == 'True'",
                "needs.consumer-plan.outputs.selected == 'True'",
            )
        ), name
    assert "scheduled_maintenance" not in _template_sync_module().ci_workflow()


@pytest.mark.parametrize(
    ("job", "outcome", "maintenance", "error"),
    [
        (None, "success", "true", ""),
        (None, "success", "", "Scheduled maintenance was not selected"),
        *(
            (job, outcome, "true", f"Required schedule workload job did not pass: {job}")
            for job in SCHEDULED_REQUIRED
            for outcome in ("failure", "cancelled", "skipped")
        ),
    ],
)
def test_scheduled_aggregate_requires_every_maintenance_job(job, outcome, maintenance, error):
    jobs = _ci_jobs()
    step = next(step for step in jobs["ci-ok"]["steps"] if "EVENT" in step.get("env", {}))
    assert step["if"] == "github.event_name == 'schedule' || needs.changes.outputs.focused_mode != ''"
    results = {
        name: {"result": "success" if name in SCHEDULED_REQUIRED else "skipped"} for name in jobs["ci-ok"]["needs"]
    }
    if job is not None:
        results[job]["result"] = outcome
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={
            "PATH": os.environ["PATH"],
            "EVENT": "schedule",
            "SCHEDULED_MAINTENANCE": maintenance,
            "RESULTS": json.dumps(results),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(30),
    )
    assert result.returncode == bool(error)
    assert error in result.stdout


def _oses(value: str | list[str], event: str, *, macos_full: bool = False, release: bool = False) -> list[str]:
    """The matrix values for an event, from a plain list or a pull-request conditional."""
    if isinstance(value, list):
        return value
    constant = re.fullmatch(r"\$\{\{ fromJSON\('(?P<all>\[[^']*\])'\) \}\}", value)
    if constant:
        return json.loads(constant["all"])
    match = re.fullmatch(
        r"\$\{\{ fromJSON\(github\.event_name == 'pull_request' && "
        r"(?P<native>needs\.changes\.outputs\.macos_full != 'true' && )?"
        r"(?P<release>needs\.changes\.outputs\.full_release != 'true' && )?'(?P<pr>.*)' "
        r"\|\| '(?P<full>.*)'\) \}\}",
        value,
    )
    assert match, value
    pr_lane = (
        event == "pull_request" and (not match["native"] or not macos_full) and (not match["release"] or not release)
    )
    return json.loads(match["pr"] if pr_lane else match["full"])


def _runs_on_pull_request(job: dict) -> bool:
    return "github.event_name != 'pull_request'" not in str(job.get("if", ""))


def test_every_scheduled_macos_job_blocks_ci_ok():
    jobs = _ci_jobs()
    for workflow_jobs in (jobs, _generated_plugin_ci()["jobs"]):
        for name, job in workflow_jobs.items():
            assert job.get("continue-on-error", False) is False, name
            for step in job.get("steps", []):
                assert step.get("continue-on-error", False) is False, name
    for name in ("setup", "test", "e2e", "notifier", "macos-specific", "macos-smoke", "electron"):
        assert name in jobs["ci-ok"]["needs"], name
    script = next(step["run"] for step in jobs["ci-ok"]["steps"] if "RESULTS" in step.get("env", {}))
    assert "success | skipped) ;;" in script
    assert "continue-on-error" not in jobs["ci-ok"]
    for name in ("setup", "e2e", "electron"):  # narrowed by matrix, never by a job-level if; a release PR keeps macOS
        expression = jobs[name]["strategy"]["matrix"]["os"]
        assert "github.event_name == 'pull_request' && needs.changes.outputs.full_release != 'true'" in expression
        assert "macos_full" not in expression
        assert "macos-latest" not in jobs[name]["if"]
    assert "needs.changes.outputs.macos_full" not in jobs["test"]["strategy"]["matrix"]["include"]


def test_macos_workload_is_reviewed_focused_or_the_unchanged_candidate_matrix():
    jobs = _ci_jobs()
    candidate_only = "needs.changes.outputs.full_ci == 'true'"
    for name in ("setup", "test", "e2e", "notifier", "macos-specific", "macos-smoke", "electron", "quarantine"):
        assert jobs[name]["if"].startswith(candidate_only)
    assert jobs["focused-tests"]["if"] == "needs.changes.outputs.focused_mode != ''"
    assert "fromJSON(needs.changes.outputs.focused_matrix" in jobs["focused-tests"]["strategy"]["matrix"]
    assert "focused-tests" in jobs["ci-ok"]["needs"]
    assert "quarantine" not in jobs["ci-ok"]["needs"]
    assert _oses(jobs["setup"]["strategy"]["matrix"]["os"], "workflow_dispatch") == [
        "ubuntu-24.04", "ubuntu-24.04-arm", "macos-latest",
    ]  # fmt: skip
    smoke = jobs["macos-smoke"]
    assert (smoke["name"], smoke["runs-on"]) == ("macOS smoke", "macos-latest")
    assert smoke["timeout-minutes"] <= 10
    assert "needs.changes.outputs.full_ci != 'true'" in smoke["if"]
    runs = {step.get("run"): step.get("if") for step in smoke["steps"] if "run" in step}
    assert runs["make test-macos-smoke"] is None
    assert runs["make notifier"] is None  # the macos_specific real-launch tests must not skip
    assert any(step.get("with", {}).get("name") == "notifier" for step in smoke["steps"])
    build = jobs["build"]
    assert {"macos-smoke", "notifier"} <= set(build["needs"])
    assert "needs.macos-smoke.result != 'failure'" in build["if"]
    wheel_check = next(step for step in build["steps"] if step.get("name") == "Check the wheel ships Heinzel Notifier")
    assert "needs.macos-smoke.result == 'success'" in wheel_check["if"]
    recipe = (REPO / "Makefile").read_text(encoding="utf-8").split("\ntest-macos-smoke:", 1)[1].split("\n\n", 1)[0]
    assert "uv run hz --version" in recipe
    assert "-m macos_specific" in recipe
    assert " -n " not in recipe


@pytest.mark.parametrize("event", ["pull_request", "push", "schedule", "workflow_dispatch"])
@pytest.mark.parametrize("release", [False, True])
def test_macos_e2e_setup_and_electron_run_on_main_schedule_dispatch_and_release_prs_only(event, release):
    jobs = _ci_jobs()
    native = event != "pull_request" or release
    assert _oses(jobs["setup"]["strategy"]["matrix"]["os"], event, release=release) == (
        ["ubuntu-24.04", "ubuntu-24.04-arm", "macos-latest"] if native else ["ubuntu-24.04", "ubuntu-24.04-arm"]
    )
    for name in ("e2e", "electron"):
        assert _oses(jobs[name]["strategy"]["matrix"]["os"], event, release=release) == (
            ["ubuntu-24.04", "macos-latest"] if native else ["ubuntu-24.04"]
        )


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("pull_request", ["3.15"]),
        ("push", ["3.15"]),
        ("schedule", ["3.15"]),
        ("workflow_dispatch", ["3.15"]),
    ],
)
def test_every_event_tests_only_python_3_15(event, expected):
    test = _ci_jobs()["test"]
    assert _oses(test["strategy"]["matrix"]["python"], event) == expected
    probe = next(step for step in test["steps"] if step.get("run") == "make linux-user-manager-proof")
    assert probe["if"] == (  # a cell every pull request runs, once
        "matrix.os == 'ubuntu-24.04' && matrix.python == '3.15' && matrix.levels == 'unit'"
    )
    assert test["strategy"]["matrix"]["levels"] == ["unit", "integration"]  # Linux shards by level under the cap


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("pull_request", ["3.15"]),
        ("push", ["3.15"]),
        ("schedule", ["3.15"]),
        ("workflow_dispatch", ["3.15"]),
    ],
)
def test_plugin_macos_matrix_preserves_its_existing_python_coverage(event, expected):
    cells = _macos_include(_generated_plugin_ci()["jobs"]["test"]["strategy"]["matrix"], event, full=False)
    assert cells == [
        {"os": "macos-latest", "python": version, "levels": level}
        for version in expected
        for level in ("unit", "integration")
    ]


def test_a_failed_cheap_check_cancels_the_rest_of_a_pull_request_run():
    jobs = _ci_jobs()
    for cheap in ("dependency-review", "commits", "lint", "secrets", "setup", "audit"):
        stop = jobs[f"stop-after-{cheap}"]
        assert stop["if"] == (
            "failure() && github.event_name == 'pull_request' && "
            "!startsWith(github.head_ref, 'release/') && "
            "github.event.pull_request.head.repo.full_name == github.repository"
        )
        assert stop["needs"] == [cheap]
        assert stop["permissions"] == {"actions": "write"}
        assert any(step.get("run") == 'gh run cancel "$RUN_ID" --repo "$REPOSITORY"' for step in stop["steps"])
    for expensive in ("test", "e2e", "coverage", "electron", "macos-smoke"):
        assert "lint" not in str(jobs[expensive].get("needs", "")), expensive  # they start in parallel, not after


def test_release_acceptance_can_pin_public_artifacts_and_correlate_its_run():
    """A coordinator can keep public-path evidence tied to one release after main moves."""
    workflow = yaml.safe_load((REPO / ".github/workflows/acceptance.yml").read_text())
    events = workflow.get("on", workflow.get(True))
    for trigger in ("workflow_dispatch", "workflow_call"):
        for name in ("expected_wheel_sha256", "expected_profile_sha256", "release_attempt"):
            assert events[trigger]["inputs"][name]["default"] == ""
    pins, job = workflow["jobs"]["pins"], workflow["jobs"]["shard"]
    for variable in ("EXPECTED_WHEEL_SHA256", "EXPECTED_PROFILE_SHA256"):
        assert f"inputs.{variable.lower()}" in pins["env"][variable]  # a supplied pin is the pin every shard tests
    command = next(step["run"] for step in job["steps"] if step.get("name") == "Run the acceptance harness")
    for flag, variable in (
        ("--accept-wheel-sha256", "EXPECTED_WHEEL_SHA256"),
        ("--accept-profile-sha256", "EXPECTED_PROFILE_SHA256"),
    ):
        assert f'{flag} "${variable}"' in command
        assert job["env"][variable].startswith("${{ needs.pins.outputs.")
    assert "inputs.release_attempt" in workflow["run-name"]
    assert workflow["permissions"] == {}
    for name in ("pins", "shard", "acceptance"):
        assert workflow["jobs"][name]["permissions"] == {"contents": "read"}


def test_sharded_release_acceptance_keeps_one_named_job_receipt_and_verdict_per_os():
    """The release driver reads `Acceptance (<os>)` jobs and `acceptance-result-<OS>` receipts; sharding keeps both."""
    workflow = yaml.safe_load((REPO / ".github/workflows/acceptance.yml").read_text())
    jobs = workflow["jobs"]
    shard = jobs["shard"]
    assert shard["strategy"]["fail-fast"] is False
    assert shard["strategy"]["matrix"] == {
        "os": ["macos-latest", "ubuntu-24.04"],
        "shard": ["public", "update", "plugins", "browser"],
    }
    assert shard["name"].startswith("Acceptance shard (")  # never a name the driver counts as a verdict job
    run = next(step["run"] for step in shard["steps"] if step.get("name") == "Run the acceptance harness")
    assert '--shard "$SHARD"' in run
    assert "--bridge-quota-wait-seconds 3900" in run
    reduce = jobs["acceptance"]
    assert reduce["name"] == "Acceptance (${{ matrix.os }})"
    assert reduce["needs"] == ["pins", "shard"]
    assert {(entry["os"], entry["runner_os"]) for entry in reduce["strategy"]["matrix"]["include"]} == {
        ("macos-latest", "macOS"),
        ("ubuntu-24.04", "Linux"),
    }
    steps = {step.get("name"): step for step in reduce["steps"]}
    assert "--release" in steps["Reduce the four shard receipts of this OS"]["run"]
    assert steps["Upload the receipt"]["with"]["name"] == "acceptance-result-${{ matrix.runner_os }}"
    download = next(step for step in reduce["steps"] if "download-artifact@" in step.get("uses", ""))
    assert download["with"]["pattern"] == "acceptance-shard-${{ matrix.runner_os }}-*"


@pytest.mark.parametrize(
    "changed", ["CHANGELOG.md\npyproject.toml\n", "docs/guides/install.md\n", "plugins/example/file.py\n"]
)
def test_release_candidates_force_the_whole_existing_matrix_for_every_changed_scope(tmp_path, ci_paths, changed):
    outputs = _changes_outputs(tmp_path, ci_paths, "pull_request", changed, release=True)
    assert {
        "macos_full=true",
        "site_only=false",
        "core=true",
        "notifier=true",
        "contracts=true",
        "electron=true",
    } <= outputs
    jobs = _ci_jobs()
    assert _oses(jobs["test"]["strategy"]["matrix"]["python"], "pull_request", release=True) == [
        "3.15",
    ]
    assert len(_macos_include(jobs["test"]["strategy"]["matrix"], "pull_request", full=True, release=True)) == 2
    assert _oses(jobs["e2e"]["strategy"]["matrix"]["os"], "pull_request", release=True) == [
        "ubuntu-24.04",
        "macos-latest",
    ]
    assert jobs["e2e"]["strategy"]["matrix"]["suite"] == ["cli", "supervised", "web-1", "web-2", "web-3"]
    assert "needs.changes.outputs.full_ci == 'true'" in jobs["notifier"]["if"]
    assert "needs.changes.outputs.full_ci == 'true'" in jobs["macos-specific"]["if"]
    assert "needs.changes.outputs.full_ci != 'true'" in jobs["macos-smoke"]["if"]
    assert "release-candidate-source" in jobs["ci-ok"]["needs"]


def test_release_candidate_source_and_selector_never_reach_plugin_templates():
    generated = _template_sync_module().ci_workflow()
    assert "release_candidate.py" not in generated
    assert "release-candidate-source" not in generated
    assert "full_release" not in generated
    assert (
        "github.event_name == 'pull_request' && needs.changes.outputs.full_ci != 'true'"
        in (_ci_jobs()["test"]["strategy"]["matrix"]["include"])
    )


def test_macos_cli_e2e_provisions_managed_python_in_ci_and_the_focused_diagnostic():
    focused = yaml.safe_load((REPO / ".github/workflows/focused-job.yml").read_text(encoding="utf-8"))["jobs"][
        "focused"
    ]
    for job, condition in (
        (_ci_jobs()["e2e"], "matrix.os == 'macos-latest' && matrix.suite == 'cli'"),
        (focused, "inputs.os == 'macos-latest' && inputs.kind == 'e2e' && inputs.selector == 'cli'"),
    ):
        setup = next(step for step in job["steps"] if step.get("uses") == "./.github/actions/setup-uv")
        assert setup["env"]["UV_PYTHON_PREFERENCE"] == "${{ " + condition + " && 'only-managed' || 'managed' }}"
    assert "UV_PYTHON_PREFERENCE" not in _template_sync_module().ci_workflow()


def test_lint_job_leaves_room_for_the_candidate_web_component_suite():
    lint = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["lint"]
    assert lint["steps"][-1] == {"if": "needs.changes.outputs.full_ci == 'true'", "run": "make test-web"}
    # Cancelled at 5:17 under five minutes in the 0.9.40 candidates; ten is about twice the observed duration.
    assert lint["timeout-minutes"] == 10


def test_every_job_that_sets_up_uv_gets_the_reviewed_pyyaml_before_any_sync():
    """Python 3.15 has no PyYAML wheel: no job may build it from source with an unpinned backend."""
    action = yaml.safe_load((REPO / ".github" / "actions" / "setup-uv" / "action.yml").read_text(encoding="utf-8"))
    steps = action["runs"]["steps"]
    bootstrap = next(step for step in steps if "bootstrap-pyyaml.sh" in step.get("run", ""))
    assert steps[-1] is bootstrap  # after uv and Python are set up, before the caller's first sync
    assert "uv venv --allow-existing --python 3.15" in bootstrap["run"]
    # the call itself, not only a guard that names the script
    assert re.search(r"^\s*bash scripts/bootstrap-pyyaml\.sh$", bootstrap["run"], re.MULTILINE)
    assert 'requires-python = ">=3.15"' in bootstrap["run"]
    script = (REPO / "scripts" / "bootstrap-pyyaml.sh").read_text(encoding="utf-8")
    assert 'install --lock uv.lock --python "$project_python"' in script
    module = (REPO / "src" / "heinzel" / "bootstrap_pyyaml.py").read_text(encoding="utf-8")
    assert '"direct_url.json").unlink(missing_ok=True)' in module  # a later sync never rebuilds it from source
    for name, job in _ci_jobs().items():
        for step in job.get("steps", []):
            if "uv sync" in step.get("run", ""):
                assert "--no-install-package pyyaml" in step["run"], f"{name}: syncs PyYAML from source"


def _project_syncing(command: str) -> bool:
    """Whether a `uv sync` or `uv run --no-build` line installs the project's locked dependencies."""
    if not re.search(r"\buv (sync|run)\b", command) or "uvx" in command:
        return False
    if any(flag in command for flag in ("--no-project", "--script", "--no-sync", "UV_NO_SYNC")):
        return False
    return "uv sync" in command or "--no-build" in command


def _commands_that_sync_the_project() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        jobs = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {})
        for name, job in jobs.items():
            if str(job.get("env", {}).get("UV_NO_SYNC")) == "1":
                continue
            for step in job.get("steps", []):
                lines = step.get("run", "").replace("\\\n", " ").splitlines()
                found.extend((f"{path.name}:{name}", line) for line in lines)
    for path in [REPO / "Makefile", *sorted((REPO / "scripts").glob("*.sh")), *sorted((REPO / "scripts").glob("*.mk"))]:
        lines = path.read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
        found.extend((f"{path.name}:{number}", line) for number, line in enumerate(lines, 1))
    return [(where, line) for where, line in found if _project_syncing(line)]


def test_no_project_sync_on_python_3_15_builds_or_requires_a_pyyaml_source_build():
    """`--no-build` fails to resolve PyYAML (no 3.15 wheel) even when it is installed; each sync must leave it out."""
    offenders = [(where, line) for where, line in _commands_that_sync_the_project() if "--no-build" in line]
    assert [(w, line) for w, line in offenders if "--no-install-package" not in line or "pyyaml" not in line] == []
    from importlib import util

    spec = util.spec_from_file_location("focused_job", REPO / "scripts" / "focused_job.py")
    module = util.module_from_spec(spec)
    sys.modules["focused_job"] = module
    spec.loader.exec_module(module)
    sync = module.plan("pytest", "tests/unit/ci_test.py", Path("junit.xml"))[0]
    assert sync[-2:] == ["--no-install-package", "pyyaml"]


def test_every_job_with_its_own_uv_setup_builds_the_reviewed_pyyaml_before_it_syncs():
    """The composite action does it for its callers; a workflow that sets uv up itself must do it explicitly."""
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        jobs = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {})
        for name, job in jobs.items():
            steps = job.get("steps", [])
            direct = [i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")]
            runs = [step.get("run", "") for step in steps]
            if not direct or job.get("if") is False or not any(_project_syncing(run) or "make " in run for run in runs):
                continue
            bootstrap = [i for i, run in enumerate(runs) if "bootstrap-pyyaml.sh" in run or "make pyyaml" in run]
            where = f"{path.name}:{name} syncs without the reviewed PyYAML"
            assert bootstrap, where
            assert bootstrap[0] > direct[0], where


def test_no_script_or_workflow_resolves_a_fresh_environment_that_would_build_pyyaml():
    """A fresh environment (`uv run --isolated`) resolves from the index: PyYAML would build from source."""
    texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in [*(REPO / ".github" / "workflows").glob("*.yml"), *(REPO / "scripts").glob("*.sh")]
    }
    for name, text in texts.items():
        assert "--isolated" not in text, f"{name}: use a throwaway environment with the reviewed PyYAML"
    # the local multi-Python target is the one exception, and CI never calls it
    assert not any("test-all-pythons" in text for text in texts.values())


def test_end_to_end_installs_resolve_the_reviewed_pyyaml_wheel():
    conftest = (REPO / "tests" / "e2e" / "conftest.py").read_text(encoding="utf-8")
    assert "def reviewed_pyyaml(" in conftest
    assert '"UV_FIND_LINKS"' in conftest
    assert "autouse=True" in conftest
    assert "--wheel-dir" in (REPO / "scripts" / "bootstrap-pyyaml.sh").read_text(encoding="utf-8")


def _flagged_then_bootstrapped(lines: list[str]) -> bool:
    """`uv sync` is exact: a sync that leaves PyYAML out removes one installed before it, so the build must follow."""
    last = max(
        (i for i, line in enumerate(lines) if "uv " in line and "--no-install-package pyyaml" in line), default=-1
    )
    return last < 0 or any("bootstrap-pyyaml.sh" in line or "make pyyaml" in line for line in lines[last + 1 :])


def test_the_reviewed_pyyaml_is_built_after_the_last_sync_that_leaves_it_out():
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {}).items():
            lines = [line for step in job.get("steps", []) for line in step.get("run", "").splitlines()]
            assert _flagged_then_bootstrapped(lines), f"{path.name}:{name} removes PyYAML and never builds it again"
    for path in sorted((REPO / "scripts").glob("*.sh")):
        lines = path.read_text(encoding="utf-8").splitlines()
        assert _flagged_then_bootstrapped(lines), f"{path.name} removes PyYAML and never builds it again"
    spec = importlib.util.spec_from_file_location("focused_job_order", REPO / "scripts" / "focused_job.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["focused_job_order"] = module
    spec.loader.exec_module(module)
    sync, bootstrap, tests = module.plan("pytest", "tests/unit/ci_test.py", Path("junit.xml"))
    assert sync[:2] == ["uv", "sync"]
    assert bootstrap == ["bash", "scripts/bootstrap-pyyaml.sh"]
    assert "pytest" in tests


def test_the_documented_app_launch_never_inherits_the_reviewed_pyyaml_find_links():
    """The e2e fixture exports UV_FIND_LINKS; an inherited one would become resolver policy in the managed tool."""
    source = (REPO / "tests" / "e2e" / "docs_test.py").read_text(encoding="utf-8")
    assert 'if not key.startswith(("HZ_", "UV_"))' in source  # the docs environment drops every UV_ variable
    assert "UV_FIND_LINKS" not in source.replace("inherited UV_FIND_LINKS", "")


def test_the_starter_journeys_find_the_reviewed_pyyaml_through_find_links():
    lines = (REPO / "scripts" / "template-test.sh").read_text(encoding="utf-8").splitlines()
    build = next(i for i, line in enumerate(lines) if "bootstrap-pyyaml.sh --wheel-dir" in line)
    launch = next(i for i, line in enumerate(lines) if 'UV_FIND_LINKS="$pyyaml_wheels"' in line)
    wheel = next(i for i, line in enumerate(lines) if "scripts/build.py --wheel" in line)
    setup = next(i for i, line in enumerate(lines) if line.strip() == "make setup")
    assert wheel < build < launch < setup
    assert "scripts/starter-test.py" in "\n".join(lines[launch : launch + 4])
    assert '--core "$core_wheel"' in "\n".join(lines[launch : launch + 4])
    assert '--destination "$starter_destination"' in "\n".join(lines[launch : launch + 4])
    assert 'UV_CACHE_DIR="$starter_destination/uv-cache"' in lines[launch]
    assert 'UV_TOOL_DIR="$starter_destination/uv-tool"' in lines[launch + 1]
    assert "trap on_exit EXIT" in lines
    assert "trap 'on_signal 143' TERM" in lines
    assert 'wait "$starter_pid"' in lines
    assert build < launch
    assert any("unset VIRTUAL_ENV UV_FROZEN" in line for line in lines)
    assert (
        lines[launch].lstrip() == 'export UV_FIND_LINKS="$pyyaml_wheels" UV_CACHE_DIR="$starter_destination/uv-cache"'
    )


def _template_test_fixture(
    tmp_path: Path,
    *,
    make_failure: str = "",
    starter_failure: bool = False,
    starter_ignores_term: bool = False,
) -> dict[str, str]:
    """Provide deterministic external tools for the template shell's overlap and cleanup paths."""
    binary = tmp_path / "bin"
    binary.mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    started = tmp_path / "starter.started"
    child_ready = tmp_path / "starter.child"
    child_stopped = tmp_path / "starter.child-stopped"
    parent_stopped = tmp_path / "starter.parent-stopped"
    starter_args = tmp_path / "starter.args"
    starter_environment = tmp_path / "starter.environment"
    events = tmp_path / "events.log"
    wheel = tmp_path / "heinzel-fixture.whl"
    uv = binary / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if 'scripts/build.py' in args:\n"
        "    if '--out-dir' in args:\n"
        "        out = pathlib.Path(args[args.index('--out-dir') + 1]); out.mkdir(parents=True, exist_ok=True)\n"
        f"        (out / {wheel.name!r}).write_text('fixture')\n"
        "    raise SystemExit(0)\n"
        "if 'copier' in args and 'copy' in args:\n"
        "    destination = pathlib.Path(args[-1]); destination.mkdir(parents=True, exist_ok=True)\n"
        "    (destination / 'pyproject.toml').write_text('\"heinzel==0.9.55\"\\n')\n"
        "    raise SystemExit(0)\n"
        "if 'scripts/starter-test.py' in args:\n"
        f"    pathlib.Path(os.environ['FAKE_STARTER_ARGS']).write_text(json.dumps(args))\n"
        "    names = ('UV_CACHE_DIR', 'UV_TOOL_DIR', 'UV_FIND_LINKS', 'VIRTUAL_ENV', 'UV_FROZEN')\n"
        "    observed = {key: os.environ.get(key) for key in names}\n"
        "    pathlib.Path(os.environ['FAKE_STARTER_ENV']).write_text(json.dumps(observed))\n"
        "    os.execv(sys.executable, [sys.executable, os.environ['FAKE_STARTER']])\n"
        "if args and args[-1].endswith('.whl'):\n"
        "    path = pathlib.Path('pyproject.toml')\n"
        "    local = f'\"heinzel @ {pathlib.Path(args[-1]).as_uri()}\"'\n"
        "    path.write_text(__import__('re').sub(r'\"heinzel==[^\"]*\"', local, path.read_text()))\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(f'unexpected uv command: {args!r}')\n",
        encoding="utf-8",
    )
    uv.chmod(0o700)
    bootstrap = binary / "bash"
    bootstrap.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "if sys.argv[1].endswith('scripts/bootstrap-pyyaml.sh'):\n"
        "    directory = pathlib.Path(sys.argv[sys.argv.index('--wheel-dir') + 1])\n"
        "    directory.mkdir(parents=True, exist_ok=True)\n"
        "    (directory / 'pyyaml.whl').write_text('fixture')\n"
        "else:\n"
        "    os.execv('/bin/bash', ['/bin/bash', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o700)
    make = binary / "make"
    make.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys, time\n"
        "target = sys.argv[-1]\n"
        f"with pathlib.Path({str(events)!r}).open('a') as stream: stream.write(target + '\\n')\n"
        "if target == 'setup':\n"
        "    ready = pathlib.Path(os.environ['STARTER_STARTED'])\n"
        "    deadline = time.monotonic() + 2\n"
        "    while not ready.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(.01)\n"
        "    if not ready.exists(): raise SystemExit('starter did not overlap setup')\n"
        "    pathlib.Path('uv.lock').write_text('fixture')\n"
        "if target == os.environ.get('MAKE_FAILURE'): raise SystemExit(23)\n",
        encoding="utf-8",
    )
    make.chmod(0o700)
    git = binary / "git"
    git.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n", encoding="utf-8")
    git.chmod(0o700)
    child = tmp_path / "child.py"
    child.write_text(
        "import os, pathlib, signal, time\n"
        f"stopped = pathlib.Path({str(child_stopped)!r})\n"
        "def stop(_signum, _frame): stopped.write_text('stopped'); raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        f"pathlib.Path({str(child_ready)!r}).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.05)\n",
        encoding="utf-8",
    )
    starter = tmp_path / "starter.py"
    child_start = (
        "child = None\n"
        if starter_ignores_term
        else "child = subprocess.Popen([sys.executable, child_script], start_new_session=True)\n"
    )
    starter.write_text(
        "import os, pathlib, signal, subprocess, sys, time\n"
        f"started = pathlib.Path({str(started)!r}); parent_stopped = pathlib.Path({str(parent_stopped)!r})\n"
        f"child_script = {str(child)!r}\n"
        "child = None\n"
        "def stop(signum, _frame):\n"
        "    if child is not None:\n"
        "        try: os.killpg(child.pid, signal.SIGTERM)\n"
        "        except ProcessLookupError: pass\n"
        "        try: child.wait(timeout=2)\n"
        "        except subprocess.TimeoutExpired: os.killpg(child.pid, signal.SIGKILL); child.wait()\n"
        "    parent_stopped.write_text('stopped'); raise SystemExit(128 + signum)\n"
        f"signal.signal(signal.SIGTERM, {'signal.SIG_IGN' if starter_ignores_term else 'stop'})\n"
        + child_start
        + "if child is not None:\n"
        + "    ready = pathlib.Path(os.environ['STARTER_CHILD_READY'])\n"
        + "    deadline = time.monotonic() + 2\n"
        + "    while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)\n"
        + "    if not ready.exists(): raise SystemExit('starter child did not start')\n"
        + "started.write_text(str(os.getpid()))\n"
        f"if {starter_failure!r}: stop(15, None)\n"
        "while True: time.sleep(.05)\n",
        encoding="utf-8",
    )
    return {
        **os.environ,
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(temporary),
        "VIRTUAL_ENV": str(tmp_path / "caller-venv"),
        "UV_FROZEN": "1",
        "FAKE_STARTER": str(starter),
        "STARTER_STARTED": str(started),
        "STARTER_CHILD_READY": str(child_ready),
        "STARTER_CHILD_STOPPED": str(child_stopped),
        "STARTER_PARENT_STOPPED": str(parent_stopped),
        "FAKE_STARTER_ARGS": str(starter_args),
        "FAKE_STARTER_ENV": str(starter_environment),
        "MAKE_FAILURE": make_failure,
        "EVENTS": str(events),
        "TEMPLATE_AUDIT": "1",
    }


def test_template_test_overlaps_after_wheel_and_cleans_starter_when_governed_check_fails(tmp_path):
    environment = _template_test_fixture(tmp_path, make_failure="check")
    result = subprocess.run(
        ["/bin/bash", str(REPO / "scripts/template-test.sh"), str(tmp_path / "governed")],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert (tmp_path / "starter.started").is_file()
    assert (tmp_path / "starter.parent-stopped").is_file()
    assert (tmp_path / "starter.child-stopped").is_file()
    args = json.loads((tmp_path / "starter.args").read_text())
    core_wheel = Path(args[args.index("--core") + 1])
    starter_destination = Path(args[args.index("--destination") + 1])
    assert core_wheel.is_file()
    assert core_wheel.name == "heinzel-fixture.whl"
    assert starter_destination != tmp_path / "governed"
    starter_environment = json.loads((tmp_path / "starter.environment").read_text())
    assert starter_environment["UV_CACHE_DIR"] == str(starter_destination / "uv-cache")
    assert starter_environment["UV_TOOL_DIR"] == str(starter_destination / "uv-tool")
    assert starter_environment["VIRTUAL_ENV"] is None
    assert starter_environment["UV_FROZEN"] is None
    assert (Path(starter_environment["UV_FIND_LINKS"]) / "pyyaml.whl").is_file()
    assert f'"heinzel @ {core_wheel.as_uri()}"' in (tmp_path / "governed" / "pyproject.toml").read_text()
    events = (tmp_path / "events.log").read_text().splitlines()
    assert events == ["setup", "fix", "docs-plugins", "check"]


def test_template_test_propagates_starter_failure_after_governed_audit(tmp_path):
    environment = _template_test_fixture(tmp_path, starter_failure=True)
    result = subprocess.run(
        ["/bin/bash", str(REPO / "scripts/template-test.sh"), str(tmp_path / "governed")],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 143, result.stdout + result.stderr
    assert (tmp_path / "starter.parent-stopped").is_file()
    assert (tmp_path / "starter.child-stopped").is_file()
    args = json.loads((tmp_path / "starter.args").read_text())
    assert Path(args[args.index("--core") + 1]).is_file()
    events = (tmp_path / "events.log").read_text().splitlines()
    assert events == ["setup", "fix", "docs-plugins", "check", "test-e2e", "audit"]


def test_template_test_signal_cleans_the_independent_starter_tree(tmp_path):
    environment = _template_test_fixture(tmp_path)
    process = subprocess.Popen(
        ["/bin/bash", str(REPO / "scripts/template-test.sh"), str(tmp_path / "governed")],
        cwd=REPO,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "starter.started").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "starter.started").exists()
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
        assert process.returncode == 143, output
        assert (tmp_path / "starter.parent-stopped").is_file()
        assert (tmp_path / "starter.child-stopped").is_file()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if (tmp_path / "starter.started").exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int((tmp_path / "starter.started").read_text()), signal.SIGTERM)
        if (tmp_path / "starter.child").exists():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(int((tmp_path / "starter.child").read_text()), signal.SIGKILL)


@pytest.mark.timeout(10)
def test_template_test_bounds_cleanup_of_a_nonresponsive_starter(tmp_path):
    environment = _template_test_fixture(tmp_path, make_failure="check", starter_ignores_term=True)
    started_at = time.monotonic()
    result = subprocess.run(
        ["/bin/bash", str(REPO / "scripts/template-test.sh"), str(tmp_path / "governed")],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert (tmp_path / "starter.started").is_file()
    assert time.monotonic() - started_at < 7
    assert not (tmp_path / "starter.parent-stopped").exists()


@pytest.mark.parametrize("makefile", ["Makefile", "template/Makefile"])
def test_install_builds_the_reviewed_pyyaml_after_the_lock_and_before_the_first_sync(makefile):
    """`make setup` is the documented command: it must not leave PyYAML to a source build on Python 3.15."""
    text = (REPO / makefile).read_text(encoding="utf-8")
    recipe = text.split("\ninstall:", 1)[1].split("\n\n", 1)[0]
    lock = recipe.index("uv lock --quiet")
    pyyaml = recipe.index("pyyaml")
    sync = min(recipe.index(token) for token in ("uv sync", "build.py sync") if token in recipe)
    assert lock < pyyaml < sync


def test_the_setup_uv_guard_matches_this_checkouts_lock():
    """The action builds PyYAML only where uv.lock says >=3.15; a changed floor must not switch that off silently."""
    action = yaml.safe_load((REPO / ".github" / "actions" / "setup-uv" / "action.yml").read_text(encoding="utf-8"))
    step = next(item for item in action["runs"]["steps"] if "bootstrap-pyyaml.sh" in item.get("run", ""))
    pattern = re.search(r"grep -q '([^']+)' uv\.lock", step["run"])
    assert pattern, "the guard must stay a grep on uv.lock"
    assert re.search(pattern[1], (REPO / "uv.lock").read_text(encoding="utf-8"), re.MULTILINE)


def test_the_readme_install_environment_carries_no_github_token():
    source = (REPO / "tests" / "e2e" / "docs_test.py").read_text(encoding="utf-8")
    assert "if key not in TOKENS" in source
    assert "launch_documented_app(block, launch_env," in source


@pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
@pytest.mark.parametrize("path", ["README.md", "docs/guide.md", "plugins/example/config.py"])
def test_direct_full_workload_includes_core_for_every_change(tmp_path, ci_paths, event, path):
    outputs = _changes_outputs(tmp_path, ci_paths, event, path + "\n", release=True)
    assert {"core=true", "site_only=false", "macos_full=true", "contracts=true", "notifier=true"} <= outputs


DIRECT_REQUIRED = [
    "admission",
    "changes",
    "lint",
    "secrets",
    "setup",
    "audit",
    "test",
    "coverage",
    "perf",
    "e2e",
    "template",
    "notifier",
    "macos-specific",
    "electron",
    "build",
    "core-wheel",
    "local-plugin-plan",
    "consumer-plan",
    "consumer-check",
    "quarantine-registry",
]


@pytest.mark.parametrize("job", [None, *DIRECT_REQUIRED, "commits", "dependency-review", "local-plugin-check"])
@pytest.mark.parametrize("outcome", ["skipped", "failure", "cancelled"])
def test_direct_ci_cannot_pass_with_a_missing_required_job(job, outcome):
    jobs = _ci_jobs()
    step = next(
        step for step in jobs["ci-ok"]["steps"] if step.get("name") == "Require the complete direct CI workload"
    )
    results = {name: {"result": "success"} for name in jobs["ci-ok"]["needs"]}
    if job is not None:
        results[job]["result"] = outcome
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={"PATH": os.environ["PATH"], "EVENT": "pull_request", "PLUGINS": "True", "RESULTS": json.dumps(results)},
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(30),
    )
    assert (result.returncode == 0) == (job is None)
    if job is not None:
        assert f"Required full CI job did not pass: {job}" in result.stdout


def test_direct_coverage_has_no_write_token_and_enforces_its_floor():
    coverage = _ci_jobs()["coverage"]
    assert coverage["permissions"] == {"contents": "read"}
    report = next(
        step for step in coverage["steps"] if step.get("name") == "Enforce coverage and record the measured total"
    )
    assert "--fail-under=90" in report["run"]
    assert "> coverage-total.txt" in report["run"]


def test_release_prs_keep_diagnostics_and_apple_ci_is_required():
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["jobs"]
    for name in ("setup", "test"):
        assert jobs[name]["strategy"]["fail-fast"] is False  # every red cell shows in one run
    for name, job in jobs.items():
        if name.startswith("stop-after-"):
            assert "!startsWith(github.head_ref, 'release/')" in job["if"]
    steps = jobs["macos-specific"]["steps"]
    assert any(step.get("run") == "make test-apple-ci" for step in steps)
    assert any(step.get("run") == "make build-apple-ci" for step in steps)
    assert "macos-specific" in jobs["ci-ok"]["needs"]
    recipe = (REPO / "Makefile").read_text()
    assert "CODE_SIGNING_ALLOWED=NO" in recipe
    assert "scripts/check_apple_ci.py reports/apple-ci.xml" in recipe


def test_pull_requests_do_not_wait_for_lint_before_the_template_job_and_never_stop_at_the_first_red_cell():
    jobs = _ci_jobs()
    assert jobs["template"]["needs"] == "changes"
    for name in ("setup", "test"):
        assert jobs[name]["strategy"]["fail-fast"] is False


_IF_TERM = re.compile(r"needs\.changes\.outputs\.(\w+)|github\.event_name|'[^']*'|==|!=|&&|\|\||[()]|\s+")


def _evaluate_if(condition: str, event: str, outputs: dict[str, str]) -> bool:
    """Evaluate the plain `&&`, `||`, `==` and `!=` conditions of a job against computed `changes` outputs."""
    pieces = []
    for match in _IF_TERM.finditer(condition):
        token = match[0]
        if match[1]:
            pieces.append(repr(outputs.get(match[1], "")))
        elif token == "github.event_name":
            pieces.append(repr(event))
        else:
            pieces.append({"&&": " and ", "||": " or "}.get(token, token))
    assert "".join(match[0] for match in _IF_TERM.finditer(condition)) == condition, condition
    return bool(eval("".join(pieces)))  # noqa: S307 - only repo-owned workflow conditions, no builtins  # nosec B307


def test_the_nightly_selects_and_runs_the_full_macos_matrix_end_to_end(tmp_path, ci_paths):
    """Compute what the `changes` job selects on `schedule`, then which jobs start and on which operating systems."""
    jobs = _ci_jobs()
    # the steps themselves are pinned by test_changed_paths_event_policy; here the selection is computed
    raw = _changes_outputs(tmp_path, ci_paths, "schedule", "", release=True)  # `full` selects true, as in the workflow
    outputs = dict(line.split("=", 1) for line in raw)
    outputs["full_ci"] = "true"
    outputs["scheduled_maintenance"] = "true"
    required = ("setup", "test", "e2e", "notifier", "macos-specific", "electron", "coverage", "template")
    for name in required:
        assert _evaluate_if(jobs[name]["if"], "schedule", outputs), name
    for name in ("setup", "e2e", "electron"):
        assert "macos-latest" in _oses(jobs[name]["strategy"]["matrix"]["os"], "schedule")
    assert len(_macos_include(jobs["test"]["strategy"]["matrix"], "schedule", full=True)) == 2
    complete = next(step for step in jobs["ci-ok"]["steps"] if step.get("name", "").startswith("Require the complete"))
    assert "if" not in complete  # the nightly requires the whole direct workload, macOS lanes included
    for name in ("setup", "test", "e2e", "notifier", "macos-specific", "electron"):
        assert name in complete["run"]


MACOS_JOBS = ("notifier", "macos-specific")
LINUX_ONLY_JOBS = (
    "lint", "audit", "coverage", "perf", "site-tests", "template", "core-wheel", "local-plugin-plan", "consumer-plan",
    "release-candidate-source",
)  # fmt: skip
MAIN_MACOS_GROUP = "ci-main-macos"


def _lane_outputs(tmp_path, event: str, lane: str = "", ref: str = "refs/heads/main") -> dict[str, str] | None:
    step = next(step for step in _ci_jobs()["changes"]["steps"] if step.get("id") == "lane")
    output = tmp_path / "lane-output"
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={
            "EVENT": event,
            "LANE": lane,
            "GITHUB_REPOSITORY": "helmut-hoffer-von-ankershoffen/heinzel",
            "GITHUB_REF": ref,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(30),
    )
    if result.returncode:
        return None
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


@pytest.mark.parametrize(
    ("event", "lane", "selected", "excluded"),
    [
        ("push", "", "linux", ["macos-latest"]),
        ("workflow_dispatch", "macos", "macos", ["ubuntu-24.04", "ubuntu-24.04-arm"]),
        ("workflow_dispatch", "all", "all", []),
        ("workflow_dispatch", "", "all", []),
        ("pull_request", "", "all", []),  # release/* candidates included: they keep the whole matrix
        ("schedule", "", "all", []),
    ],
)
def test_main_lanes_split_linux_pushes_from_the_dispatched_macos_run(tmp_path, event, lane, selected, excluded):
    outputs = _lane_outputs(tmp_path, event, lane)
    assert outputs is not None
    assert outputs["lane"] == selected
    assert [row["os"] for row in json.loads(outputs["exclude"])] == excluded


def test_the_macos_lane_refuses_any_ref_but_main(tmp_path):
    assert _lane_outputs(tmp_path, "workflow_dispatch", "macos", ref="refs/heads/release/v1.2.3") is None


def test_main_lanes_gate_every_platform_job():
    jobs = _ci_jobs()
    for name in LINUX_ONLY_JOBS:
        assert jobs[name]["if"].split(" # ")[0].endswith(" && needs.changes.outputs.lane != 'macos'"), name
    for name in MACOS_JOBS:
        assert jobs[name]["if"].split(" # ")[0].endswith(" && needs.changes.outputs.lane != 'linux'"), name
    for name in ("setup", "test", "e2e", "electron", "quarantine"):
        assert jobs[name]["strategy"]["matrix"]["exclude"] == (
            "${{ fromJSON(needs.changes.outputs.lane_exclude || '[]') }}"
        ), name
    assert jobs["test"]["strategy"]["matrix"]["include"].startswith(
        "${{ fromJSON(needs.changes.outputs.lane == 'linux' && '[]' || "
    )
    assert _macos_include(jobs["test"]["strategy"]["matrix"], "workflow_dispatch", full=True, lane="macos") == [
        {"os": "macos-latest", "python": "3.15", "levels": level} for level in ("unit", "integration")
    ]
    assert "lane" not in jobs["build"]["if"]  # the macOS lane builds the wheel with the notifier it just built
    assert "lane" not in jobs["macos-smoke"]["if"]  # pull requests only


def test_a_push_to_main_dispatches_the_coalesced_macos_run():
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    job = jobs["macos-main"]
    assert job["permissions"] == {"actions": "write"}
    assert job["needs"] == "changes"
    assert job["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.changes.outputs.lane == 'linux'"
    )
    assert [step["run"] for step in job["steps"]] == [
        'gh workflow run ci.yml --repo "$GITHUB_REPOSITORY" --ref main -f lane=macos'
    ]
    assert "macos-main" in jobs["ci-ok"]["needs"]
    lane = workflow[True]["workflow_dispatch"]["inputs"]["lane"]  # YAML 1.1 reads the key `on` as True
    assert (lane["type"], lane["options"], lane["default"]) == ("choice", ["all", "macos"], "all")
    group = workflow["concurrency"]["group"]
    assert f"inputs.lane == 'macos' && '{MAIN_MACOS_GROUP}' ||" in group
    assert "github.ref == 'refs/heads/main' && github.sha || github.ref" in group  # Linux keeps one group per SHA
    # cancel-in-progress stays pull-request-only: the macOS group keeps its running run and the newest pending one
    assert workflow["concurrency"]["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' && !startsWith(github.head_ref, 'release/') }}"
    )
    assert "inputs.lane == 'macos' && 'CI macOS main '" in workflow["run-name"]


def test_ci_ok_requires_each_lane_its_own_jobs():
    jobs = _ci_jobs()
    complete = next(step for step in jobs["ci-ok"]["steps"] if step.get("name", "").startswith("Require the complete"))
    lanes = dict(re.findall(r"(?m)^ +(linux|macos)\) required=\(([^)]*)\)", complete["run"]))
    linux, macos = (set(lanes[name].split()) for name in ("linux", "macos"))
    assert not linux & set(MACOS_JOBS)
    assert "macos-main" in linux
    assert {"setup", "test", "e2e", "electron", "build", *MACOS_JOBS} <= macos
    assert not macos & set(LINUX_ONLY_JOBS)
    assert complete["env"]["LANE"] == "${{ needs.changes.outputs.lane }}"


def test_the_plugin_template_never_sees_the_main_lanes():
    generated = _template_sync_module().ci_workflow()
    assert "lane" not in generated
    assert MAIN_MACOS_GROUP not in generated
    assert "macos-main" not in generated


# Path selection on pull requests (the paths step reads the real release flag, not an always-true one).
_NATIVE_OFF = {"notifier=false", "contracts=false", "electron=false", "macos_full=false"}


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ("docs/guides/install.md\n", {"site_only=true", "core=true"} | _NATIVE_OFF),  # prose: site-tests only
        ("web/src/components/Footer.tsx\n", {"site_only=false", "macos_full=false", "contracts=false"}),
        ("src/heinzel/kernel/_exposure_x.py\n", {"macos_full=true", "contracts=true"}),
        ("src/heinzel/testing.py\n", {"contracts=true"}),
        ("src/heinzel/conformance.py\n", {"contracts=true"}),
        ("src/heinzel/modules/team/_x.py\n", {"contracts=true"}),
        ("pyproject.toml\n", {"contracts=true", "notifier=true"}),
        ("uv.lock\n", {"contracts=true"}),
        ("weird/unknown.bin\n", {"contracts=true"}),
        ("docs/a.md\nweird/unknown.bin\n", {"contracts=true"}),
        ("web/src/plugins/x.tsx\n", {"contracts=true"}),
        ("", {"contracts=true", "notifier=true", "core=true"}),
        ("plugins/example/file.py\n", {"core=false", "plugins=true", "notifier=false", "contracts=false"}),
    ],
)
def test_an_ordinary_pull_request_selects_only_what_its_paths_need(tmp_path, ci_paths, changed, expected):
    assert expected <= _changes_outputs(tmp_path, ci_paths, "pull_request", changed, release=False)


def _ci_ok_run(run: str, **env: str) -> tuple[int, str]:
    jobs = _ci_jobs()
    step = next(s for s in jobs["ci-ok"]["steps"] if s.get("name") == "Require the complete direct CI workload")
    ran = [*run.split(), "coverage-floor", "sonar-ratings"]  # both judge every pull request, whatever it selected
    results = {name: {"result": "success" if name in ran else "skipped"} for name in jobs["ci-ok"]["needs"]}
    base = {
        "PATH": os.environ["PATH"],
        "RESULTS": json.dumps(results),
        "EVENT": "pull_request",
        "FULL_RELEASE": "false",
    }
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(30),
    )
    return result.returncode, result.stdout


_OFF = {"NOTIFIER": "false", "ELECTRON": "false", "MACOS_FULL": "false", "CONTRACTS": "false", "PLUGIN_PATHS": "false"}
_PROSE = {**_OFF, "SITE_ONLY": "true", "CORE": "true", "PLUGINS": "False"}
_PROSE_JOBS = ["admission", "changes", "lint", "secrets", "setup", "audit", "site-tests", "build", "core-wheel"]
PROSE_RUN = " ".join([*_PROSE_JOBS, "quarantine-registry", "commits", "dependency-review"])


def test_a_prose_pull_request_passes_ci_ok_with_site_tests_alone():
    assert _ci_ok_run(PROSE_RUN, **_PROSE)[0] == 0


def test_a_prose_pull_request_cannot_pass_without_its_site_tests():
    code, out = _ci_ok_run(PROSE_RUN.replace("site-tests", ""), **_PROSE)
    assert code != 0
    assert "site-tests" in out


def test_a_plugins_only_pull_request_needs_no_core_job():
    run = "admission changes secrets quarantine-registry commits dependency-review local-plugin-plan"
    env = {**_OFF, "SITE_ONLY": "false", "CORE": "false", "PLUGIN_PATHS": "true", "PLUGINS": "False"}
    assert _ci_ok_run(run, **env)[0] == 0
    assert _ci_ok_run(run.replace("local-plugin-plan", ""), **env)[0] != 0


def test_a_release_pull_request_and_main_still_require_everything():
    assert _ci_ok_run(PROSE_RUN, **_PROSE, FULL_RELEASE="true")[0] != 0
    assert _ci_ok_run(PROSE_RUN, **_PROSE, EVENT="push")[0] != 0


def test_the_macos_jobs_wait_for_a_native_path_on_ordinary_pull_requests():
    condition = _ci_jobs()["macos-specific"]["if"]
    base = {"full_ci": "true", "core": "true", "site_only": "false", "lane": "all", "surface_only": "false"}
    assert not _evaluate_if(condition, "pull_request", {**base, "macos_full": "false", "full_release": "false"})
    assert _evaluate_if(condition, "pull_request", {**base, "macos_full": "true", "full_release": "false"})
    assert _evaluate_if(condition, "pull_request", {**base, "macos_full": "false", "full_release": "true"})
    assert _evaluate_if(condition, "push", {**base, "macos_full": "false", "full_release": "false"})


def test_a_failed_diff_keeps_the_consumers_and_the_notifier_on(tmp_path, ci_paths):
    outputs = _changes_outputs(tmp_path, ci_paths, "pull_request", "", git_exit=1)
    assert {"contracts=true", "notifier=true", "core=true"} <= outputs


def test_a_release_branch_that_is_not_the_candidate_runs_everything(tmp_path, ci_paths, monkeypatch):
    script, directory = ci_paths
    output = tmp_path / "release-output"
    subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": f"{directory / 'bin'}:/usr/bin:/bin", "EVENT": "pull_request", "FULL_RELEASE": "false",
            "HEAD_REF": "release/foo", "BASE": "base", "GITHUB_OUTPUT": str(output),
            "CHANGED_PATHS": "docs/guides/install.md\n", "DIFF_EXIT": "0",
        },
        cwd=directory, check=True, timeout=scaled(30),
    )  # fmt: skip
    assert {"site_only=false", "macos_full=true", "contracts=true", "notifier=true"} <= set(
        output.read_text().splitlines()
    )


def test_ci_ok_requires_everything_on_a_release_branch_and_keeps_the_plugin_check():
    assert _ci_ok_run(PROSE_RUN, **_PROSE, HEAD_REF="release/foo")[0] != 0
    run = "admission changes secrets quarantine-registry commits dependency-review local-plugin-plan"
    env = {**_OFF, "SITE_ONLY": "false", "CORE": "false", "PLUGIN_PATHS": "true", "PLUGINS": "True"}
    assert _ci_ok_run(run + " local-plugin-check", **env)[0] == 0
    assert _ci_ok_run(run, **env)[0] != 0


def _evaluate_job_if(expression, event, outputs, results, ref="refs/heads/main"):
    """Evaluate one job `if` against a selection; only the expression forms ci.yml uses are supported."""
    text = re.sub(r"^\$\{\{\s*|\s*\}\}$", "", str(expression).strip())
    text = re.sub(r"\s+#.*$", "", text)
    text = re.sub(r"needs\.([\w-]+)\.outputs\.(\w+)", r'N("\1","\2")', text)
    text = re.sub(r"needs\.([\w-]+)\.result", r'R("\1")', text)
    text = text.replace("github.event_name", "EV").replace("github.ref", "REF").replace("github.head_ref", "''")
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    scope = {
        "N": lambda job, key: outputs.get(job, {}).get(key, ""),
        "R": results,
        "EV": event,
        "REF": ref,
        "cancelled": lambda: False,
        "startsWith": lambda *_: False,
        "failure": lambda: False,
        "__builtins__": {},
    }
    return bool(eval(text, scope))  # noqa: S307 - the expression comes from the repository's own workflow


def _selection_run(  # noqa: PLR0913 - independent event, path and job fixture inputs
    tmp_path, ci_paths, event, changed, *, release=False, plugins="True", skipped_job=None
):
    """Run the real selector, evaluate every job `if` on it, then run CI OK's own script on the resulting results."""
    jobs = _ci_jobs()
    selected = dict(
        line.split("=", 1)
        for line in _changes_outputs(tmp_path, ci_paths, event, changed, release=release)
        if "=" in line
    )
    lane = "linux" if event == "push" else "all"
    outputs = {
        "changes": {
            **selected,
            "lane": lane,
            "full_ci": "true",
            "full_release": str(release).lower(),
            "focused_mode": "",
            "scheduled_maintenance": "true" if event == "schedule" else "",
        },
        "admission": {"full": "true", "base": ""},
        "local-plugin-plan": {"selected": plugins},
        "consumer-plan": {"selected": "True"},
        "quarantine-plan": {"selected": "true"},
    }
    cache: dict[str, str] = {}

    def result(job):
        if job not in cache:
            cache[job] = (
                "success"
                if job not in jobs or "if" not in jobs[job] or _evaluate_job_if(jobs[job]["if"], event, outputs, result)
                else "skipped"
            )
        return cache[job]

    needs = {job: {"result": result(job), "outputs": outputs.get(job, {})} for job in jobs["ci-ok"]["needs"]}
    if skipped_job is not None:
        assert needs[skipped_job]["result"] == "success"
        needs[skipped_job]["result"] = "skipped"
    step = next(
        s for s in jobs["ci-ok"]["steps"] if s.get("name", "").startswith("Require the complete direct CI workload")
    )
    changes = outputs["changes"]
    env = {
        "PATH": os.environ["PATH"],
        "RESULTS": json.dumps(needs),
        "EVENT": event,
        "SURFACE_ONLY": changes["surface_only"],
        "PLUGINS": plugins,
        "FULL_RELEASE": changes["full_release"],
        "HEAD_REF": "docs/x",
        "SITE_ONLY": changes["site_only"],
        "CORE": changes["core"],
        "NOTIFIER": changes["notifier"],
        "ELECTRON": changes["electron"],
        "MACOS_FULL": changes["macos_full"],
        "CONTRACTS": changes["contracts"],
        "PLUGIN_PATHS": changes["plugins"],
        "LANE": lane,
    }
    return subprocess.run(
        ["bash", "-c", step["run"]], env=env, capture_output=True, text=True, timeout=scaled(30), check=False
    )


SELECTION_PATHS = [
    "AGENTS.md\n",  # a docs-only change: site_only and surface_only together (PR 955)
    "docs/guides/install.md\nREADME.md\n",
    "docs/install.sh\n",
    "web/src/components/Footer.tsx\n",
    "src/heinzel/__init__.py\n",
    "src/heinzel/modules/team/_render.py\n",
    "plugins/heinzel-x/src/plugin.py\n",
    "desktop/src/main.js\n",
    ".github/workflows/ci.yml\n",
    "apple/App/Info.plist\n",
    "tests/unit/a_test.py\n",
    "CHANGELOG.md\n",
    "",
]


@pytest.mark.parametrize("plugins", ["True", "False"])
@pytest.mark.parametrize("release", [False, True])
@pytest.mark.parametrize("changed", SELECTION_PATHS)
@pytest.mark.parametrize("event", ["pull_request", "push", "schedule", "workflow_dispatch"])
def test_ci_ok_requires_only_jobs_the_selection_runs(  # noqa: PLR0913,PLR0917 - one parametrized axis each
    tmp_path, ci_contexts_paths, event, changed, release, plugins
):
    """CI OK and the job conditions can never disagree: every job it requires is one this selection ran."""
    run = _selection_run(tmp_path, ci_contexts_paths, event, changed, release=release, plugins=plugins)
    assert run.returncode == 0, run.stderr + run.stdout


def test_ci_ok_requires_macos_dispatch_on_prose_only_main_push(tmp_path, ci_contexts_paths):
    run = _selection_run(tmp_path, ci_contexts_paths, "push", "README.md\n", plugins="False", skipped_job="macos-main")
    assert run.returncode == 1
    assert "Required full CI job did not pass: macos-main" in run.stdout
