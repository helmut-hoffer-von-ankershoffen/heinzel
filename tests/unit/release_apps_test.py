"""Publish promotes the candidate's macOS apps only when their bytes and tree are the tag's."""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_apps_under_test", ROOT / "scripts/release_apps.py")
assert SPEC
assert SPEC.loader
apps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apps)

TAG, TAG_COMMIT, TREE, BUILT = "v1.2.3", "a" * 40, "b" * 40, "c" * 40
RUN = {
    "id": 7,
    "head_branch": "release/v1.2.3",
    "conclusion": "success",
    "event": "workflow_dispatch",
    "head_repository": {"full_name": apps.REPOSITORY},
    "path": ".github/workflows/ci.yml",
    "created_at": "2026-01-01T00:00:00Z",
}


def write_desktop(directory, *, version="1.2.3", commit=BUILT, corrupt=False):
    directory.mkdir(parents=True)
    for suffix, manifest in (("", apps.MANIFESTS[0]), ("-swift", apps.MANIFESTS[1])):
        name = f"heinzel-desktop{suffix}-{version}-darwin-arm64.tar.gz"
        data = f"archive{suffix}".encode()
        (directory / name).write_bytes(data + (b"x" if corrupt else b""))
        record = {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        body = {"schema": 1, "version": version, "commit": commit, "platform": "darwin", "arch": "arm64"}
        (directory / manifest).write_text(json.dumps(body | {"archive": record}))


def write_notifier(directory, member=apps.NOTIFIER_EXECUTABLE):
    directory.mkdir(parents=True)
    with tarfile.open(directory / "notifier.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = 2
        tar.addfile(info, io.BytesIO(b"#!"))


def fake(tmp_path, *, runs=(RUN,), tree=TREE, **desktop):
    def get(path):
        if path.startswith("actions/workflows/ci.yml/runs"):
            assert "branch=release/v1.2.3" in path
            assert "status=success" in path
            return {"workflow_runs": list(runs)}
        assert path == f"git/commits/{BUILT}"
        return {"tree": {"sha": tree}}

    def download(_run_id, name, dest):
        if name == apps.DESKTOP:
            write_desktop(dest, **desktop)
        else:
            write_notifier(dest)

    return get, download


def test_the_candidate_apps_are_promoted_and_rebound_to_the_tag(tmp_path):
    get, download = fake(tmp_path)
    facts = apps.promote(TAG, tmp_path / "out", TAG_COMMIT, TREE, get=get, download=download)
    assert facts == {"run": 7, "built_commit": BUILT, "tree": TREE}
    for name in apps.MANIFESTS:
        manifest = json.loads((tmp_path / "out/desktop" / name).read_text())
        assert manifest["commit"] == TAG_COMMIT  # what `make desktop-bind-release-assets` requires
        archive = tmp_path / "out/desktop" / manifest["archive"]["name"]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive"]["sha256"]
    assert (tmp_path / "out/notifier/notifier.tar.gz").is_file()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"tree": "d" * 40}, "tree is not the tag's"),
        ({"version": "1.2.4"}, "does not describe darwin arm64 1.2.3"),
        ({"corrupt": True}, "differs in size"),
        ({"runs": ()}, "No successful CI run"),
        ({"runs": ({**RUN, "head_repository": {"full_name": "someone/fork"}},)}, "No successful CI run"),
        ({"runs": ({**RUN, "head_branch": "release/v1.2.3-x"},)}, "No successful CI run"),
    ],
)
def test_anything_unproven_is_not_promoted(tmp_path, change, reason):
    get, download = fake(tmp_path, **change)
    with pytest.raises(apps.NotPromotedError, match=reason):
        apps.promote(TAG, tmp_path / "out", TAG_COMMIT, TREE, get=get, download=download)
    assert not (tmp_path / "out/desktop").exists()


def test_a_notifier_without_its_executable_is_not_promoted(tmp_path):
    get, _ = fake(tmp_path)

    def download(_run_id, name, dest):
        if name == apps.DESKTOP:
            write_desktop(dest)
        else:
            write_notifier(dest, member="Other.app/Contents/MacOS/other")

    with pytest.raises(apps.NotPromotedError, match="lacks the app's executable"):
        apps.promote(TAG, tmp_path / "out", TAG_COMMIT, TREE, get=get, download=download)


def test_a_later_run_that_fails_falls_back_to_an_earlier_good_one(tmp_path):
    newer = {**RUN, "id": 8, "created_at": "2026-01-02T00:00:00Z"}
    get, good = fake(tmp_path, runs=(RUN, newer))

    def download(run_id, name, dest):
        if run_id == 8:
            message = "artifact expired"
            raise OSError(message)
        good(run_id, name, dest)

    facts = apps.promote(TAG, tmp_path / "out", TAG_COMMIT, TREE, get=get, download=download)
    assert facts["run"] == 7


def test_the_main_entry_reports_a_refusal_as_output_never_as_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(apps, "run", lambda *_args: (_ for _ in ()).throw(OSError("no git")))
    assert apps.main(["promote", TAG, str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "promoted=false"
    assert out[1].startswith("reason=")


def test_publish_builds_on_macos_only_when_promotion_did_not_prove_the_bytes():
    jobs = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())["jobs"]
    promote = jobs["promote"]
    assert promote["runs-on"] == "ubuntu-24.04"
    assert promote["permissions"] == {"contents": "read", "actions": "read"}
    for name in ("notifier", "desktop"):
        assert jobs[name]["needs"] == "promote"
        assert "needs.promote.outputs.promoted != 'true'" in jobs[name]["if"]
    publish = jobs["publish"]
    assert publish["needs"] == ["promote", "notifier", "desktop"]
    assert "needs.promote.outputs.promoted == 'true'" in publish["if"]
    names = [step.get("with", {}).get("name", "") for step in publish["steps"]]
    assert "${{ needs.promote.outputs.promoted == 'true' && 'promoted-notifier' || 'notifier' }}" in names
    release = yaml.safe_load((ROOT / ".github/workflows/release-tag.yml").read_text())
    assert release["jobs"]["publish"]["permissions"]["actions"] == "read"


def test_candidate_ci_builds_the_release_apps_before_any_smoke_test_runs_them():
    electron = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["electron"]
    names = [step.get("name", step.get("uses", "")) for step in electron["steps"]]
    archive = names.index("Archive the release candidate's desktop apps and bind their manifests")
    assert names[archive - 1] == "Build and verify unsigned native package"
    assert archive < names.index("macOS Electron native window smoke test")
    step = electron["steps"][archive]
    assert step["if"] == "runner.os == 'macOS' && needs.changes.outputs.full_release == 'true'"
    assert "make desktop-swift-package" in step["run"]
    upload = electron["steps"][archive + 1]
    assert upload["with"]["name"] == apps.DESKTOP
    assert upload["if"] == step["if"]
