"""`hz system update` through the CLI with a fake `uv` on PATH, a uv tool receipt and a fake GitHub. Offline.

The fake `uv` updates an isolated receipt and distribution metadata or simulates a resolver failure.
An isolated Python wrapper answers the target version and plugin-list probes without running installed code.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import __version__
from heinzel.heads.cli import build
from heinzel.modules.system import _github

pytestmark = pytest.mark.integration

runner = CliRunner()
REPO = "https://github.com/example-org/heinzel"
PLUGIN = "heinzel-acme @ git+https://github.com/example-org/heinzel-acme@v1.0.0"

FAKE_UV = """#!{python}
import json, pathlib, shutil, sys
root = pathlib.Path({env!r})
binary = pathlib.Path({binary!r})
python = pathlib.Path({fake_python!r})
assert root.resolve().is_relative_to(pathlib.Path({test_root!r}).resolve())
assert sys.argv[sys.argv.index("--python") + 1] == str(python)
assert pathlib.Path(__import__("os").environ["UV_TOOL_DIR"]) == root.parent
assert pathlib.Path(__import__("os").environ["UV_TOOL_BIN_DIR"]) == binary
if "--constraints" in sys.argv:
    constraint = pathlib.Path(sys.argv[sys.argv.index("--constraints") + 1])
    assert constraint.read_text() == "heinzel-acme<2\\n"
with (root / "uv.log").open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
if (root / "block").exists() and "--with" in sys.argv:
    sys.stderr.write("error: No solution found when resolving dependencies\\n  cause: Because heinzel-acme==1.0.0 "
                     "depends on heinzel<0.3 and you require heinzel, we can conclude that your requirements are "
                     "unsatisfiable.\\n")
    sys.exit(1)
core = sys.argv[sys.argv.index("--python") + 2]
url, tag = core.split(" @ git+", 1)[1].rsplit("@", 1)
version = tag[1:] if tag.startswith("v") else (root / "main-version").read_text()
(root / "version").write_text(version)
site = root / "lib/python3.14/site-packages"
for old in site.glob("heinzel-*.dist-info"):
    assert old.resolve().is_relative_to(root.resolve()) and not old.is_symlink()
    shutil.rmtree(old)
metadata = site / ("heinzel-" + version + ".dist-info")
metadata.mkdir()
(metadata / "METADATA").write_text("Metadata-Version: 2.1\\nName: heinzel\\nVersion: " + version + "\\n")
observed = url + ("-wrong" if (root / "wrong-source").exists() else "")
source = {{"url":observed,"vcs_info":{{"vcs":"git", "requested_revision":tag,"commit_id":"a" * 40}}}}
(metadata / "direct_url.json").write_text(json.dumps(source))
if "--with" not in sys.argv:
    plugin = site / "heinzel_acme-1.0.0.dist-info"
    assert plugin.resolve().is_relative_to(root.resolve()) and not plugin.is_symlink()
    shutil.rmtree(plugin)
receipt = (root / "uv-receipt.toml").read_text().splitlines()
receipt[2] = '    {{ name = "heinzel", git = "' + url + '?rev=' + tag + '" }},'
if "--with" not in sys.argv:
    receipt = [line for line in receipt if 'name = "heinzel-acme"' not in line]
(root / "uv-receipt.toml").write_text("\\n".join(receipt) + "\\n")
"""
FAKE_PYTHON = """#!{python}
import json, pathlib, sys
root = pathlib.Path({env!r})
assert sys.argv[1:4] == ["-I", "-m", "heinzel"]
if (root / "uv.log").exists():
    with (root.parent.parent / "post-uv-probes").open("a") as out: out.write("probe\\n")
if sys.argv[4:7] == ["system", "plugin", "list"]:
    info = root / "lib/python3.14/site-packages/heinzel_acme-1.0.0.dist-info"
    print(json.dumps([{{"distribution":"heinzel-acme", "version":"1.0.0"}}] if info.exists() else []))
else:
    assert sys.argv[4:] == ["--version"]
    print("hz " + (root / "version").read_text())
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private receipt, physical metadata and disposable interpreter; fake uv and GitHub stay offline."""
    original_python = Path(sys.executable).resolve(strict=True)
    original_base = Path(sys._base_executable).resolve(strict=True)
    env = tmp_path / "tools" / "heinzel"
    (env / "bin").mkdir(parents=True)
    (env / "version").write_text(__version__)
    (env / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = [\n"
        f'    {{ name = "heinzel", git = "{REPO}?rev=v{__version__}" }},\n'
        f'    {{ name = "heinzel-acme", git = "{REPO}-acme?rev=v1.0.0" }},\n'
        "]\n"
    )
    binary = tmp_path / "entrypoints"
    binary.mkdir()
    (env / "bin/hz").write_text("owned fixture entrypoint")
    (binary / "hz").symlink_to(env / "bin/hz")
    receipt = env / "uv-receipt.toml"
    receipt.write_text(
        receipt.read_text() + f"entrypoints = [{{ name = 'hz', install-path = {str(binary / 'hz')!r} }}]\n"
    )
    fake_python = tmp_path / "fake-python"
    assert fake_python.parent.resolve() == tmp_path.resolve()
    assert not fake_python.exists()
    assert not fake_python.is_symlink()
    fake_python.write_text(FAKE_PYTHON.format(python=original_python, env=str(env)))
    assert fake_python.resolve().is_relative_to(tmp_path.resolve())
    assert not fake_python.samefile(original_python)
    assert not fake_python.samefile(original_base)
    fake_python.chmod(0o700)
    (env / "bin/python").symlink_to(fake_python)
    uv = env / "bin/uv"
    assert uv.parent.resolve().is_relative_to(tmp_path.resolve())
    assert not uv.exists()
    assert not uv.is_symlink()
    uv.write_text(
        FAKE_UV.format(
            python=original_python,
            env=str(env),
            binary=str(binary),
            fake_python=str(fake_python),
            test_root=str(tmp_path),
        )
    )
    assert uv.resolve().is_relative_to(tmp_path.resolve())
    assert not uv.samefile(original_python)
    assert not uv.samefile(original_base)
    uv.chmod(0o700)
    site = env / "lib/python3.14/site-packages"
    for name, version, url, revision in (
        ("heinzel", __version__, REPO, f"v{__version__}"),
        ("heinzel_acme", "1.0.0", REPO + "-acme", "v1.0.0"),
    ):
        metadata = site / f"{name}-{version}.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        (metadata / "direct_url.json").write_text(
            json.dumps({"url": url, "vcs_info": {"vcs": "git", "requested_revision": revision, "commit_id": "a" * 40}})
        )
    monkeypatch.setenv("PATH", f"{env / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(sys, "executable", str(fake_python))
    return env


def github_releases(monkeypatch, *tags):
    def get_json(url, token, timeout):
        assert url == "https://api.github.com/repos/example-org/heinzel/releases?per_page=30&page=1"
        assert token is None
        return [{"tag_name": tag, "draft": False, "prerelease": False} for tag in tags]

    monkeypatch.setattr(_github, "get_json", get_json)


@pytest.fixture
def app(make_kernel):
    return build(make_kernel(builtins=True))


def uv_log(env: Path) -> list[str]:
    log = env / "uv.log"
    return log.read_text().splitlines() if log.exists() else []


def test_a_tag_pinned_install_updates_to_the_latest_release_and_keeps_plugins(app, env, monkeypatch):
    github_releases(monkeypatch, "v99.0.0", f"v{__version__}", "v99.1.0rc1")
    result = runner.invoke(app, ["system", "update"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"Updated hz {__version__} → 99.0.0.\nPlugins unchanged: heinzel-acme."
    [command] = uv_log(env)
    assert command.startswith("tool install --reinstall --refresh ")
    assert command.endswith(f"heinzel @ git+{REPO}@v99.0.0 --with {PLUGIN}")
    assert "rev=v99.0.0" in (env / "uv-receipt.toml").read_text()


def test_json_carries_before_after_latest_and_the_pin(app, env, monkeypatch):
    github_releases(monkeypatch, "v99.0.0")
    invoked = runner.invoke(app, ["system", "update", "--json"])
    assert invoked.exit_code == 0, invoked.output
    result = json.loads(invoked.output)
    assert {k: result[k] for k in ("before", "after", "latest", "pinned_to", "updated")} == {
        "before": __version__,
        "after": "99.0.0",
        "latest": "99.0.0",
        "pinned_to": "v99.0.0",
        "updated": True,
    }


def test_the_latest_release_installed_is_said_not_silent(app, env, monkeypatch):
    github_releases(monkeypatch, f"v{__version__}")
    result = runner.invoke(app, ["system", "update"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"hz {__version__} is up to date (releases)."
    assert uv_log(env) == []


def test_a_main_pin_is_updated_in_place_to_the_newest_commit(app, env, monkeypatch):
    receipt = env / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace(f"rev=v{__version__}", "rev=main"))
    (env / "main-version").write_text("99.0.0.dev7")
    github_releases(monkeypatch, "v0.0.1")
    result = runner.invoke(app, ["system", "update", "--channel", "main"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"Updated hz {__version__} \u2192 99.0.0.dev7.\nPlugins unchanged: heinzel-acme."
    [command] = uv_log(env)
    assert command.endswith(f"heinzel @ git+{REPO}@main --with {PLUGIN}")
    assert "rev=main" in receipt.read_text()


def test_a_branch_pin_is_left_alone_with_the_command_that_moves_it(app, env, monkeypatch):
    receipt = env / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace(f"rev=v{__version__}", "rev=feature"))
    github_releases(monkeypatch, "v99.0.0")
    result = runner.invoke(app, ["system", "update"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert (
        f"Pinned to feature; to get its newest commit run: hz system update --source git+{REPO}@feature"
        in result.output
    )
    assert f"run hz system update --source git+{REPO}@v99.0.0 to switch to releases" in " ".join(result.output.split())
    assert uv_log(env) == []


def test_a_blocking_plugin_is_taken_out_and_hz_still_updates(app, env, monkeypatch):
    (env / "block").touch()
    github_releases(monkeypatch, "v99.0.0")
    result = runner.invoke(app, ["system", "update"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert f"Updated hz {__version__} → 99.0.0." in output
    assert "Plugin heinzel-acme: failed." in output
    assert f"hz system plugin add '{PLUGIN}' --confirm" in output
    first, second, third = uv_log(env)
    assert first == second  # kept at its spec, which fails too
    assert "--with" not in third  # then hz alone
    assert "rev=v99.0.0" in (env / "uv-receipt.toml").read_text()


def test_json_reports_each_plugin(app, env, monkeypatch):
    (env / "block").touch()
    github_releases(monkeypatch, "v99.0.0")
    invoked = runner.invoke(app, ["system", "update", "--json"])
    assert invoked.exit_code == 0, invoked.output
    [acme] = json.loads(invoked.output)["plugins"]
    assert (acme["distribution"], acme["status"], acme["disabled"]) == ("heinzel-acme", "failed", True)
    assert acme["next"] == [f"hz system plugin add '{PLUGIN}' --confirm"]


def test_channel_release_switches_a_main_pin_to_the_newest_release(app, env, monkeypatch):
    receipt = env / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace(f"rev=v{__version__}", "rev=main"))
    github_releases(monkeypatch, "v99.0.0")
    result = runner.invoke(app, ["system", "update"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines()[0] == f"Updated hz {__version__} \u2192 99.0.0."
    assert "rev=v99.0.0" in receipt.read_text()


def test_existing_constraints_are_used_for_real_fake_uv_subprocess(app, env, monkeypatch):
    receipt = env / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'constraints = [{name = "heinzel-acme", specifier = "<2"}]\n')
    github_releases(monkeypatch, "v99.0.0")
    result = runner.invoke(app, ["system", "update"])
    assert result.exit_code == 0, result.output
    assert "--constraints" in uv_log(env)[0]


def test_wrong_installed_source_never_starts_the_changed_interpreter(app, env, monkeypatch):
    (env / "wrong-source").touch()
    github_releases(monkeypatch, "v99.0.0")
    result = runner.invoke(app, ["system", "update"])
    assert result.exit_code != 0
    assert uv_log(env)
    assert not (env.parent.parent / "post-uv-probes").exists()
