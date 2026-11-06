"""Only complete core distributions cross the isolated PyPI publishing boundary."""

import hashlib
import importlib.util
import io
import json
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pypi_release", ROOT / "scripts/pypi_release.py")
assert SPEC is not None
assert SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
VERSION = "1.2.3"
COMMIT = "a" * 40


@pytest.fixture
def artifacts(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dict.fromkeys(release.REQUIRED, b"payload")
    wheel[release.NOTIFIER] = b"\xca\xfe\xba\xbepayload"
    wheel[f"heinzel-{VERSION}.dist-info/METADATA"] = b"Name: heinzel\nVersion: 1.2.3\n"
    wheel[f"heinzel-{VERSION}.dist-info/entry_points.txt"] = (
        b"[console_scripts]\nheinzel = _heinzel_launcher:main\nhz = _heinzel_launcher:cli\n"
        b"hz-install = _heinzel_launcher:install\n[pytest11]\nheinzel = heinzel.testing\n"
    )
    source = {
        f"heinzel-{VERSION}/src/{name}": b"payload" for name in release.REQUIRED if name != "heinzel/CHANGELOG.md"
    }
    source[f"heinzel-{VERSION}/CHANGELOG.md"] = b"Changes"
    source[f"heinzel-{VERSION}/PKG-INFO"] = b"Name: heinzel\nVersion: 1.2.3\n"
    source[f"heinzel-{VERSION}/pyproject.toml"] = b'[project]\nname="heinzel"\nversion="1.2.3"\n'

    def write(
        *,
        wheel_changes=None,
        source_changes=None,
        notifier_mode=stat.S_IFREG | 0o755,
        launcher_mode=stat.S_IFREG | 0o644,
        source_launcher_symlink=False,
    ):
        for names, changes in ((wheel, wheel_changes), (source, source_changes)):
            for name, value in (changes or {}).items():
                if value is None:
                    names.pop(name, None)
                else:
                    names[name] = value
        with zipfile.ZipFile(dist / f"heinzel-{VERSION}-py3-none-any.whl", "w") as archive:
            for name, value in wheel.items():
                info = zipfile.ZipInfo(name)
                mode = (
                    notifier_mode
                    if name == release.NOTIFIER
                    else launcher_mode
                    if name == "_heinzel_launcher.py"
                    else stat.S_IFREG | 0o644
                )
                info.external_attr = mode << 16
                archive.writestr(info, value)
        with tarfile.open(dist / f"heinzel-{VERSION}.tar.gz", "w:gz") as archive:
            for name, value in source.items():
                info = tarfile.TarInfo(name)
                if source_launcher_symlink and name == f"heinzel-{VERSION}/src/_heinzel_launcher.py":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "other.py"
                    archive.addfile(info)
                    continue
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
        return dist

    return write


def test_complete_core_artifacts_have_exact_identity_and_sha256_manifest(artifacts):
    dist = artifacts()
    manifest = release.verify_artifacts(dist, "v1.2.3", COMMIT)
    assert manifest == {
        "schema": 1,
        "project": "heinzel",
        "version": VERSION,
        "commit": COMMIT,
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(dist.iterdir())},
    }


@pytest.mark.parametrize(
    "entries",
    [
        b"[console_scripts]\nhz = heinzel.heads.cli:main\n",
        b"[console_scripts]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\n",
        b"[console_scripts]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\nhz-install = wrong:target\n",
        (
            b"[console_scripts]\nhz = _heinzel_launcher:cli\nhz = _heinzel_launcher:cli\n"
            b"heinzel = _heinzel_launcher:main\nhz-install = _heinzel_launcher:install\n"
        ),
        (
            b"[pytest11]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\n"
            b"hz-install = _heinzel_launcher:install\n"
        ),
        (
            b"[DEFAULT]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\n"
            b"hz-install = _heinzel_launcher:install\n[console_scripts]\n"
        ),
    ],
)
def test_wrong_missing_duplicate_or_misplaced_console_scripts_are_refused(artifacts, entries):
    dist = artifacts(wheel_changes={f"heinzel-{VERSION}.dist-info/entry_points.txt": entries})
    with pytest.raises(release.ReleaseError, match="console scripts"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_empty_launcher_payload_is_refused(artifacts):
    dist = artifacts(wheel_changes={"_heinzel_launcher.py": b""})
    with pytest.raises(release.ReleaseError, match="empty"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_symlink_launcher_payload_is_refused(artifacts):
    dist = artifacts(launcher_mode=stat.S_IFLNK | 0o777)
    with pytest.raises(release.ReleaseError, match="regular"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_empty_source_launcher_is_refused(artifacts):
    dist = artifacts(source_changes={f"heinzel-{VERSION}/src/_heinzel_launcher.py": b""})
    with pytest.raises(release.ReleaseError, match="nonempty"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_symlink_source_launcher_is_refused(artifacts):
    dist = artifacts(source_launcher_symlink=True)
    with pytest.raises(release.ReleaseError, match="regular"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_missing_source_launcher_is_refused(artifacts):
    dist = artifacts(source_changes={f"heinzel-{VERSION}/src/_heinzel_launcher.py": None})
    with pytest.raises(release.ReleaseError, match="incomplete"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize("missing", release.REQUIRED)
def test_missing_wheel_payload_is_refused(artifacts, missing):
    dist = artifacts(wheel_changes={missing: None})
    with pytest.raises(release.ReleaseError, match="lacks"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize(
    "metadata",
    [
        b"Name: other\nVersion: 1.2.3\n",
        b"Name: heinzel\nVersion: 1.2.4\n",
        b"Name: heinzel\nName: other\nVersion: 1.2.3\n",
    ],
)
def test_mismatched_or_ambiguous_metadata_is_refused(artifacts, metadata):
    dist = artifacts(wheel_changes={f"heinzel-{VERSION}.dist-info/METADATA": metadata})
    with pytest.raises(release.ReleaseError, match="metadata"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize("mode", [stat.S_IFREG | 0o644, stat.S_IFLNK | 0o755])
def test_notifier_must_be_a_real_executable_payload(artifacts, mode):
    dist = artifacts(notifier_mode=mode)
    with pytest.raises(release.ReleaseError, match=r"regular|executable"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_notifier_placeholder_is_refused(artifacts):
    dist = artifacts(wheel_changes={release.NOTIFIER: b"not a binary"})
    with pytest.raises(release.ReleaseError, match="universal"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_incomplete_sdist_is_refused(artifacts):
    dist = artifacts(source_changes={f"heinzel-{VERSION}/src/heinzel/heads/web/static/index.html": None})
    with pytest.raises(release.ReleaseError, match="incomplete"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_unpublished_plugin_or_extra_file_cannot_enter_the_upload(artifacts):
    dist = artifacts()
    (dist / "heinzel-harbor-1.2.3.whl").write_bytes(b"plugin")
    with pytest.raises(release.ReleaseError, match="only one core"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize("tag", ["main", "v1.2.3rc1", "v01.2.3", "v1.2.3;echo unsafe", "refs/tags/v1.2.3"])
def test_only_final_exact_version_tags_are_allowed(tag):
    with pytest.raises(release.ReleaseError, match="genuine final release tag"):
        release.version_for(tag)


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/pypi.yml").read_text())


def test_uv_build_helper_is_removed_before_strict_artifact_verification(artifacts):
    dist = artifacts()
    (dist / ".gitignore").write_text("*", encoding="utf-8")
    steps = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())["jobs"]["publish"]["steps"]
    build = next(index for index, step in enumerate(steps) if step.get("run", "").startswith("make build"))
    verify = next(index for index, step in enumerate(steps) if "pypi_release.py producer" in step.get("run", ""))
    attest = next(index for index, step in enumerate(steps) if step.get("id") == "attest")
    assert build < verify < attest
    assert steps[verify]["run"].index("make pypi-clean-build-marker") < steps[verify]["run"].index(
        "pypi_release.py producer"
    )
    subprocess.run(
        ["make", "pypi-clean-build-marker", f"PYPI_DIST={dist}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert not (dist / ".gitignore").exists()
    manifest = release.verify_artifacts(dist, "v1.2.3", COMMIT)
    assert set(manifest["files"]) == {f"heinzel-{VERSION}-py3-none-any.whl", f"heinzel-{VERSION}.tar.gz"}


def test_oidc_authority_is_isolated_from_all_build_and_checkout_steps():
    spec = workflow()
    assert spec["permissions"] == {}
    assert spec[True] == {"workflow_dispatch": None}  # YAML1.1 represents GitHub's 'on' as True.
    assert spec["concurrency"]["cancel-in-progress"] is False
    jobs = spec["jobs"]
    assert set(jobs) == {"verify", "publish"}
    verifier = jobs["verify"]
    assert verifier["permissions"] == {"contents": "read", "actions": "read", "attestations": "read"}
    checkout = next(step for step in verifier["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "refs/tags/${{ github.ref_name }}"
    assert checkout["with"]["persist-credentials"] is False
    commands = "\n".join(step.get("run", "") for step in verifier["steps"])
    assert "pypi_release.py promote" in commands
    assert "check_confidentiality.py --require" in commands
    assert "make licenses" in commands
    assert all(command not in commands for command in ("make notifier", "make build", "uv build", "scripts/build.py"))
    publisher = jobs["publish"]
    assert publisher["permissions"] == {"id-token": "write"}
    assert publisher["environment"]["name"] == "pypi"
    assert publisher["needs"] == "verify"
    assert all("refs/tags/v" in job["if"] and release.REPOSITORY in job["if"] for job in jobs.values())
    assert not any(step.get("uses", "").startswith("actions/checkout") for step in publisher["steps"])
    upload = publisher["steps"][-1]
    assert upload["uses"] == "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086"
    assert upload["with"] == {
        "packages-dir": "release-artifacts/dist",
        "attestations": True,
        "skip-existing": False,
        "verify-metadata": True,
    }


@pytest.mark.parametrize("change", [None, "bytes", "commit", "extra", "symlink"])
def test_publish_job_rechecks_identity_and_bytes_without_project_code(artifacts, tmp_path, monkeypatch, change):
    dist = artifacts()
    manifest = release.verify_artifacts(dist, "v1.2.3", COMMIT)
    root = tmp_path / "release-artifacts"
    root.mkdir()
    dist.rename(root / "dist")
    if change == "bytes":
        next((root / "dist").glob("*.whl")).write_bytes(b"modified")
    elif change == "commit":
        manifest["commit"] = "b" * 40
    elif change == "extra":
        (root / "dist/other.whl").write_bytes(b"extra")
    elif change == "symlink":
        wheel = next((root / "dist").glob("*.whl"))
        outside = tmp_path / "outside.whl"
        wheel.rename(outside)
        wheel.symlink_to(outside)
    (root / "artifact-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("RELEASE_TAG", "v1.2.3")
    monkeypatch.setenv("SOURCE_COMMIT", COMMIT)
    steps = workflow()["jobs"]["publish"]["steps"]
    script = next(step["run"] for step in steps if "run" in step).split("\n", 1)[1].removesuffix("PY\n")
    # Run the exact standard-library verification used in the credentialed job, not a matching reimplementation.
    monkeypatch.chdir(tmp_path)
    if change is None:
        exec(compile(script, "<publish-manifest-verifier>", "exec"), {})  # noqa: S102 - exercise the exact trusted workflow verifier
    else:
        with pytest.raises(SystemExit):
            exec(compile(script, "<publish-manifest-verifier>", "exec"), {})  # noqa: S102 - exercise the exact trusted workflow verifier


@pytest.fixture
def promotion(artifacts, monkeypatch):
    directory = artifacts()
    manifest = release.verify_artifacts(directory, "v1.2.3", COMMIT)
    producer = {
        "run_id": 123,
        "attempt": 1,
        "source_ref": "refs/heads/main",
        "workflow": ".github/workflows/release-tag.yml",
    }
    manifest["producer"] = producer
    (directory / "artifact-manifest.json").write_text(json.dumps(manifest))
    published = {
        "assets": [
            {
                "name": path.name,
                "id": index + 1,
                "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
                "browser_download_url": f"https://github.com/{release.REPOSITORY}/releases/download/v1.2.3/{path.name}",
            }
            for index, path in enumerate(sorted(directory.iterdir()))
        ]
    }
    run = {
        "id": 123,
        "run_attempt": 1,
        "repository": {"full_name": release.REPOSITORY},
        "head_sha": COMMIT,
        "path": producer["workflow"],
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }
    branch = {"name": "main", "protected": True}
    calls = []

    def gh(*args):
        calls.append(args)
        if args[0] == "api":
            if "/releases/tags/" in args[1]:
                return published
            return branch if args[1].endswith("/branches/main") else run
        path = Path(args[2])
        assert args[0:2] == ("attestation", "verify")
        assert args[3:] == (
            "--repo",
            release.REPOSITORY,
            "--signer-workflow",
            f"{release.REPOSITORY}/.github/workflows/publish.yml",
            "--signer-digest",
            COMMIT,
            "--source-digest",
            COMMIT,
            "--source-ref",
            producer["source_ref"],
            "--deny-self-hosted-runners",
            "--format",
            "json",
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return [
            {
                "verificationResult": {
                    "signature": {
                        "certificate": {
                            "runInvocationURI": f"https://github.com/{release.REPOSITORY}/actions/runs/123/attempts/1",
                            "buildConfigURI": f"https://github.com/{release.REPOSITORY}/{producer['workflow']}@{producer['source_ref']}",
                            "buildConfigDigest": COMMIT,
                            "runnerEnvironment": "github-hosted",
                        }
                    },
                    "verifiedTimestamps": ["verified"],
                    "statement": {
                        "predicateType": "https://slsa.dev/provenance/v1",
                        "subject": [{"name": path.name, "digest": {"sha256": digest}}],
                        "predicate": {
                            "buildDefinition": {
                                "externalParameters": {
                                    "workflow": {
                                        "repository": f"https://github.com/{release.REPOSITORY}",
                                        "path": producer["workflow"],
                                        "ref": producer["source_ref"],
                                    }
                                },
                                "internalParameters": {"github": {"runner_environment": "github-hosted"}},
                            },
                            "runDetails": {
                                "metadata": {
                                    "invocationId": f"https://github.com/{release.REPOSITORY}/actions/runs/123/attempts/1"
                                }
                            },
                        },
                    },
                }
            }
        ]

    monkeypatch.setattr(release, "_gh", gh)
    monkeypatch.setattr(release, "verify_source", lambda *args: COMMIT)
    monkeypatch.setattr(release, "verify_remote_tag", lambda *args: None)
    return directory, manifest, published, run, branch, calls, gh


def test_promotes_only_the_original_signed_bytes_without_a_build(promotion):
    directory, manifest, _, _, _, calls, _ = promotion
    release.promote(directory, "v1.2.3")
    assert release.verify_artifacts(directory / "dist", "v1.2.3", COMMIT) == {
        key: value for key, value in manifest.items() if key != "producer"
    }
    assert len([call for call in calls if call[0] == "attestation"]) == 3
    assert all(call[0] in {"api", "attestation"} for call in calls)


@pytest.mark.parametrize(
    "change",
    ["run", "attempt", "source", "workflow", "repository", "status", "conclusion", "ref", "event", "protected"],
)
def test_wrong_or_unfinished_official_producer_fails_closed(promotion, change):
    directory, _, _, run, branch, _, _ = promotion
    if change == "protected":
        branch["protected"] = False
    else:
        key, value = {
            "run": ("id", 124),
            "attempt": ("run_attempt", 2),
            "source": ("head_sha", "b" * 40),
            "workflow": ("path", ".github/workflows/edge.yml"),
            "repository": ("repository", {"full_name": "other/repo"}),
            "status": ("status", "in_progress"),
            "conclusion": ("conclusion", "failure"),
            "ref": ("head_branch", "other"),
            "event": ("event", "pull_request"),
        }[change]
        run[key] = value
    with pytest.raises(release.ReleaseError, match="producer"):
        release.promote(directory, "v1.2.3")
    assert not (directory / "dist").exists()


@pytest.mark.parametrize("change", ["missing", "duplicate", "digest", "url", "bytes", "symlink", "tag", "manifest"])
def test_missing_substituted_or_changed_release_assets_fail_closed(promotion, change, tmp_path):
    directory, manifest, published, _, _, _, _ = promotion
    asset = published["assets"][0]
    if change == "missing":
        published["assets"].pop(0)
    elif change == "duplicate":
        published["assets"].append(dict(asset))
    elif change in {"digest", "url"}:
        asset["digest" if change == "digest" else "browser_download_url"] = "substituted"
    elif change == "tag":
        manifest["version"] = "1.2.4"
        (directory / "artifact-manifest.json").write_text(json.dumps(manifest))
    elif change == "manifest":
        manifest["files"]["../other.whl"] = "b" * 64
        (directory / "artifact-manifest.json").write_text(json.dumps(manifest))
    else:
        path = directory / asset["name"]
        if change == "symlink":
            outside = tmp_path / "outside"
            path.rename(outside)
            path.symlink_to(outside)
        else:
            path.write_bytes(b"changed")
    with pytest.raises(release.ReleaseError):
        release.promote(directory, "v1.2.3")


@pytest.mark.parametrize(
    "change", ["run", "workflow", "subject", "digest", "host", "timestamp", "certificate", "empty"]
)
def test_verified_provenance_requires_exact_subject_and_producer(promotion, monkeypatch, change):
    directory, _, _, _, _, _, gh = promotion

    def changed(*args):
        result = gh(*args)
        if args[0] != "attestation":
            return result
        verified = result[0]["verificationResult"]
        statement = verified["statement"]
        if change == "run":
            statement["predicate"]["runDetails"]["metadata"]["invocationId"] += "0"
        elif change == "workflow":
            statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"]["path"] = "wrong.yml"
        elif change in {"subject", "digest"}:
            statement["subject"][0]["name" if change == "subject" else "digest"] = "wrong"
        elif change == "host":
            statement["predicate"]["buildDefinition"]["internalParameters"]["github"]["runner_environment"] = (
                "self-hosted"
            )
        elif change in {"timestamp", "certificate"}:
            verified["verifiedTimestamps"] = []
            verified["signature"]["certificate"] = {}
        else:
            return []
        return result

    monkeypatch.setattr(release, "_gh", changed)
    with pytest.raises(release.ReleaseError, match="provenance"):
        release.promote(directory, "v1.2.3")


def test_crypto_verification_failure_does_not_stage_any_upload(promotion, monkeypatch):
    directory, _, _, _, _, _, gh = promotion

    def failed(*args):
        if args[0] == "attestation":
            raise subprocess.CalledProcessError(1, args)
        return gh(*args)

    monkeypatch.setattr(release, "_gh", failed)
    with pytest.raises(subprocess.CalledProcessError):
        release.promote(directory, "v1.2.3")
    assert not (directory / "dist").exists()


def test_producer_build_guards_precede_any_tooling_or_attestation():
    jobs = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())["jobs"]
    for name in ("notifier", "publish"):
        steps = jobs[name]["steps"]
        checkout = steps[0]
        assert checkout["with"]["ref"] == "refs/tags/${{ inputs.tag || github.ref_name }}"
        assert checkout["with"]["fetch-depth"] == 0
        guard = steps[1]["run"]
        assert 'test "$GITHUB_SHA" = "$tagged"' in guard
        assert 'git merge-base --is-ancestor "$tagged" origin/main' in guard
        assert steps[2]["uses"] == "./.github/actions/setup-uv"


def test_core_publication_checks_the_tagged_ui_contract_before_building():
    steps = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())["jobs"]["publish"]["steps"]
    names = [step.get("name") for step in steps]
    index = names.index("Check the Core foreground UI release contract")
    build = next(i for i, step in enumerate(steps) if step.get("run", "").startswith("make build"))
    assert names.index("Check that the tag matches the project version") < index < build
    guard = steps[index]
    assert guard["if"] == f"github.repository == '{release.REPOSITORY}'"
    assert guard["timeout-minutes"] == 1
    assert guard["run"].endswith('python -I scripts/acceptance/run_acceptance.py --check-foreground-ui "${TAG#v}"')


@pytest.mark.parametrize(
    "remote",
    ["", "b" * 40 + "\trefs/tags/v1.2.3", "c" * 40 + "\trefs/tags/v1.2.3\n" + "b" * 40 + "\trefs/tags/v1.2.3^{}"],
)
def test_missing_or_moved_remote_tag_is_refused(monkeypatch, remote):
    monkeypatch.setattr(release, "_git", lambda *args: remote)
    with pytest.raises(release.ReleaseError, match="remote version tag"):
        release.verify_remote_tag(ROOT, "v1.2.3", COMMIT)


def test_annotated_remote_tag_is_compared_by_peeled_commit(monkeypatch):
    monkeypatch.setattr(
        release, "_git", lambda *args: "c" * 40 + "\trefs/tags/v1.2.3\n" + COMMIT + "\trefs/tags/v1.2.3^{}"
    )
    release.verify_remote_tag(ROOT, "v1.2.3", COMMIT)


def test_release_replacement_during_verification_is_refused(promotion, monkeypatch):
    directory, _, _, _, _, _, gh = promotion
    reads = 0

    def changed(*args):
        nonlocal reads
        result = gh(*args)
        if args[0] == "api" and "/releases/tags/" in args[1]:
            reads += 1
            if reads == 2:
                return {**result, "id": 999}
        return result

    monkeypatch.setattr(release, "_gh", changed)
    with pytest.raises(release.ReleaseError, match="changed during promotion"):
        release.promote(directory, "v1.2.3")


def test_download_counter_changes_do_not_substitute_asset_identity(promotion, monkeypatch):
    directory, _, _, _, _, _, gh = promotion
    reads = 0

    def counted(*args):
        nonlocal reads
        result = gh(*args)
        if args[0] == "api" and "/releases/tags/" in args[1]:
            reads += 1
            if reads == 2:
                return {**result, "assets": [{**asset, "download_count": 10} for asset in result["assets"]]}
        return result

    monkeypatch.setattr(release, "_gh", counted)
    release.promote(directory, "v1.2.3")


@pytest.mark.parametrize(
    "change",
    [
        None,
        "GITHUB_REPOSITORY",
        "GITHUB_SHA",
        "GITHUB_REF",
        "GITHUB_WORKFLOW_REF",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
    ],
)
def test_only_an_exact_official_producer_can_create_the_signed_manifest(artifacts, monkeypatch, change):
    directory = artifacts()
    env = {
        "GITHUB_REPOSITORY": release.REPOSITORY,
        "GITHUB_SHA": COMMIT,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{release.REPOSITORY}/.github/workflows/release-tag.yml@refs/heads/main",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, "wrong" if key == change else value)
    if change is not None:
        with pytest.raises(release.ReleaseError):
            release.producer_manifest(directory, "v1.2.3", COMMIT)
    else:
        manifest = release.producer_manifest(directory, "v1.2.3", COMMIT)
        assert manifest["producer"] == {
            "run_id": 123,
            "attempt": 1,
            "source_ref": "refs/heads/main",
            "workflow": ".github/workflows/release-tag.yml",
        }


def test_promotion_rechecks_even_signed_payload_completeness(promotion):
    directory, manifest, published, _, _, _, _ = promotion
    wheel = directory / f"heinzel-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist() if info.filename != release.NOTIFIER]
    with zipfile.ZipFile(wheel, "w") as archive:
        for info, data in entries:
            archive.writestr(info, data)
    manifest["files"][wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (directory / "artifact-manifest.json").write_text(json.dumps(manifest))
    for asset in published["assets"]:
        asset["digest"] = "sha256:" + hashlib.sha256((directory / asset["name"]).read_bytes()).hexdigest()
    with pytest.raises(release.ReleaseError, match="lacks"):
        release.promote(directory, "v1.2.3")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("runInvocationURI", f"https://github.com/{release.REPOSITORY}/actions/runs/124/attempts/1"),
        ("runInvocationURI", f"https://github.com/{release.REPOSITORY}/actions/runs/123/attempts/2"),
        ("runInvocationURI", None),
        ("buildConfigURI", f"https://github.com/{release.REPOSITORY}/.github/workflows/wrong.yml@refs/heads/main"),
        (
            "buildConfigURI",
            f"https://github.com/{release.REPOSITORY}/.github/workflows/release-tag.yml@refs/heads/other",
        ),
        ("buildConfigDigest", "b" * 40),
        ("runnerEnvironment", "self-hosted"),
    ],
)
def test_matching_predicate_cannot_substitute_a_different_verified_certificate(promotion, monkeypatch, key, value):
    directory, _, _, _, _, _, gh = promotion

    def substituted(*args):
        proof = gh(*args)
        if args[0] == "attestation":
            proof[0]["verificationResult"]["signature"]["certificate"][key] = value
        return proof

    monkeypatch.setattr(release, "_gh", substituted)
    with pytest.raises(release.ReleaseError, match="provenance"):
        release.promote(directory, "v1.2.3")
