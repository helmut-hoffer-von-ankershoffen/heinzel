"""Keep CI bootstrap independent of mutable version discovery without bypassing integrity checks."""

import base64
import copy
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/setup-uv"
SURFACES = ("ci", "acceptance", "edge", "pypi", "publish", "prepare-release", "release-tag")
PLATFORMS = {
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu",
}


def assert_bootstrap(action, manifest):
    """Reject ranges, remote discovery, missing targets and missing archive integrity."""
    steps = action["runs"]["steps"]
    setup = steps[-1]
    assert setup["uses"] == "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e"
    version = setup["with"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert version == manifest["version"]
    assert setup["with"]["manifest-file"] == "${{ steps.manifest.outputs.url }}"
    assert {artifact["platform"] for artifact in manifest["artifacts"]} == PLATFORMS
    for artifact in manifest["artifacts"]:
        assert artifact["variant"] == "default"
        assert artifact["archive_format"] == "tar.gz"
        assert artifact["url"] == (
            f"https://github.com/astral-sh/uv/releases/download/{version}/uv-{artifact['platform']}.tar.gz"
        )
        assert re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"])


def test_ci_and_release_surfaces_use_the_reviewed_bootstrap():
    for name in SURFACES:
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        found = False
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                assert "astral-sh/setup-uv@" not in step.get("uses", ""), name
                found |= step.get("uses", "").endswith("/.github/actions/setup-uv")
        assert found, name
    assert (ROOT / "template/.github/actions/setup-uv/action.yml").resolve() == ACTION / "action.yml"


def test_manifest_output_is_a_complete_offline_data_url(tmp_path):
    action = yaml.safe_load((ACTION / "action.yml").read_text())
    manifest = json.loads((ACTION / "uv-manifest.json").read_text())
    assert_bootstrap(action, manifest)
    output = tmp_path / "output"
    subprocess.run(
        ["bash", "-e", "-c", action["runs"]["steps"][0]["run"]],
        env={"PATH": "/usr/bin:/bin", "ACTION_PATH": str(ACTION), "GITHUB_OUTPUT": str(output)},
        check=True,
        timeout=10,
    )
    prefix = "url=data:application/json;base64,"
    assert output.read_text().startswith(prefix)
    decoded = base64.b64decode(output.read_text()[len(prefix) :]).decode()
    # Match setup-uv parseManifest: every nonblank line must be a complete JSON record.
    assert [json.loads(line) for line in decoded.splitlines() if line.strip()] == [manifest]
    assert decoded.encode() == (ACTION / "uv-manifest.json").read_bytes()


@pytest.mark.parametrize("bad_version", ["latest", "latest-known", ">=0.13.0", "0.13.*", "0.13.6"])
def test_mutable_or_mismatched_resolution_is_rejected(bad_version):
    action = yaml.safe_load((ACTION / "action.yml").read_text())
    action["runs"]["steps"][-1]["with"]["version"] = bad_version
    with pytest.raises(AssertionError):
        assert_bootstrap(action, json.loads((ACTION / "uv-manifest.json").read_text()))


@pytest.mark.parametrize("field", ["sha256", "url"])
def test_archive_integrity_and_release_origin_are_required(field):
    manifest = copy.deepcopy(json.loads((ACTION / "uv-manifest.json").read_text()))
    manifest["artifacts"][0][field] = ""
    with pytest.raises(AssertionError):
        assert_bootstrap(yaml.safe_load((ACTION / "action.yml").read_text()), manifest)


def test_remote_manifest_resolution_is_rejected():
    action = yaml.safe_load((ACTION / "action.yml").read_text())
    action["runs"]["steps"][-1]["with"]["manifest-file"] = "https://example.org/latest.ndjson"
    with pytest.raises(AssertionError):
        assert_bootstrap(action, json.loads((ACTION / "uv-manifest.json").read_text()))


def assert_local_actions_exist_in_checkout(steps):
    """Resolve local actions against the repository actually occupying each checkout path."""
    checkouts = {}
    for step in steps:
        uses = step.get("uses", "")
        if uses.startswith("actions/checkout@"):
            config = step.get("with", {})
            destination = config.get("path", "").strip("/")
            checkouts = {
                path: value
                for path, value in checkouts.items()
                if destination and path != destination and not path.startswith(destination + "/")
            }
            checkouts[destination] = config
        elif uses.startswith("./"):
            relative = uses[2:]
            matches = [path for path in checkouts if not path or relative.startswith(path + "/")]
            assert matches, f"No checkout supplies {uses}"
            destination = max(matches, key=len)
            config = checkouts[destination]
            assert config.get("repository", "${{ github.repository }}") == "${{ github.repository }}", uses
            action_path = relative[len(destination) :].lstrip("/")
            assert (ROOT / action_path / "action.yml").is_file(), uses
            if destination:
                assert config["ref"] == "${{ github.sha }}"
                assert config["sparse-checkout"] == action_path
                assert config["persist-credentials"] is False


def test_every_local_action_exists_in_its_active_checkout():
    for name in SURFACES:
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        for job in workflow["jobs"].values():
            assert_local_actions_exist_in_checkout(job.get("steps", []))


@pytest.mark.parametrize("job", ["consumer-plan", "consumer-check"])
def test_foreign_root_checkout_cannot_supply_the_core_bootstrap(job):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"][job]["steps"]
    for step in steps:
        if step.get("uses") == "./.core-bootstrap/.github/actions/setup-uv":
            step["uses"] = "./.github/actions/setup-uv"
    with pytest.raises(AssertionError):
        assert_local_actions_exist_in_checkout(steps)


def test_pretty_multiline_json_is_not_a_manifest_record():
    manifest = json.loads((ACTION / "uv-manifest.json").read_text())
    with pytest.raises(json.JSONDecodeError):
        [json.loads(line) for line in json.dumps(manifest, indent=2).splitlines() if line.strip()]
