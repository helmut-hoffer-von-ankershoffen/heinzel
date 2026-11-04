"""Every browser-test host installs both engines before running the mounted journeys."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("platform", ["Linux", "Darwin"])
def test_browser_setup_installs_both_engines_and_only_linux_system_packages(tmp_path, platform):
    commands = tmp_path / "bin"
    commands.mkdir()
    uname = commands / "uname"
    uname.write_text(f"#!/bin/sh\nprintf '%s\\n' {platform}\n")
    uname.chmod(0o700)
    output = tmp_path / "arguments.json"
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['BROWSER_SETUP_RECORD']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    environment = {
        **os.environ,
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "BROWSER_SETUP_RECORD": str(output),
    }
    subprocess.run(
        ["make", "--no-print-directory", "test-browser-deps", "WEB=" + shlex.join([sys.executable, str(recorder)])],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    expected = ["exec", "playwright-core", "install"]
    if platform == "Linux":
        expected.append("--with-deps")
    assert json.loads(output.read_text()) == [*expected, "chromium", "webkit"]


def test_every_e2e_matrix_host_installs_its_engines_before_tests():
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["e2e"]["steps"]
    setup = next(step for step in steps if step.get("run") == "make test-browser-deps")
    run = next(step for step in steps if step.get("run") == "make test-e2e SUITE=${{ matrix.suite }}")
    # Browser and supervised journeys need engines; the tool-install and CLI suite skips the install.
    assert setup["if"] == "matrix.suite == 'supervised' || startsWith(matrix.suite, 'web')"
    assert steps.index(setup) < steps.index(run)
    assert "make test-browser-deps" not in (REPO / "template/.github/workflows/ci.yml.jinja").read_text()


def test_docs_browser_dependencies_use_primary_archive_without_changing_signed_package_sources(tmp_path):
    workflow = yaml.safe_load((REPO / ".github/workflows/docs.yml").read_text())
    site = workflow["jobs"]["site"]
    steps = site["steps"]
    [archive] = [step for step in steps if step.get("name") == "Use Ubuntu's primary archive for browser dependencies"]
    [build] = [
        step
        for step in steps
        if step.get("name") == "Build and verify the product site or a standalone documentation site"
    ]
    assert site["runs-on"] == "ubuntu-24.04"
    assert site["timeout-minutes"] == 15
    assert steps.index(archive) < steps.index(build)
    assert "if" not in archive
    assert "continue-on-error" not in archive
    command = shlex.split(archive["run"])
    assert command[:3] == ["sudo", "sed", "-i"]
    assert command[-2:] == ["/etc/apt/apt-mirrors.txt", "/etc/apt/sources.list.d/ubuntu.sources"]
    source = (
        "Types: deb\nURIs: http://azure.archive.ubuntu.com/ubuntu\n"
        "Suites: noble noble-updates noble-backports\nComponents: main universe restricted multiverse\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n\n"
        "Types: deb\nURIs: http://security.ubuntu.com/ubuntu\nSuites: noble-security\n"
        "Components: main universe restricted multiverse\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    mirrors = tmp_path / "apt-mirrors.txt"
    mirrors.write_text("http://azure.archive.ubuntu.com/ubuntu/\nhttp://archive.ubuntu.com/ubuntu/\n")
    mirror_sources = (
        "URIs: mirror+file:/etc/apt/apt-mirrors.txt\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    # The failed runner used mirror+file; both that list and direct Deb822 Azure URIs must be covered.
    mirrored = tmp_path / "mirrored.sources"
    mirrored.write_text(mirror_sources)
    for fixture in (mirrors, mirrored):
        result = subprocess.run(
            ["sed", command[3], str(fixture)], check=True, capture_output=True, text=True, timeout=5
        )
        fixture.write_text(result.stdout)
    assert mirrors.read_text() == "https://archive.ubuntu.com/ubuntu/\nhttp://archive.ubuntu.com/ubuntu/\n"
    assert mirrored.read_text() == mirror_sources
    sources = tmp_path / "ubuntu.sources"
    sources.write_text(source)
    # Execute the actual workflow substitution against a private fixture, without sudo or host changes.
    result = subprocess.run(["sed", command[3], str(sources)], check=True, capture_output=True, text=True, timeout=5)
    sources.write_text(result.stdout)
    assert sources.read_text() == source.replace(
        "http://azure.archive.ubuntu.com/ubuntu", "https://archive.ubuntu.com/ubuntu"
    )
    result = subprocess.run(["sed", command[3], str(sources)], check=True, capture_output=True, text=True, timeout=5)
    sources.write_text(result.stdout)
    assert sources.read_text().count("https://archive.ubuntu.com/ubuntu") == 1
    assert "--install $(if $(CI),--with-deps)" in (REPO / "Makefile").read_text()
    installer = (REPO / "scripts/site_check.py").read_text().partition('if __name__ == "__main__":')[2]
    assert '"chromium",' in installer
    assert '"webkit",' in installer
    assert '"--with-deps"' in installer
    assert "timeout=600" in installer
