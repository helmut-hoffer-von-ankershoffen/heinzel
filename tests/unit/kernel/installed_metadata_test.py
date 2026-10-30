import json

import pytest

from heinzel import HeinzelError
from heinzel.kernel._installed_metadata import inspect_requirements

pytestmark = pytest.mark.unit


def distribution(prefix, name, version, direct=None):
    info = prefix / "lib/python3.14/site-packages" / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    if direct is not None:
        (info / "direct_url.json").write_text(json.dumps(direct))
    return info


def git_source(url, revision):
    return {"url": url, "vcs_info": {"vcs": "git", "requested_revision": revision, "commit_id": "a" * 40}}


def test_host_metadata_validates_selected_git_and_index_without_executing_site(tmp_path):
    distribution(tmp_path, "heinzel", "0.4.0", git_source("https://example.test/heinzel", "v0.4.0"))
    distribution(tmp_path, "heinzel-other", "1.2.0")
    site = tmp_path / "lib/python3.14/site-packages"
    marker = tmp_path / "ran"
    result = inspect_requirements(
        tmp_path, ["heinzel @ git+https://example.test/heinzel@v0.4.0", "heinzel-other>=1"], "0.4.0"
    )
    assert result == {"heinzel": "0.4.0", "heinzel-other": "1.2.0"}
    (site / "hostile.pth").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).touch()\n")
    with pytest.raises(HeinzelError, match="startup paths"):
        inspect_requirements(tmp_path, ["heinzel @ git+https://example.test/heinzel@v0.4.0"], "0.4.0")
    assert not marker.exists()


@pytest.mark.parametrize("bad", ["version", "source", "revision", "duplicate", "missing"])
def test_host_metadata_rejects_wrong_or_ambiguous_artifact(tmp_path, bad):
    source = git_source(
        "https://example.test/other" if bad == "source" else "https://example.test/heinzel",
        "v0.3.0" if bad == "revision" else "v0.4.0",
    )
    if bad != "missing":
        distribution(tmp_path, "heinzel", "0.3.0" if bad == "version" else "0.4.0", source)
    if bad == "duplicate":
        distribution(tmp_path, "heinzel", "0.4.1", source)
    with pytest.raises(HeinzelError, match="metadata does not match"):
        inspect_requirements(tmp_path, ["heinzel @ git+https://example.test/heinzel@v0.4.0"], "0.4.0")


def test_wheel_source_checks_the_selected_hash(tmp_path):
    digest = "a" * 64
    url = "https://example.test/heinzel-0.4.0-py3-none-any.whl"
    distribution(tmp_path, "heinzel", "0.4.0", {"url": url, "archive_info": {"hashes": {"sha256": digest}}})
    assert inspect_requirements(tmp_path, [f"heinzel @ {url}#sha256={digest}"], "0.4.0")["heinzel"] == "0.4.0"
    with pytest.raises(HeinzelError, match="metadata does not match"):
        inspect_requirements(tmp_path, [f"heinzel @ {url}#sha256={'b' * 64}"], "0.4.0")


def test_local_wheel_hash_can_be_recorded_in_source_fragment(tmp_path):
    digest = "c" * 64
    url = "file:///tmp/heinzel-0.4.0-py3-none-any.whl"
    distribution(tmp_path, "heinzel", "0.4.0", {"url": f"{url}#sha256={digest}", "archive_info": {}})
    assert inspect_requirements(tmp_path, [f"heinzel @ {url}#sha256={digest}"], "0.4.0")["heinzel"] == "0.4.0"


def test_dropped_distribution_must_be_absent_but_unrelated_dependencies_are_allowed(tmp_path):
    distribution(tmp_path, "heinzel", "0.4.0")
    distribution(tmp_path, "ordinary-dependency", "1.0.0")
    assert inspect_requirements(tmp_path, ["heinzel==0.4.0"], "0.4.0", forbidden=("heinzel-dropped",)) == {
        "heinzel": "0.4.0"
    }
    distribution(tmp_path, "heinzel_dropped", "1.0.0")
    with pytest.raises(HeinzelError, match="metadata does not match"):
        inspect_requirements(tmp_path, ["heinzel==0.4.0"], "0.4.0", forbidden=("heinzel-dropped",))


def test_safe_in_prefix_pth_extension_is_included_in_selected_and_dropped_metadata(tmp_path):
    distribution(tmp_path, "heinzel", "0.4.0")
    site = tmp_path / "lib/python3.14/site-packages"
    extension = site / "extra"
    info = extension / "heinzel_dropped-1.0.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-dropped\nVersion: 1.0.0\n")
    (site / "paths.pth").write_text("# safe path\nextra\n")
    assert inspect_requirements(tmp_path, ["heinzel==0.4.0", "heinzel-dropped==1.0.0"], "0.4.0") == {
        "heinzel": "0.4.0",
        "heinzel-dropped": "1.0.0",
    }
    with pytest.raises(HeinzelError, match="metadata does not match"):
        inspect_requirements(tmp_path, ["heinzel==0.4.0"], "0.4.0", forbidden=("heinzel-dropped",))


@pytest.mark.parametrize(
    "kind", ["executable", "external", "missing", "oversize", "file", "sitecustomize", "usercustomize", "symlink"]
)
def test_unsupported_tool_startup_paths_refuse_without_executing_them(tmp_path, kind):
    distribution(tmp_path, "heinzel", "0.4.0")
    site = tmp_path / "lib/python3.14/site-packages"
    marker = tmp_path / "executed"
    if kind in {"sitecustomize", "usercustomize"}:
        (site / f"{kind}.py").write_text(f"open({str(marker)!r}, 'w').write('bad')")
    else:
        values = {
            "executable": f"import pathlib; pathlib.Path({str(marker)!r}).touch()\n",
            "external": "/outside/fixture\n",
            "missing": "missing\n",
            "oversize": "x" * 65537,
            "file": "archive.zip\n",
            "symlink": "link\n",
        }
        if kind == "file":
            (site / "archive.zip").write_bytes(b"fixture")
        if kind == "symlink":
            (site / "real").mkdir()
            (site / "link").symlink_to(site / "real", target_is_directory=True)
        (site / "startup.pth").write_text(values[kind])
    with pytest.raises(HeinzelError, match=r"cannot safely inspect.*startup paths"):
        inspect_requirements(tmp_path, ["heinzel==0.4.0"], "0.4.0")
    assert not marker.exists()


def test_editable_tool_pointing_outside_prefix_refuses_with_repair_guidance(tmp_path):
    distribution(tmp_path, "heinzel", "0.4.0")
    site = tmp_path / "lib/python3.14/site-packages"
    outside = tmp_path.parent / "external-source"
    outside.mkdir(exist_ok=True)
    (site / "_editable_impl_heinzel.pth").write_text(str(outside) + "\n")
    with pytest.raises(HeinzelError, match=r"reinstall.*verified sources"):
        inspect_requirements(tmp_path, ["heinzel==0.4.0"], "0.4.0")


def test_initial_version_observation_does_not_approve_file_source(tmp_path):
    from heinzel.kernel._installed_metadata import inspect_core_version

    distribution(tmp_path, "heinzel", "0.9.7", {"url": "file:///tmp/heinzel.whl", "archive_info": {}})
    assert inspect_core_version(tmp_path) == "0.9.7"
    with pytest.raises(HeinzelError, match="metadata does not match"):
        inspect_requirements(tmp_path, ["heinzel"], None)


@pytest.mark.parametrize("problem", ["duplicate", "missing", "invalid", "unsafe_startup"])
def test_initial_version_refuses_ambiguous_or_unsafe_metadata(tmp_path, problem):
    from heinzel.kernel._installed_metadata import inspect_core_version

    if problem != "missing":
        distribution(tmp_path, "heinzel", "invalid" if problem == "invalid" else "0.9.7")
    if problem == "duplicate":
        distribution(tmp_path, "heinzel", "0.9.8")
    if problem == "unsafe_startup":
        (tmp_path / "lib/python3.14/site-packages/bad.pth").write_text("import os\n")
    with pytest.raises(HeinzelError, match="cannot be safely observed"):
        inspect_core_version(tmp_path)


def test_index_requirement_uses_captured_wheel_constraint_source_and_all_versions(tmp_path):
    digest = "a" * 64
    wheel = "file:///fixture/heinzel-0.9.7-py3-none-any.whl"
    info = distribution(tmp_path, "heinzel", "0.9.7", {"url": wheel, "archive_info": {"hashes": {"sha256": digest}}})
    constraint = f"heinzel @ {wheel}#sha256={digest}"
    assert inspect_requirements(tmp_path, ["heinzel>=0.9"], None, constraints=(constraint, "heinzel<1")) == {
        "heinzel": "0.9.7"
    }
    for direct in (
        {"url": wheel.replace("0.9.7", "0.9.8"), "archive_info": {"hashes": {"sha256": digest}}},
        {"url": wheel, "archive_info": {"hashes": {"sha256": "b" * 64}}},
    ):
        (info / "direct_url.json").write_text(json.dumps(direct))
        with pytest.raises(HeinzelError, match="metadata does not match"):
            inspect_requirements(tmp_path, ["heinzel>=0.9"], None, constraints=(constraint,))


def test_constraint_version_is_enforced_with_direct_source(tmp_path):
    wheel = "file:///fixture/heinzel-0.9.7-py3-none-any.whl"
    distribution(tmp_path, "heinzel", "0.9.7", {"url": wheel, "archive_info": {}})
    with pytest.raises(HeinzelError, match="metadata does not match"):
        inspect_requirements(tmp_path, ["heinzel>=0.9"], None, constraints=(f"heinzel @ {wheel}", "heinzel<0.9.7"))
