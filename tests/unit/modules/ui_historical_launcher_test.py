"""The retained historical web launcher renders the exact script verified migration compares against, and it runs."""

import subprocess
from pathlib import Path

import pytest

from heinzel.modules.ui._historical_launcher import HistoricalLauncher

pytestmark = pytest.mark.unit


@pytest.fixture
def launcher(tmp_path: Path) -> HistoricalLauncher:
    root = tmp_path.resolve()
    executable = root / "bin" / "hz"
    executable.parent.mkdir()
    # Stands in for hz: prints its arguments and the environment it was started with.
    executable.write_text('#!/bin/sh\nprintf "ARGS:%s\\n" "$*"\nenv\n')
    executable.chmod(0o755)
    (root / "uvbin").mkdir()
    return HistoricalLauncher(
        prefix=root / "prefix",
        directory=root / "state" / "ui",
        executable=executable,
        uv=root / "uvbin" / "uv",
        profile={"HZ_HOME": str(root / "home"), "HOME": str(root / "home")},
    )


def _run(script: str, tmp_path: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "launcher.sh"
    path.write_text(script)
    return subprocess.run(
        ["/bin/sh", str(path)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", **environment},
    )


def test_the_rendered_script_serves_on_the_port_with_the_generation_and_a_clean_environment(launcher, tmp_path):
    result = _run(
        launcher.render(8123, "gen-7"),
        tmp_path,
        HZ_SECRET="must-not-leak",
        LANG="en_US.UTF-8",
        UNLISTED="dropped",
    )

    assert result.returncode == 0, result.stderr
    assert "ARGS:ui serve --no-browser --port 8123 --supervised gen-7" in result.stdout
    environment = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line and not line.startswith("ARGS:")
    )
    assert environment["LANG"] == "en_US.UTF-8"  # an OS-session variable is forwarded
    assert environment["HZ_HOME"] == launcher.profile["HZ_HOME"]
    assert "HZ_SECRET" not in environment
    assert "UNLISTED" not in environment
    assert environment["PATH"].split(":")[:2] == [str(launcher.uv.parent), str(launcher.executable.parent)]


def test_the_legacy_form_leaves_out_the_serve_word(launcher):
    script = launcher.render(9000, "g", legacy=True)

    assert " ui --no-browser --port 9000 --supervised g" in script
    assert " ui serve " in launcher.render(9000, "g")


def test_a_profile_given_to_render_replaces_the_launchers_own(launcher, tmp_path):
    result = _run(launcher.render(1, "g", {"ONLY_THIS": "yes"}), tmp_path)

    assert "ONLY_THIS=yes" in result.stdout.splitlines()
    assert not [line for line in result.stdout.splitlines() if line.startswith("HZ_HOME=")]


@pytest.mark.parametrize(
    "pending",
    ["state/auto-update/before-update", "state/daemon/mutation-pending.json", "prefix/.heinzel-startup-pending"],
)
def test_a_pending_package_mutation_stops_the_launcher_before_hz_starts(launcher, tmp_path, pending):
    marker = tmp_path.resolve() / pending
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending")

    result = _run(launcher.render(8123, "g"), tmp_path)

    assert result.returncode == 75
    assert "ARGS:" not in result.stdout


def test_a_dangling_link_at_a_mutation_path_also_stops_it(launcher, tmp_path):
    marker = tmp_path.resolve() / "state" / "daemon" / "standard-install-pending.json"
    marker.parent.mkdir(parents=True)
    Path(marker).symlink_to(tmp_path / "nowhere")

    assert _run(launcher.render(8123, "g"), tmp_path).returncode == 75
