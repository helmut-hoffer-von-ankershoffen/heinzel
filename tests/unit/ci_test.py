"""CI runs what developers run locally."""

import hashlib
import importlib.util
import io
import os
import re
import stat
import subprocess
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]

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
        "uv run --no-project --no-build --python 3.14 python scripts/check_confidentiality.py --require": (
            "the secret must be configured in this repo"
        ),
        "unzip -l ": "asserts what the built wheel contains",
        'changed="$(git diff --name-only': "decides from the changed paths whether a pull request builds the macOS app",
        "if compgen -G ": "selects the built core candidate or an independently released artifact for source plugins",
        "for result in $RESULTS": "the CI OK gate aggregates the results of the other jobs",
        "tar -czf notifier.tar.gz": "packs the app the macOS job built, with its executable bit, for the build job",
        "tar -xzf notifier.tar.gz": "unpacks Heinzel Notifier into the package before the wheel is built",
    },
    "docs.yml": {
        "sudo apt-get update": "fonts for the terminal recordings, from the runner's packages",
        "uv run --quiet --frozen --no-build --group docs playwright install-deps": (
            "Chromium's system libraries, from the runner"
        ),
    },
    "publish.yml": {
        'test "v$(uv version --short)" = "$TAG"': "refuses a tag that doesn't match the project version",
        "uv run --no-project --no-build --python 3.14 python scripts/check_confidentiality.py": (
            "scans the tagged tree with the term list from the secret"
        ),
        'name="$(uv version': "exports the release's CycloneDX bill of materials",
        "uv run --no-project --no-build --python 3.14 python scripts/release_notes.py": "writes the release notes",
        'cp "$BUNDLE"': "attaches the provenance attestation and creates the GitHub release",
        "tar -czf notifier.tar.gz": "packs the app the macOS job built, with its executable bit, for the build job",
        "tar -xzf notifier.tar.gz": "unpacks Heinzel Notifier into the package before the wheel is built",
        "unzip -l ": "asserts that the released wheel ships Heinzel Notifier",
    },
    "pypi.yml": {
        '[[ "$TAG" =~': "binds publishing to the exact tag and main ancestry before build tooling",
        "gh api ": "verifies the published GitHub release and exports its exact source commit",
        "uv run --no-project --no-build --python 3.14 python scripts/check_confidentiality.py --require": (
            "requires the configured release term list"
        ),
        "tar -czf notifier.tar.gz": "preserves the notifier executable bits and signature across runners",
        "tar -xzf notifier.tar.gz": "restores the notifier from the same validated tag",
        "uv build --package heinzel": "builds only the registered core project, never unpublished workspace plugins",
        "uv run --no-project --no-build --python 3.14 python scripts/pypi_release.py artifacts": (
            "checks core metadata and complete packaged payloads"
        ),
        "python3 - <<'PY'": "rechecks hashes without checking out or running project code in the OIDC job",
    },
    "release.yml": {
        'test "$REF" = refs/heads/main': "restores an original plugin artifact set with the pinned toolkit",
        'test "$PUBLISH" != true || test "$REF" = refs/heads/main': (
            "runs the owning Make commands' same toolkit checks, build, audit and acceptance without core work"
        ),
        'uv run --no-project --no-build --python 3.14 python scripts/plugin_tooling.py --toolkit "$TOOLING" '
        "python -m heinzel_plugin_tools.publishing": (
            "publishes the original verified plugin bytes through its protected generic host workflow"
        ),
    },
    "prepare-release.yml": {
        'if [[ ! "$INTRO"': "rejects blank dispatch input before checkout or release work",
        "git config user.name": "opens the release pull request as the bot and hands its tag to the next step",
        "gh workflow run ci.yml": "starts CI on the release pull request, which the bot's token doesn't",
        "gh pr merge": "enables squash auto-merge on the release pull request once CI OK is green",
    },
    "release-tag.yml": {'tag="v$(uv version --short)"': "tags a merged release commit"},
    "claude-review.yml": {'files="$(git diff': "picks the review tier from the changed files"},
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
    unreviewed = "uv run --no-project --no-build --python 3.14 python scripts/unreviewed.py"
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


@pytest.mark.parametrize("event", ["pull_request", "push"])
@pytest.mark.parametrize("path", ["plugins/heinzel-unforeseen/src/plugin.py", "src/heinzel/kernel/public.py"])
def test_plugin_only_changes_skip_core_work_and_keep_required_plugin_checks(tmp_path, monkeypatch, event, path):
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
        ["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path,
        env={**environment, "BASE": base, "EVENT": event, "GITHUB_OUTPUT": str(output)}, check=True, timeout=30,
    )  # fmt: skip
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert not foreign_index.exists()
    plugin_only = path.startswith("plugins/")
    assert values["core"] == str(not plugin_only).lower()
    assert values["plugins"] == str(plugin_only).lower()
    assert values["contracts"] == str(not plugin_only).lower()
    if plugin_only:
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
INTERPRETER = re.compile(r"(?:\bbash|\bpython3?|\buv run(?: -[\w-]+)*(?: --script| python3?))\s+$")


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
    assert any(
        "gh pr merge" in s.get("run", "") and "--auto" in s["run"] for s in release["jobs"]["pull-request"]["steps"]
    )
    assert step["env"]["INTRO"] == "${{ inputs.intro }}"
    assert (REPO / "template" / "scripts" / "changelog_intros.py").resolve() == REPO / "scripts" / "changelog_intros.py"


def test_pushing_runs_check_fast_through_a_hook():
    """What CI would catch first (format, lint, contracts, generated files, unit tests) fails before the push."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    assert "exec make check-fast" in hooks["check-fast"]["entry"]
    assert hooks["check-fast"]["stages"] == ["pre-push"]
    assert "pre-push" in config["default_install_hook_types"]
    assert "--hook-type pre-push" in (REPO / "Makefile").read_text(encoding="utf-8")
    assert (REPO / "template" / ".pre-commit-config.yaml").resolve() == REPO / ".pre-commit-config.yaml"


def test_the_template_audit_runs_in_template_test_ci_and_full_preflight():
    """The rendered plugin's audit takes seconds, so `make template-test` runs it unless `AUDIT=0` (offline).

    `make preflight` stays offline unless `PREFLIGHT=full`, like the audit of Heinzelmännchen's own dependencies.
    """
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "\tTEMPLATE_AUDIT=$(if $(filter 0,$(AUDIT)),,1) bash scripts/template-test.sh" in makefile
    assert "template-test" not in (REPO / "template" / "Makefile").read_text(encoding="utf-8")
    assert "TEMPLATE_AUDIT=$(if $(filter full,$(PREFLIGHT)),1) bash scripts/template-test.sh" in makefile
    assert "make audit" in (REPO / "scripts" / "template-test.sh").read_text(encoding="utf-8")
    ci = "\n".join(commands(REPO / ".github" / "workflows" / "ci.yml"))
    assert "make template-test AUDIT=1" in ci


def test_claude_review_skips_events_sent_by_bots():
    """The action refuses bot-triggered runs (update-prs merges, release pull requests), so the job must not start."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    condition = spec["jobs"]["review"]["if"]
    for clause in (
        "vars.CLAUDE_REVIEW == 'true'",
        "github.event.sender.type != 'Bot'",
        "!github.event.pull_request.draft",
        "github.event.pull_request.head.repo.full_name == github.repository",
    ):
        assert clause in condition


def test_claude_review_prefers_the_api_key():
    """Each review step authenticates with ANTHROPIC_API_KEY and uses the subscription token only without one."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    actions = [step for step in spec["jobs"]["review"]["steps"] if "claude-code-action" in step.get("uses", "")]
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
    assert jobs["setup"]["strategy"]["matrix"]["os"] == ["ubuntu-latest", "macos-latest"]
    assert "if" not in jobs["build"]


def test_pull_requests_run_one_macos_python_and_ci_ok_waits_for_changes():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    include = jobs["test"]["strategy"]["matrix"]["include"]
    assert "pull_request" in include
    assert '[{"os":"macos-latest","python":"3.15"}]' in include
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


@pytest.mark.parametrize("fail", [False, True])
def test_e2e_prepares_core_build_before_suite_and_removes_owned_temp(tmp_path, fail):
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
                'for last do :; done\nprintf "dir:%s\\n" "$last" >> "$TRACE"\n'
                'test "$FAIL_PREP" != 1\n'
            ),
        ),
        (steps, '#!/bin/sh\nprintf "suite:%s\\n" "$*" >> "$TRACE"\n'),
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
        "FAIL_PREP": "1" if fail else "0",
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
    assert lines[0].startswith("build:build --quiet --wheel --out-dir ")
    assert lines[1].startswith("dir:")
    directory = Path(lines[1].removeprefix("dir:"))
    assert directory.is_relative_to(tmp_path)
    assert not directory.exists()
    assert (result.returncode == 0) is not fail
    assert any(line.startswith("suite:e2e=uv run pytest ") for line in lines) is not fail
    if not fail:
        [suite] = [line for line in lines if line.startswith("suite:e2e=")]
        node = "tests/e2e/docs_test.py::test_readme_instructions"
        first, second = suite.split(" && ")
        assert first.endswith(f"-x {node}")
        assert second.endswith(f"-x --deselect={node}")
        assert first.count("-n 3") == second.count("-n 3") == 1
        assert first.count("--session-timeout=600") == second.count("--session-timeout=600") == 1
    generated = _template_sync_module().makefile()
    assert "e2e-prepare" not in generated
    assert generated == (REPO / "template/Makefile").read_text(encoding="utf-8")


@pytest.mark.parametrize("first_fails", [False, True])
def test_e2e_sequence_stops_after_readme_failure(tmp_path, first_fails):
    """The shared step runs the remaining nodes only after the isolated README succeeds."""
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
        (steps, '#!/bin/sh\ncommand=${1#e2e=}\nexec /bin/sh -c "$command"\n'),
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
    assert commands[0].startswith("build ")
    assert len(commands) == (2 if first_fails else 3)
    assert "--deselect=" not in commands[1]
    if not first_fails:
        assert "--deselect=tests/e2e/docs_test.py::test_readme_instructions" in commands[2]
    assert (result.returncode != 0) is first_fails
