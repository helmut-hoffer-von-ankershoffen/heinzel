"""Exercise desktop release identity and verification boundaries with fictional bytes."""

import hashlib
import json
import subprocess

import pytest

from heinzel.heads import _desktop_release as release

pytestmark = pytest.mark.unit
TAG = "v1.2.3"
COMMIT = "a" * 40


def _asset(name="one", identifier=42, digest=None):
    return {"name": name, "id": identifier, "digest": digest or "sha256:" + "b" * 64}


def _release(assets):
    return {"tag_name": TAG, "draft": False, "prerelease": False, "assets": assets}


def test_release_assets_select_exact_published_ids(monkeypatch):
    monkeypatch.setattr(release, "_api", lambda *_: _release([_asset(), _asset("other")]))
    assert release._release_assets(TAG, {"one"}) == {"one": release._AssetIdentity(TAG, 42, "b" * 64)}


@pytest.mark.parametrize(
    "change", ["tag", "draft", "prerelease", "duplicate", "bool-id", "zero-id", "digest", "none", "partial"]
)
def test_release_assets_refuse_ambiguous_or_unpublished_identity(monkeypatch, change):
    payload = _release([_asset()])
    names = {"one"}
    if change == "tag":
        payload["tag_name"] = "v9.0.0"
    if change == "draft":
        payload["draft"] = True
    if change == "prerelease":
        payload["prerelease"] = True
    if change == "duplicate":
        payload["assets"].append(_asset())
    if change == "bool-id":
        payload["assets"][0]["id"] = True
    if change == "zero-id":
        payload["assets"][0]["id"] = 0
    if change == "digest":
        payload["assets"][0]["digest"] = "sha256:bad"
    if change == "none":
        payload["assets"] = []
    if change == "partial":
        names.add("two")
    monkeypatch.setattr(release, "_api", lambda *_: payload)
    with pytest.raises(release.HeinzelError):
        release._release_assets(TAG, names)


def _core(source="refs/heads/main", workflow=".github/workflows/release-tag.yml"):
    return release._CoreManifest(
        version="1.2.3",
        commit=COMMIT,
        producer=release._Producer(run_id=123, attempt=1, source_ref=source, workflow=workflow),
    )


def _producer_records():
    return {
        "branches/main": {"protected": True},
        "actions/runs/123/attempts/1": {
            "id": 123,
            "run_attempt": 1,
            "head_sha": COMMIT,
            "path": ".github/workflows/release-tag.yml",
            "head_branch": "main",
            "repository": {"full_name": release.REPOSITORY},
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        },
        f"commits/{TAG}": {"sha": COMMIT},
        f"compare/{COMMIT}...main?per_page=1": {"status": "ahead"},
    }


def test_producer_requires_the_recorded_protected_release(monkeypatch):
    records = _producer_records()
    monkeypatch.setattr(release, "_api", lambda path, *_: records[path])
    release._check_producer(_core(), TAG)


@pytest.mark.parametrize(
    "field",
    [
        "protected",
        "tag",
        "ancestry",
        "run-id",
        "attempt",
        "commit",
        "workflow",
        "branch",
        "repo",
        "event",
        "status",
        "conclusion",
    ],
)
def test_producer_rejects_inconsistent_release_identity(monkeypatch, field):
    records = _producer_records()
    run = records["actions/runs/123/attempts/1"]
    if field == "protected":
        records["branches/main"]["protected"] = False
    if field == "tag":
        records[f"commits/{TAG}"]["sha"] = "bad"
    if field == "ancestry":
        records[f"compare/{COMMIT}...main?per_page=1"]["status"] = "diverged"
    values = {
        "run-id": ("id", 999),
        "attempt": ("run_attempt", 2),
        "commit": ("head_sha", "bad"),
        "workflow": ("path", "other"),
        "branch": ("head_branch", "other"),
        "repo": ("repository", {"full_name": "other"}),
        "event": ("event", "pull_request"),
        "status": ("status", "in_progress"),
        "conclusion": ("conclusion", "failure"),
    }
    if field in values:
        key, value = values[field]
        run[key] = value
    monkeypatch.setattr(release, "_api", lambda path, *_: records[path])
    with pytest.raises(release.HeinzelError, match="protected Core release"):
        release._check_producer(_core(), TAG)


def test_producer_rejects_unofficial_workflow_before_network(monkeypatch):
    monkeypatch.setattr(release, "_api", lambda *_: pytest.fail("network must not be read"))
    with pytest.raises(release.HeinzelError, match="outside the official"):
        release._check_producer(_core(workflow="other"), TAG)


def test_verifier_requires_both_invocation_bindings(monkeypatch, tmp_path):
    invocation = f"https://github.com/{release.REPOSITORY}/actions/runs/123/attempts/1"

    def entry(cert, predicate):
        return [
            {
                "verificationResult": {
                    "signature": {"certificate": {"runInvocationURI": cert}},
                    "statement": {"predicate": {"runDetails": {"metadata": {"invocationId": predicate}}}},
                }
            }
        ]

    def reply(cert, predicate):
        monkeypatch.setattr(
            release,
            "_run_gh",
            lambda *_: subprocess.CompletedProcess([], 0, json.dumps(entry(cert, predicate)).encode(), b""),
        )

    reply(invocation, invocation)
    release._verify("gh", tmp_path / "asset", tmp_path / "proof", _core())
    reply(invocation, "wrong")
    with pytest.raises(release.HeinzelError, match="provenance"):
        release._verify("gh", tmp_path / "asset", tmp_path / "proof", _core())


@pytest.fixture
def fictional_archive(monkeypatch):
    version = "1.2.3"
    archive = f"heinzel-desktop-{version}-darwin-arm64.tar.gz"
    body = b"fictional archive bytes"
    core = json.dumps(
        {
            "version": version,
            "commit": COMMIT,
            "producer": {
                "run_id": 123,
                "attempt": 1,
                "source_ref": "refs/heads/main",
                "workflow": ".github/workflows/release-tag.yml",
            },
        }
    ).encode()
    manifest = json.dumps(
        {
            "schema": 1,
            "version": version,
            "commit": COMMIT,
            "platform": "darwin",
            "arch": "arm64",
            "archive": {"name": archive, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()},
        }
    ).encode()
    contents = {
        release.MANIFEST_NAME: manifest,
        release.CORE_MANIFEST_NAME: core,
        release.PROOF_NAME: b"fictional proof",
        archive: body,
    }
    assets = {
        name: release._AssetIdentity(TAG, index, hashlib.sha256(value).hexdigest())
        for index, (name, value) in enumerate(contents.items(), 1)
    }
    calls = []
    monkeypatch.setattr(release.shutil, "which", lambda _: "/fixture/gh")
    monkeypatch.setattr(release, "_release_assets", lambda *_: assets)

    def download(path, _limit, _asset, *_args):
        calls.append(("download", path.name))
        path.write_bytes(contents[path.name])

    monkeypatch.setattr(release, "_download", download)
    monkeypatch.setattr(release, "_verify", lambda _gh, path, _proof, _core: calls.append(("verify", path.name)))
    monkeypatch.setattr(release, "_check_producer", lambda *_: calls.append(("producer", None)))
    return contents, assets, calls


def test_fetch_archive_verifies_before_and_after_archive_download(fictional_archive, tmp_path):
    contents, _, calls = fictional_archive
    result = release.fetch_archive("1.2.3", tmp_path)
    assert result.read_bytes() == contents[result.name]
    assert calls == [
        ("download", release.MANIFEST_NAME),
        ("download", release.PROOF_NAME),
        ("download", release.CORE_MANIFEST_NAME),
        ("verify", release.CORE_MANIFEST_NAME),
        ("verify", release.MANIFEST_NAME),
        ("producer", None),
        ("download", result.name),
        ("verify", result.name),
    ]


def test_fetch_archive_rejects_bad_version_and_missing_verifier(monkeypatch, tmp_path):
    with pytest.raises(release.HeinzelError, match="published stable"):
        release.fetch_archive("v1.2.3", tmp_path)
    monkeypatch.setattr(release.shutil, "which", lambda _: None)
    with pytest.raises(release.HeinzelError, match="Install GitHub CLI"):
        release.fetch_archive("1.2.3", tmp_path)


@pytest.mark.parametrize("failure", ["manifest-version", "archive-digest", "identity-change"])
def test_fetch_archive_rejects_mismatched_artifact(fictional_archive, monkeypatch, tmp_path, failure):
    contents, assets, calls = fictional_archive
    name = "heinzel-desktop-1.2.3-darwin-arm64.tar.gz"
    if failure == "manifest-version":
        contents[release.MANIFEST_NAME] = contents[release.MANIFEST_NAME].replace(b"1.2.3", b"9.9.9", 1)
    if failure == "archive-digest":
        contents[name] = b"X" * len(contents[name])
    if failure == "identity-change":
        modified = dict(assets)
        modified[name] = release._AssetIdentity(TAG, 999, assets[name].sha256)
        responses = iter([assets, modified])
        monkeypatch.setattr(release, "_release_assets", lambda *_: next(responses))
    with pytest.raises(release.HeinzelError):
        release.fetch_archive("1.2.3", tmp_path)
    if failure != "identity-change":
        assert ("verify", name) not in calls
