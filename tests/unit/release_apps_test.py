"""Publish promotes the candidate's macOS apps only when their bytes and tree are the tag's."""

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
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


def fake(tmp_path, *, tree=TREE, **desktop):
    def get(path):
        assert path == f"git/commits/{BUILT}"
        return {"tree": {"sha": tree}}

    def download(run_id, name, dest):
        assert run_id == 7
        if name == apps.DESKTOP:
            write_desktop(dest, **desktop)
        else:
            write_notifier(dest)

    return get, download


def test_the_candidate_apps_are_promoted_and_rebound_to_the_tag(tmp_path):
    get, download = fake(tmp_path)
    facts = apps.promote(
        TAG, tmp_path / "out", TAG_COMMIT, TREE, approved_run=7, approved_checkout=BUILT, get=get, download=download
    )
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
    ],
)
def test_anything_unproven_is_not_promoted(tmp_path, change, reason):
    get, download = fake(tmp_path, **change)
    with pytest.raises(apps.NotPromotedError, match=reason):
        apps.promote(
            TAG, tmp_path / "out", TAG_COMMIT, TREE, approved_run=7, approved_checkout=BUILT, get=get, download=download
        )
    assert not (tmp_path / "out/desktop").exists()


def test_a_notifier_without_its_executable_is_not_promoted(tmp_path):
    get, _ = fake(tmp_path)

    def download(run_id, name, dest):
        assert run_id == 7
        if name == apps.DESKTOP:
            write_desktop(dest)
        else:
            write_notifier(dest, member="Other.app/Contents/MacOS/other")

    with pytest.raises(apps.NotPromotedError, match="lacks the app's executable"):
        apps.promote(
            TAG, tmp_path / "out", TAG_COMMIT, TREE, approved_run=7, approved_checkout=BUILT, get=get, download=download
        )


@pytest.mark.parametrize(
    "archive_name", ["../heinzel-desktop-1.2.3-darwin-arm64.tar.gz", "heinzel-desktop-swift-1.2.3-darwin-arm64.tar.gz"]
)
def test_a_desktop_manifest_must_select_its_own_exact_archive(tmp_path, archive_name):
    dest = tmp_path / "desktop"
    write_desktop(dest)
    manifest = dest / "desktop-artifact-manifest.json"
    record = json.loads(manifest.read_text())
    record["archive"]["name"] = archive_name
    manifest.write_text(json.dumps(record))
    with pytest.raises(apps.NotPromotedError, match="unexpected archive"):
        apps.check_desktop(dest, TAG[1:])


def test_a_different_manifested_commit_with_the_same_tree_is_not_reused(tmp_path):
    get, download = fake(tmp_path)
    with pytest.raises(apps.NotPromotedError, match="approved tested checkout"):
        apps.promote(
            TAG,
            tmp_path / "out",
            TAG_COMMIT,
            TREE,
            approved_run=7,
            approved_checkout="d" * 40,
            get=get,
            download=download,
        )


@pytest.mark.parametrize("defect", ["", "digest", "foreign-file", "producer", "head", "expired"])
def test_notifier_is_bound_to_the_approved_run_and_github_archive_digest(tmp_path, monkeypatch, defect):
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("foreign" if defect == "foreign-file" else "notifier.tar.gz", b"notifier")
    row = {
        "id": 55,
        "name": apps.NOTIFIER,
        "expired": defect == "expired",
        "digest": "sha256:" + ("0" * 64 if defect == "digest" else apps.file_sha256(archive)),
        "workflow_run": {"id": 8 if defect == "producer" else 7, "head_sha": "a" * 40 if defect == "head" else BUILT},
    }
    monkeypatch.setattr(apps, "gh_json", lambda path: {"total_count": 1, "artifacts": [row]})
    monkeypatch.setenv("APPROVED_HEAD", BUILT)
    monkeypatch.setenv("TAG", TAG)

    def downloaded(argv, *, stdout, **_kwargs):
        assert argv == ["gh", "api", f"repos/{apps.REPOSITORY}/actions/artifacts/55/zip"]
        stdout.write(archive.read_bytes())

    monkeypatch.setattr(apps.subprocess, "run", downloaded)
    dest = tmp_path / "notifier"
    if defect:
        with pytest.raises(apps.NotPromotedError):
            apps.gh_download(7, apps.NOTIFIER, dest)
        assert not (dest / "notifier.tar.gz").exists()
    else:
        apps.gh_download(7, apps.NOTIFIER, dest)
        assert (dest / "notifier.tar.gz").read_bytes() == b"notifier"


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
    assert promote["permissions"] == {
        "contents": "read",
        "actions": "read",
        "pull-requests": "read",
        "statuses": "read",
    }
    for name in ("notifier", "desktop"):
        assert jobs[name]["needs"] == "promote"
        assert "needs.promote.outputs.promoted != 'true'" in jobs[name]["if"]
    publish = jobs["publish"]
    assert publish["needs"] == ["promote", "notifier", "desktop", "swift-developer-id"]
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
