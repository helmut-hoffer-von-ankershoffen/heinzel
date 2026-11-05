"""CI runs what developers run locally."""

import hashlib
import importlib.util
import io
import json
import os
import re
import runpy
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def test_installed_browser_service_journey_keeps_cold_starts_out_of_browser_shards():
    recipe = "show-browser-allocation:\n\t@printf '%s\\n' '$(E2E_WEB)' '$(E2E_WEB_1)' '$(E2E_WEB_2)'"
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", "Makefile", "-f", "-", "show-browser-allocation"],
        cwd=REPO,
        input=recipe + "\n",
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    browser, first, second = [set(line.split()) for line in result.stdout.splitlines()]
    journey = "tests/e2e/ui_service_browser_test.py"
    assert journey not in browser
    assert journey not in first | second
    assert "tests/e2e/ui_service_test.py" not in browser
    assert first.isdisjoint(second)
    assert first | second == browser
    # The mounted UI sweep already contains roughly half the observed browser phase time.
    assert first == {"tests/e2e/web_ui_test.py"}
    assert second == browser - first


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
        "python3 -I scripts/release_candidate.py": "selects or records release source",
        "uv run --frozen --no-sync --no-build python scripts/ci_node.py": (
            "exports the already installed locked runtime through GitHub's step-local GITHUB_PATH setup contract"
        ),
        "sudo sed -i 's|http://azure\\.archive\\.ubuntu\\.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' "
        "/etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources": (
            "selects the same signed primary Ubuntu archive as Docs before browser dependency installation"
        ),
        "uv run --no-build --script scripts/main_batch.py": "read-only bounded admission of an explicit main batch",
        "echo 'The validated aggregate base": "records that the final run consumed the aggregate base",
        'echo "Full CI is recorded': "links the intermediate admission to its exact final run",
        "uv run --no-project --frozen --no-build --python 3.14 python scripts/check_confidentiality.py --require": (
            "the secret must be configured in this repo"
        ),
        "unzip -l ": "asserts what the built wheel contains",
        'changed="$(git diff --name-only': "decides from the changed paths whether a pull request builds the macOS app",
        "if compgen -G ": "selects the built core candidate or an independently released artifact for source plugins",
        "for result in $RESULTS": "the CI OK gate aggregates the results of the other jobs",
        "uv run --no-build coverage report --format=total": "hands the measured total to the merge gate",
        "tar -czf notifier.tar.gz": "packs the app the macOS job built, with its executable bit, for the build job",
        "tar -xzf notifier.tar.gz": "unpacks Heinzel Notifier into the package before the wheel is built",
        'gh run cancel "$RUN_ID"': "a failed cheap check cancels the rest of its own pull request run",
    },
    "merge-gate.yml": {
        "uv run --no-build --frozen --script scripts/merge_gate.py": "judges by public APIs, from the base branch",
    },
    "acceptance-sharded.yml": {
        "uv python install 3.13": "the isolated acceptance study interpreter",
        '"$(uv python find 3.13)" -I scripts/acceptance/': "runs or reduces the isolated acceptance study",
    },
    "acceptance.yml": {
        "uv python install 3.13": "the interpreter the harness runs on",
        '"$(uv python find 3.13)" -I scripts/acceptance/': (
            "installs the published core with the public commands on a hosted runner and writes the receipt"
        ),
    },
    "docs.yml": {
        "sudo sed -i ": "selects the primary signed Ubuntu archive before browser dependency installation",
        "sudo apt-get update": "fonts for the terminal recordings, from the runner's packages",
        "uv run --quiet --frozen --no-build --group docs playwright install-deps": (
            "Chromium's system libraries, from the runner"
        ),
    },
    "publish.yml": {
        'test "v$(uv version --short)" = "$TAG"': "refuses a tag that doesn't match the project version",
        "uv run --no-project --frozen --no-build --python 3.14 python scripts/check_confidentiality.py": (
            "scans the tagged tree with the term list from the secret"
        ),
        'name="$(uv version': "exports the release's CycloneDX bill of materials",
        "uv run --no-project --frozen --no-build --python 3.14 python scripts/release_notes.py": (
            "writes the release notes"
        ),
        'cp "$BUNDLE"': "attaches the provenance attestation and creates the GitHub release",
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
        '[[ "$TAG" =~': "binds publishing to the exact tag and main ancestry before build tooling",
        "gh api ": "verifies the published GitHub release and exports its exact source commit",
        "uv run --no-project --frozen --no-build --python 3.14 python scripts/check_confidentiality.py --require": (
            "requires the configured release term list"
        ),
        "tar -czf notifier.tar.gz": "preserves the notifier executable bits and signature across runners",
        "tar -xzf notifier.tar.gz": "restores the notifier from the same validated tag",
        "uv build --package heinzel": "builds only the registered core project, never unpublished workspace plugins",
        "uv run --no-project --frozen --no-build --python 3.14 python scripts/pypi_release.py artifacts": (
            "checks core metadata and complete packaged payloads"
        ),
        "python3 - <<'PY'": "rechecks hashes without checking out or running project code in the OIDC job",
    },
    "release.yml": {
        'test "$REF" = refs/heads/main': "restores an original plugin artifact set with the pinned toolkit",
        'test "$PUBLISH" != true || test "$REF" = refs/heads/main': (
            "runs the owning Make commands' same toolkit checks, build, audit and acceptance without core work"
        ),
        'uv run --no-project --frozen --no-build --python 3.14 python scripts/plugin_tooling.py --toolkit "$TOOLING" '
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
        'files="$(git diff': "picks the review tier from the changed files",
        "uv run --no-project --frozen --no-build --python 3.14 python scripts/review_outcome.py": (
            "judges the review attempt; the job has no project environment"
        ),
        'gh api "repos/${GITHUB_REPOSITORY}/check-runs"': "reports a review that could not run as a neutral check",
        'if [[ "$VERDICT" == reviewed ]]': "the review check passes only on a review",
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
    unreviewed = "uv run --no-project --frozen --no-build --python 3.14 python scripts/unreviewed.py"
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


def test_every_main_commit_gets_its_own_ci_run_while_pull_requests_cancel_superseded_runs():
    concurrency = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())["concurrency"]

    def group(ref: str, sha: str) -> str:
        # Evaluate the one expression shape the group uses: `<cond> && github.sha || github.ref`.
        expr = concurrency["group"]
        assert "github.ref == 'refs/heads/main' && github.sha || github.ref" in expr
        return f"{ref}:{sha}" if ref == "refs/heads/main" else ref

    assert group("refs/heads/main", "a") != group("refs/heads/main", "b")
    assert group("refs/pull/1/merge", "a") == group("refs/pull/1/merge", "b")
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_source_plugin_audits_receive_authoritative_repository_visibility():
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    assert workflow["jobs"]["local-plugin-check"]["env"]["PLUGIN_PRIVATE"] == "${{ github.event.repository.private }}"


def test_managed_backend_verification_gates_ci_without_leaking_into_plugin_templates():
    """The source-only native owner is checked by required CI; plugins never install its private Node toolchain."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert "lint" in jobs["ci-ok"]["needs"]
    assert any(step.get("run") == "make managed-backend-setup managed-backend-check" for step in jobs["lint"]["steps"])
    runs = [step.get("run") for step in jobs["lint"]["steps"]]
    assert runs.index("make managed-backend-setup managed-backend-check") < runs.index("make typecheck")
    for name in ("Makefile", ".github/workflows/ci.yml.jinja"):
        assert "managed-backend" not in (REPO / "template" / name).read_text()


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
    assert values["core"] == str(batch_base or not plugin_only).lower()
    assert values["plugins"] == str(plugin_only).lower()
    assert values["contracts"] == str(batch_base or not plugin_only).lower()
    if plugin_only and not batch_base:
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


def test_release_docs_only_publish_existing_recordings_even_when_the_caller_was_a_push():
    release = yaml.safe_load((REPO / ".github/workflows/release-tag.yml").read_text(encoding="utf-8"))
    docs = yaml.safe_load((REPO / ".github/workflows/docs.yml").read_text(encoding="utf-8"))
    assert release["jobs"]["docs"].get("with", {}).get("site_only") is True
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
    assert '--intro "${INTRO:-}"' in script
    release = yaml.safe_load((REPO / ".github" / "workflows" / "prepare-release.yml").read_text(encoding="utf-8"))
    assert "intro" in release[True]["workflow_dispatch"]["inputs"]  # YAML 1.1 reads the key `on` as True
    step = next(s for s in release["jobs"]["pull-request"]["steps"] if "release.sh" in s.get("run", ""))
    assert not any("gh pr merge" in s.get("run", "") for s in release["jobs"]["pull-request"]["steps"])
    assert step["env"]["INTRO"] == "${{ inputs.intro }}"
    assert (
        REPO / "template" / "scripts" / "changelog_intros.py.jinja"
    ).resolve() == REPO / "scripts" / "changelog_intros.py"


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
    assert "\nuv lock --quiet\nmake setup\n" not in script, "a fresh render must run the documented make setup unlocked"
    assert "[[ ! -e uv.lock ]]" in script
    ci = "\n".join(commands(REPO / ".github" / "workflows" / "ci.yml"))
    assert "make template-test AUDIT=1" in ci


def test_claude_review_skips_events_sent_by_bots():
    """The action refuses bot-triggered runs (update-prs merges, release pull requests), so the job must not start."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    condition = spec["jobs"]["attempt"]["if"]
    assert spec["jobs"]["review"]["needs"] == "attempt"
    for clause in (
        "vars.CLAUDE_REVIEW == 'true'",
        "github.event.sender.type != 'Bot'",
        "!github.event.pull_request.draft",
        "github.event.pull_request.head.repo.full_name == github.repository",
    ):
        assert clause in condition


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
    monkeypatch.setattr("shutil.which", lambda _name: "/isolated/uv")
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


def test_claude_review_prefers_the_api_key():
    """Each review step authenticates with ANTHROPIC_API_KEY and uses the subscription token only without one."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    actions = [step for step in spec["jobs"]["attempt"]["steps"] if "claude-code-action" in step.get("uses", "")]
    assert actions
    for step in actions:
        assert step["with"]["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"
        assert step["with"]["claude_code_oauth_token"].startswith("${{ secrets.ANTHROPIC_API_KEY == '' && ")


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


def _macos_include(matrix: dict, event: str, full: bool, *, release: bool = False) -> list[dict]:
    expression = re.fullmatch(r"\$\{\{ fromJSON\((.*)\) \}\}", matrix["include"].strip())
    assert expression
    context = {
        "github.event_name": event,
        "needs.changes.outputs.macos_full": str(full).lower(),
        "needs.changes.outputs.full_release": str(release).lower(),
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


@pytest.mark.parametrize(
    ("event", "full", "pythons"),
    [
        ("pull_request", False, []),
        ("pull_request", True, []),
        ("push", False, ["3.12", "3.13", "3.14", "3.15"]),
        ("push", True, ["3.12", "3.13", "3.14", "3.15"]),
        ("schedule", False, ["3.12", "3.13", "3.14", "3.15"]),
        ("schedule", True, ["3.12", "3.13", "3.14", "3.15"]),
        ("workflow_dispatch", False, ["3.12", "3.13", "3.14", "3.15"]),
        ("workflow_dispatch", True, ["3.12", "3.13", "3.14", "3.15"]),
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
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    script = next(step["run"] for step in jobs["changes"]["steps"] if step.get("id") == "paths")
    directory = tmp_path_factory.mktemp("ci-paths")
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
            "PATH": f"{directory / 'bin'}:/usr/bin:/bin",
            "EVENT": event,
            "FULL_RELEASE": str(release).lower(),
            "BASE": "base",
            "GITHUB_OUTPUT": str(output),
            "CHANGED_PATHS": changed,
            "DIFF_EXIT": str(git_exit),
        },
        cwd=directory,
        check=True,
        timeout=4,
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
        ("src/heinzel/kernel/_contributions.py\n", "false"),
        ("src/heinzel/heads/_tool_install.py\n", "true"),
        ("src/heinzel/heads/web/__init__.py\n", "true"),
        ("src/heinzel/kernel/_session_io.py\n", "true"),
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
        ("src/heinzel/kernel/_installation_mutations.py\n", "true"),
        ("src/heinzel/modules/ui/_service.py\n", "true"),
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
        ("push", "src/heinzel/__init__.py\n", "false"),
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
def test_electron_runs_for_its_paths_on_a_push_and_always_on_schedule_and_dispatch(
    tmp_path, ci_paths, event, changed, electron
):
    assert f"electron={electron}" in _changes_outputs(tmp_path, ci_paths, event, changed)


def test_the_test_matrix_keeps_the_whole_output_of_a_failure():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
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
    for name in ("test", "coverage", "perf", "e2e"):
        assert jobs[name]["if"] == "needs.changes.outputs.core == 'true' && needs.changes.outputs.site_only != 'true'"
    for name in ("lint", "build"):
        assert "site_only" not in jobs[name]["if"]
    assert jobs["site-tests"]["if"] == "needs.changes.outputs.site_only == 'true'"
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


def _ci_jobs() -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]


def _oses(value: str | list[str], event: str, *, macos_full: bool = False, release: bool = False) -> list[str]:
    """The matrix values for an event, from a plain list or a pull-request conditional."""
    if isinstance(value, list):
        return value
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
    for name in ("setup", "e2e", "electron"):
        assert "needs.changes.outputs.macos_full != 'true'" in jobs[name]["strategy"]["matrix"]["os"]
    assert "needs.changes.outputs.macos_full" not in jobs["test"]["strategy"]["matrix"]["include"]


def test_a_pull_request_runs_one_small_macos_job_and_main_runs_the_full_macos_suite():
    jobs = _ci_jobs()
    on_pull_request, on_main = set(), set()
    for name, job in jobs.items():
        runs_on = str(job.get("runs-on", ""))
        matrix = job.get("strategy", {}).get("matrix")
        oses = matrix.get("os") if isinstance(matrix, dict) else None  # a planned matrix lists plugins, on Linux
        for event, found in (("pull_request", on_pull_request), ("push", on_main)):
            if event == "pull_request" and not _runs_on_pull_request(job):
                continue
            if event == "push" and "github.event_name == 'pull_request'" in str(job.get("if", "")):
                continue
            if "macos" in runs_on or (oses is not None and any("macos" in os for os in _oses(oses, event))):
                found.add(name)
    include = jobs["test"]["strategy"]["matrix"]["include"]
    assert "github.event_name == 'pull_request' && needs.changes.outputs.full_release != 'true' && '[]'" in include
    assert on_pull_request == {"macos-smoke"}
    assert on_main == {"setup", "e2e", "notifier", "macos-specific", "electron"}  # and the test matrix's macOS shards
    assert _oses(jobs["setup"]["strategy"]["matrix"]["os"], "push") == [
        "ubuntu-24.04", "ubuntu-24.04-arm", "macos-latest",
    ]  # fmt: skip
    smoke = jobs["macos-smoke"]
    assert (smoke["name"], smoke["runs-on"]) == ("macOS smoke", "macos-latest")
    assert smoke["timeout-minutes"] <= 10
    assert smoke["if"].startswith("github.event_name == 'pull_request' && ")
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
@pytest.mark.parametrize("macos_full", [False, True])
def test_native_macos_matrices_follow_scope_only_on_pull_requests(event, macos_full):
    jobs = _ci_jobs()
    native = event != "pull_request" or macos_full
    assert _oses(jobs["setup"]["strategy"]["matrix"]["os"], event, macos_full=macos_full) == (
        ["ubuntu-24.04", "ubuntu-24.04-arm", "macos-latest"] if native else ["ubuntu-24.04", "ubuntu-24.04-arm"]
    )
    for name in ("e2e", "electron"):
        assert _oses(jobs[name]["strategy"]["matrix"]["os"], event, macos_full=macos_full) == (
            ["ubuntu-24.04", "macos-latest"] if native else ["ubuntu-24.04"]
        )


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("pull_request", ["3.12", "3.15"]),
        ("push", ["3.12", "3.13", "3.14", "3.15"]),
        ("schedule", ["3.12", "3.13", "3.14", "3.15"]),
        ("workflow_dispatch", ["3.12", "3.13", "3.14", "3.15"]),
    ],
)
def test_a_pull_request_tests_python_oldest_and_newest_and_main_every_supported_python(event, expected):
    test = _ci_jobs()["test"]
    assert _oses(test["strategy"]["matrix"]["python"], event) == expected
    probe = next(step for step in test["steps"] if step.get("run") == "make linux-user-manager-proof")
    assert probe["if"] == "matrix.os == 'ubuntu-24.04' && matrix.python == '3.12'"  # a cell every pull request runs


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("pull_request", ["3.15"]),
        ("push", ["3.12", "3.13", "3.14", "3.15"]),
        ("schedule", ["3.12", "3.13", "3.14", "3.15"]),
        ("workflow_dispatch", ["3.12", "3.13", "3.14", "3.15"]),
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
    job = workflow["jobs"]["acceptance"]
    command = next(step["run"] for step in job["steps"] if step.get("name") == "Run the acceptance harness")
    for flag, variable in (
        ("--accept-wheel-sha256", "EXPECTED_WHEEL_SHA256"),
        ("--accept-profile-sha256", "EXPECTED_PROFILE_SHA256"),
    ):
        assert f'{flag} "${variable}"' in command
        assert variable in job["env"]
    assert "inputs.release_attempt" in workflow["run-name"]
    assert workflow["permissions"] == {}
    assert job["permissions"] == {"contents": "read"}


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
        "3.12",
        "3.13",
        "3.14",
        "3.15",
    ]
    assert len(_macos_include(jobs["test"]["strategy"]["matrix"], "pull_request", full=True, release=True)) == 8
    assert _oses(jobs["e2e"]["strategy"]["matrix"]["os"], "pull_request", macos_full=True) == [
        "ubuntu-24.04",
        "macos-latest",
    ]
    assert jobs["e2e"]["strategy"]["matrix"]["suite"] == ["cli", "supervised", "web-1", "web-2"]
    assert "needs.changes.outputs.full_release == 'true'" in jobs["notifier"]["if"]
    assert "needs.changes.outputs.full_release == 'true'" in jobs["macos-specific"]["if"]
    assert "needs.changes.outputs.full_release != 'true'" in jobs["macos-smoke"]["if"]
    assert "release-candidate-source" in jobs["ci-ok"]["needs"]


def test_release_candidate_source_and_selector_never_reach_plugin_templates():
    generated = _template_sync_module().ci_workflow()
    assert "release_candidate.py" not in generated
    assert "release-candidate-source" not in generated
    assert "full_release" not in generated
    assert (
        "github.event_name == 'pull_request' && needs.changes.outputs.full_release != 'true'"
        in (_ci_jobs()["test"]["strategy"]["matrix"]["python"])
    )
