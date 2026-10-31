"""The edge channel's rules: what is releasable, its versions, and that it can never reach stable (ADR 0114)."""

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


channel = _load("release_channel")
pypi_release = _load("pypi_release")


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/guides/install.md"], False),
        (["tests/unit/x_test.py", ".github/workflows/ci.yml", ".claude/skills/a/SKILL.md"], False),
        (["README.md", "AGENTS.md", "mkdocs.yml"], False),
        ([], False),
        (["src/heinzel/__init__.py"], True),
        (["web/src/App.tsx"], True),
        (["docs/a.md", "uv.lock"], True),
        (["something-new/file"], True),
        (["src/heinzel/CHANGELOG.md"], True),
    ],
)
def test_only_known_non_shipping_paths_skip_a_release(paths, expected):
    assert channel.releasable(paths) is expected


def test_edge_is_above_the_current_stable_and_below_the_next():
    assert channel.edge_version("0.9.9", "0.9.9", 57) == "0.9.10.dev57"
    assert channel.edge_version("0.9.9", "0.10.0", 57) == "0.10.0.dev57"


def test_an_older_commit_never_replaces_a_newer_edge():
    assert channel.edge_version("0.9.9", "0.9.10", 57, "0.9.10.dev57") is None
    assert channel.edge_version("0.9.9", "0.9.10", 56, "0.9.10.dev57") is None


def test_edge_base_never_moves_back():
    assert channel.edge_version("0.9.9", "0.9.10", 58, "0.10.0.dev57") == "0.10.0.dev58"


@pytest.mark.parametrize(
    ("current", "suggested", "count"), [("0.9", "0.9.10", 1), ("0.9.9", "0.9.10rc1", 1), ("0.9.9", "0.9.10", 0)]
)
def test_invalid_edge_inputs_are_refused(current, suggested, count):
    with pytest.raises(channel.ChannelError):
        channel.edge_version(current, suggested, count)


def test_stamp_changes_only_the_core_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "heinzel"\nversion = "0.9.9"\ndeps = ["x"]\n')
    lock = '[[package]]\nname = "other"\nversion = "1.0.0"\n\n[[package]]\nname = "heinzel"\nversion = "0.9.9"\n'
    (tmp_path / "uv.lock").write_text(lock)
    channel.stamp("0.9.10.dev57", tmp_path)
    assert 'version = "0.9.10.dev57"' in (tmp_path / "pyproject.toml").read_text()
    stamped = (tmp_path / "uv.lock").read_text()
    assert 'name = "other"\nversion = "1.0.0"' in stamped
    assert 'name = "heinzel"\nversion = "0.9.10.dev57"' in stamped


@pytest.mark.parametrize("version", ["0.9.10", "0.9.10rc1", "0.9.10.dev0", "v0.9.10.dev1"])
def test_stamp_accepts_only_edge_versions(tmp_path, version):
    with pytest.raises(channel.ChannelError):
        channel.stamp(version, tmp_path)


def test_stamp_refuses_an_ambiguous_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\nversion = "1"\n')
    (tmp_path / "uv.lock").write_text("")
    with pytest.raises(channel.ChannelError):
        channel.stamp("0.9.10.dev1", tmp_path)


def test_pointer_binds_the_immutable_wheel(tmp_path):
    wheel = tmp_path / "heinzel-0.9.10.dev57-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    pointer = channel.pointer("0.9.10.dev57", "a" * 40, wheel)
    assert pointer["tag"] == "edge-v0.9.10.dev57"
    assert pointer["sha256"] == hashlib.sha256(b"wheel").hexdigest()
    assert str(pointer["wheel"]).endswith("/edge-v0.9.10.dev57/heinzel-0.9.10.dev57-py3-none-any.whl")


def test_pointer_refuses_another_wheel(tmp_path):
    wheel = tmp_path / "heinzel-0.9.10-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    with pytest.raises(channel.ChannelError):
        channel.pointer("0.9.10.dev57", "a" * 40, wheel)


@pytest.mark.parametrize("tag", ["edge-v0.9.10.dev57", "v0.9.10.dev57", "0.9.10.dev57"])
def test_the_pypi_publisher_refuses_every_edge_identity(tag):
    with pytest.raises(pypi_release.ReleaseError):
        pypi_release.version_for(tag)


def _edge() -> dict:
    return yaml.safe_load((REPO / ".github/workflows/edge.yml").read_text(encoding="utf-8"))


def test_edge_publishing_is_dark_until_a_maintainer_activates_it():
    plan = _edge()["jobs"]["plan"]
    gate = next(step for step in plan["steps"] if step.get("id") == "plan")
    assert "vars.RELEASE_CHANNELS" in gate["env"]["CHANNELS"]
    assert '[ "$EVENT" = workflow_run ]' in gate["run"]
    assert _edge()["jobs"]["publish"]["if"] == "needs.plan.outputs.publish == 'true'"


def test_edge_only_follows_a_green_push_to_main_of_this_repository():
    workflow = _edge()
    triggers = workflow[True]  # YAML reads the key `on` as true
    assert triggers["workflow_run"] == {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}
    condition = workflow["jobs"]["plan"]["if"]
    for required in (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
        "github.event.workflow_run.head_sha == github.sha",
    ):
        assert required in condition


def test_only_the_publish_job_can_write_and_it_runs_no_project_code():
    jobs = _edge()["jobs"]
    for name, job in jobs.items():
        if name != "publish":
            assert job["permissions"] == {"contents": "read"}, name
    publish = jobs["publish"]
    assert publish["permissions"] == {"contents": "write", "id-token": "write", "attestations": "write"}
    assert not any("actions/checkout" in step.get("uses", "") for step in publish["steps"])


def test_edge_release_can_never_become_latest_or_touch_pypi():
    text = (REPO / ".github/workflows/edge.yml").read_text(encoding="utf-8")
    assert text.count("--prerelease --latest=false") == 2
    assert "pypa/gh-action-pypi-publish" not in text
    assert "pypi.yml" not in text


def test_edge_never_checks_out_the_triggering_ref():
    for job in _edge()["jobs"].values():
        for step in job["steps"]:
            if "actions/checkout" in step.get("uses", ""):
                assert "ref" not in step["with"]


def test_a_dispatch_publishes_only_from_main():
    workflow = _edge()
    assert workflow[True]["workflow_dispatch"] is None  # no input can select what is published
    assert (
        "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in workflow["jobs"]["plan"]["if"]
    )
