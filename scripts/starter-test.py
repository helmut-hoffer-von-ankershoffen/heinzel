# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Exercise fresh standalone and collection authors against installed core and toolkit artifacts."""

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.4.1/heinzel_plugin_tools-0.4.1-py3-none-any.whl"
    "#sha256=56072dabdd2a7c94f5210b10dedb85099d6ad67f19a58e37949a05d4b830ad38"
)


def _process_group_exists(group: int) -> bool:
    """Whether a POSIX child process group still has members."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_process_group(process: subprocess.Popen[str], grace: float = 2.0) -> None:
    """Stop and reap a command and any descendants it left in its isolated session."""
    if os.name != "posix":
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return

    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace)
    deadline = time.monotonic() + grace
    while _process_group_exists(process.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _process_group_exists(process.pid):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def run_process(command: list[str], cwd: Path, environment: dict[str, str], output: TextIO, timeout: int = 600) -> int:
    """Run one journey command in a private process group, cleaning descendants on interruption."""
    process = subprocess.Popen(  # noqa: S603 - explicit tools and artifact paths, no shell
        command,
        cwd=cwd,
        env=environment,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
        text=True,
    )
    try:
        returncode = process.wait(timeout=timeout)
    except BaseException:
        _stop_process_group(process)
        raise
    return returncode


def journey_environment(destination: Path) -> dict[str, str]:
    """Build a disposable process environment for one generated plugin journey."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    environment.pop("VIRTUAL_ENV", None)
    # Tool installs hardlink cache files; a caller cache may retain unsafe modes from unrelated builds.
    environment["UV_CACHE_DIR"] = str(destination / "uv-cache")
    environment["UV_TOOL_DIR"] = str(destination / "uv-tool")
    shim_find_links(environment, destination / "uv-shim")
    environment["HZ_PLUGINS_ENABLED"] = "false"
    for name in ("CONFIG_HOME", "DATA_DIR", "CACHE_DIR", "STATE_DIR", "LOG_DIR"):
        environment[f"HZ_{name}"] = str(destination / "state" / name.lower())
    return environment


def check_layout(directory: Path, *, collection: bool) -> None:
    """Reject copied governance or an uninitialized standalone repository.

    Raises:
        AssertionError: If the generated layout violates its ownership contract.
    """
    if collection:
        valid = (directory / "Makefile").is_file() and not any(
            (directory / path).exists() for path in (".git", ".github")
        )
    else:
        valid = (directory / ".git").is_dir() and not (directory / ".claude/agents").exists()
    if not valid:
        msg = f"Invalid {'collection' if collection else 'standalone'} layout: {directory}"
        raise AssertionError(msg)


def add_first_operation(directory: Path) -> None:
    """Make a real authored change and test its installed CLI result, rather than only the untouched scaffold."""
    metadata = tomllib.loads((directory / "pyproject.toml").read_text())
    name, entry = next(iter(metadata["project"]["entry-points"]["heinzel.modules"].items()))
    package_name = entry.split(":")[0]
    package = directory / "src" / package_name.replace(".", "/") / "__init__.py"
    package.write_text(
        package.read_text()
        + '\n\n@module.operation\ndef double(value: int) -> int:\n    """Double a number."""\n    return value * 2\n'
    )
    (directory / "tests/double_test.py").write_text(
        f"import pytest\nfrom {package_name} import double\npytestmark = pytest.mark.unit\n"
        "def test_doubles_a_number():\n    assert double(7) == 14\n"
    )
    (directory / "tests/acceptance/double_test.py").write_text(
        "import json\nimport os\nimport subprocess\nimport sys\nimport pytest\npytestmark = pytest.mark.e2e\n"
        "def test_installed_double(tmp_path):\n"
        '    environment = dict(os.environ, HZ_PLUGINS_ENABLED="true")\n'
        '    environment.pop("PYTHONPATH", None)\n'
        f'    result = subprocess.run([sys.executable, "-m", "heinzel", "{name}", "double", "7", "--json"], '
        "cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=30)\n"
        "    assert result.returncode == 0, result.stderr\n"
        "    assert json.loads(result.stdout) == 14\n"
    )


def check_collection_message(log: Path) -> None:
    """Keep printed next steps aligned with the owning Makefile.

    Raises:
        AssertionError: If the scaffold sends an author into unrelated host commands.
    """
    message = log.read_text()
    if "&& make setup" not in message or "make check-fast NAME=" in message or "make lock NAME=" in message:
        msg = "Collection scaffold must display the owning-directory setup and check commands"
        raise AssertionError(msg)


def uv_shim(directory: Path, find_links: str) -> Path:
    """A fixture uv wrapper that scopes reviewed PyYAML to resolver arguments, never descendant environment.

    uv saves an inherited find-links in the tool receipt as resolver policy, and the installer rightly refuses a
    receipt with options. A user's install never has them, so the shim removes them as soon as uv exits.

    Raises:
        RuntimeError: If uv is not on PATH.
    """
    directory.mkdir(parents=True, exist_ok=True)
    real = shutil.which("uv")
    if not real:
        msg = "uv is not on PATH"
        raise RuntimeError(msg)
    script = directory / "uv"
    script.write_text(
        f"#!{sys.executable} -I\n"
        "import glob, os, re, subprocess, sys\n"
        "env = {key: value for key, value in os.environ.items() if key != 'UV_FIND_LINKS'}\n"
        "args = sys.argv[1:]\n"
        "position = 1 if args[:1] and args[0] in {'run', 'sync', 'lock', 'build', 'export'} else 0\n"
        "if args[:2] in (['tool', 'run'], ['tool', 'install'], ['pip', 'install'], "
        "['pip', 'sync'], ['pip', 'compile']):\n"
        "    position = 2\n"
        "if position:\n"
        f"    args[position:position] = ['--find-links', {find_links!r}]\n"
        f"code = subprocess.call([{real!r}, *args], env=env)\n"
        "for path in glob.glob(os.path.join(os.environ.get('UV_TOOL_DIR', ''), '*', 'uv-receipt.toml')):\n"
        "    text = open(path, encoding='utf-8').read()\n"
        "    new = re.sub(r'\\n\\[tool\\.options\\]\\nfind-links = \\[[^\\]]*\\]\\n', '\\n', text)\n"
        "    if new != text:\n"
        "        open(path, 'w', encoding='utf-8').write(new.rstrip('\\n') + '\\n')\n"
        "sys.exit(code)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return directory


def shim_find_links(environment: dict[str, str], directory: Path) -> None:
    """Replace an inherited UV_FIND_LINKS by the shim that scopes it to uv's own process."""
    if find_links := environment.pop("UV_FIND_LINKS", None):
        wrapper = uv_shim(directory, find_links)
        environment["PATH"] = f"{wrapper}{os.pathsep}{environment.get('PATH', '')}"
        # uv run prepends its environment's bin directory; leaf acceptance installs must keep this wrapper.
        environment["PLUGIN_TEST_UV"] = str(wrapper / "uv")


def journey(core: str, toolkit: str | None, template: Path, destination: Path) -> None:
    """The documented commands must work without copying configuration or importing core sources.

    Raises:
        AssertionError: If scaffolding changes files outside its owning project directories.
    """
    environment = journey_environment(destination)
    timings: list[dict[str, str | float]] = []
    make = ["make", f"CORE={core}", *([f"TOOLING={toolkit}"] if toolkit else [])]

    def run(command: list[str], cwd: Path, label: str) -> None:
        started = time.monotonic()
        log = destination / f"{label}.log"
        with log.open("w") as stream:
            returncode = run_process(command, cwd, environment, stream)
        timings.append({"step": label, "seconds": round(time.monotonic() - started, 2)})
        (destination / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")
        if returncode:
            print(log.read_text())
            raise subprocess.CalledProcessError(returncode, command)
        print(f"✓ {label}: {timings[-1]['seconds']}s", flush=True)

    for preset in ("minimal", "contributions"):
        directory = destination / f"heinzel-{preset}"
        run(
            [
                "uv",
                "tool",
                "run",
                "--from",
                core,
                "hz",
                "system",
                "plugin",
                "new",
                str(directory),
                "--defaults",
                "--template",
                str(template),
                "--ref",
                "HEAD",
                "--preset",
                preset,
                "--data",
                f"plugin_name={preset}",
            ],
            destination,
            f"{preset}-scaffold",
        )
        for target in ("setup", "check-fast"):
            run([*make, target], directory, f"{preset}-{target}")
        add_first_operation(directory)
        run([*make, "fix"], directory, f"{preset}-first-change")
        for target in ("check", "build", "acceptance", "release"):
            run([*make, target], directory, f"{preset}-{target}")
        check_layout(directory, collection=False)

    collection_journey(core, toolkit, template, destination, run)


def collection_journey(
    core: str, toolkit: str | None, template: Path, destination: Path, run: Callable[[list[str], Path, str], None]
) -> None:
    """Exercise collection presets and real private captures of stock and authored source.

    Raises:
        AssertionError: If scaffolding changes files outside its owning project directories.
    """
    import plugin_tooling  # noqa: PLC0415 - only the script entrypoint needs the sibling toolkit loader

    make = ["make", f"CORE={core}", *([f"TOOLING={toolkit}"] if toolkit else [])]
    collection = destination / "collection"
    collection.mkdir()
    marker = collection / "README.md"
    marker.write_text("Existing collection; no registration edits.\n")
    cli = [
        sys.executable,
        str(Path(__file__).with_name("plugin_tooling.py")),
        "--toolkit",
        toolkit or TOOLKIT,
        "hz-plugin",
    ]
    screenshot_env = destination / "screenshot-toolkit"
    run(["uv", "venv", "--python", "3.15", str(screenshot_env)], destination, "screenshot-toolkit-venv")
    if toolkit and toolkit != TOOLKIT:
        selection = [str(plugin_tooling.candidate_wheel(toolkit, screenshot_env))]
    else:
        selection = [
            "--require-hashes",
            "--requirements",
            str(plugin_tooling.local_requirements(plugin_tooling.REQUIREMENTS, screenshot_env)),
        ]
    screenshot_python = str(screenshot_env / "bin/python")
    run(
        ["uv", "pip", "install", "--no-build", "--python", screenshot_python, *selection],
        destination,
        "screenshot-toolkit-install",
    )
    screenshots = [
        screenshot_python,
        str(Path(__file__).resolve().parents[1] / "tests/fixtures/starter_screenshots.py"),
    ]
    for preset in ("minimal", "contributions"):
        name = f"collection-{preset}"
        run(
            [
                *cli,
                "new",
                name,
                "--core",
                core,
                "--template",
                str(template),
                "--ref",
                "HEAD",
                "--preset",
                preset,
                "--no-screenshots",
            ],
            collection,
            f"{name}-scaffold",
        )
        check_collection_message(destination / f"{name}-scaffold.log")
        owning = collection / "plugins" / f"heinzel-{name}"
        check_layout(owning, collection=True)
        stock = destination / f"{name}-stock-draft"
        run(
            [*screenshots, str(owning), str(stock), "--core", core, "--preset", preset],
            collection,
            f"{name}-stock-screenshots",
        )
        add_first_operation(owning)
        run([*make, "setup"], owning, f"{name}-setup")
        run([*make, "fix"], owning, f"{name}-first-change")
        for command in ("check-fast", "check", "build", "acceptance", "release"):
            run([*make, command], owning, f"{name}-{command}")
        run(
            [
                *screenshots,
                str(owning),
                str(destination / f"{name}-authored-draft"),
                "--core",
                core,
                "--preset",
                preset,
                "--previous",
                str(stock),
            ],
            collection,
            f"{name}-authored-screenshots",
        )
    if marker.read_text() != "Existing collection; no registration edits.\n" or set(collection.iterdir()) != {
        marker,
        collection / "plugins",
    }:
        msg = "Scaffolding changed files outside the owning plugin directories"
        raise AssertionError(msg)


def main() -> None:
    """Accept explicit candidate artifacts; normal CI consumes the versioned toolkit."""

    def abort(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, abort)
    signal.signal(signal.SIGTERM, abort)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True)
    parser.add_argument("--toolkit", default=os.environ.get("PLUGIN_TOOLKIT"))
    parser.add_argument("--template", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    core = str(Path(args.core).resolve()) if Path(args.core).is_file() else args.core
    if args.destination:
        args.destination.mkdir(parents=True, exist_ok=True)
        journey(core, args.toolkit, args.template, args.destination.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="hz-starter-test-") as temporary:
            journey(core, args.toolkit, args.template, Path(temporary))


if __name__ == "__main__":
    main()
