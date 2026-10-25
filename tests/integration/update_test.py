"""`hz system update` through the CLI with a fake `uv` on PATH, a uv tool receipt and a fake GitHub. Offline.

The fake `uv` rewrites the receipt and the version a fresh `hz` reports, as a real `uv tool install --force` would, or
fails resolving the way uv does when told to; the fresh `hz` is a script standing in for the tool's interpreter.
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
import pathlib, sys
env = pathlib.Path({env!r})
with (env / "uv.log").open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
if (env / "block").exists() and "--with" in sys.argv:
    sys.stderr.write("error: No solution found when resolving dependencies\\n  cause: Because heinzel-acme==1.0.0 "
                     "depends on heinzel<0.3 and you require heinzel, we can conclude that your requirements are "
                     "unsatisfiable.\\n")
    sys.exit(1)
core = sys.argv[sys.argv.index("--python") + 2]
url, tag = core.split(" @ git+", 1)[1].rsplit("@", 1)
if tag.startswith("v"):
    (env / "version").write_text(tag[1:])
elif (env / "main-version").exists():  # what the branch has now
    (env / "version").write_text((env / "main-version").read_text())
receipt = (env / "uv-receipt.toml").read_text().splitlines()
receipt[2] = '    {{ name = "heinzel", git = "' + url + '?rev=' + tag + '" }},'
(env / "uv-receipt.toml").write_text("\\n".join(receipt) + "\\n")
"""
FAKE_HZ = """#!/bin/sh
case "$*" in
*"plugin list"*)
  printf '[{{"distribution": "heinzel-acme", "version": "1.0.0"}}]'
  ;;
*"daemon restart"*)
  printf '{{"restarted": false}}'
  ;;
*)
  printf 'hz %s\\n' "$(cat {env}/version)"
  ;;
esac
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A uv tool environment pinned to the running version's tag, with one plugin; fake uv and hz; fake GitHub."""
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    (env / "version").write_text(__version__)
    (env / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = [\n"
        f'    {{ name = "heinzel", git = "{REPO}?rev=v{__version__}" }},\n'
        f'    {{ name = "heinzel-acme", git = "{REPO}-acme?rev=v1.0.0" }},\n'
        "]\n"
    )
    for name, script in {
        "uv": FAKE_UV.format(python=sys.executable, env=str(env)),
        "hz": FAKE_HZ.format(env=env),
    }.items():
        (env / "bin" / name).write_text(script)
        (env / "bin" / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{env / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(sys, "executable", str(env / "bin" / "hz"))  # the fresh `hz` that reports the version
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
    assert command.startswith("tool install --force --refresh ")
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
