# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Exercise fresh standalone and collection authors against installed core and toolkit artifacts."""

import argparse
import json
import os
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl"
    "#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7"
)


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


def journey(core: str, toolkit: str, template: Path, destination: Path) -> None:
    """The documented commands must work without copying configuration or importing core sources.

    Raises:
        AssertionError: If scaffolding changes files outside its owning project directories.
    """
    environment = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    environment.pop("VIRTUAL_ENV", None)
    environment["HZ_PLUGINS_ENABLED"] = "false"
    for name in ("CONFIG_HOME", "DATA_DIR", "CACHE_DIR", "STATE_DIR", "LOG_DIR"):
        environment[f"HZ_{name}"] = str(destination / "state" / name.lower())
    timings: list[dict[str, str | float]] = []

    def run(command: list[str], cwd: Path, label: str) -> None:
        started = time.monotonic()
        log = destination / f"{label}.log"
        with log.open("w") as stream:
            result = subprocess.run(  # noqa: S603 - explicit tools and artifact paths, no shell
                command, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=600, check=False
            )
        timings.append({"step": label, "seconds": round(time.monotonic() - started, 2)})
        (destination / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")
        if result.returncode:
            print(log.read_text())
            result.check_returncode()
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
            run(["make", target, f"CORE={core}", f"TOOLING={toolkit}"], directory, f"{preset}-{target}")
        add_first_operation(directory)
        run(["make", "fix", f"CORE={core}", f"TOOLING={toolkit}"], directory, f"{preset}-first-change")
        for target in ("check", "build", "acceptance", "release"):
            run(["make", target, f"CORE={core}", f"TOOLING={toolkit}"], directory, f"{preset}-{target}")
        check_layout(directory, collection=False)

    collection = destination / "collection"
    collection.mkdir()
    marker = collection / "README.md"
    marker.write_text("Existing collection; no registration edits.\n")
    cli = ["uv", "run", "--no-project", "--with", toolkit, "hz-plugin"]
    for preset in ("minimal", "contributions"):
        name = f"collection-{preset}"
        run(
            [*cli, "new", name, "--core", core, "--template", str(template), "--ref", "HEAD", "--preset", preset],
            collection,
            f"{name}-scaffold",
        )
        check_collection_message(destination / f"{name}-scaffold.log")
        owning = collection / "plugins" / f"heinzel-{name}"
        check_layout(owning, collection=True)
        add_first_operation(owning)
        make = ["make", f"CORE={core}", f"TOOLING={toolkit}"]
        run([*make, "setup"], owning, f"{name}-setup")
        run([*make, "fix"], owning, f"{name}-first-change")
        for command in ("check-fast", "check", "build", "acceptance", "release"):
            run([*make, command], owning, f"{name}-{command}")
    if marker.read_text() != "Existing collection; no registration edits.\n" or set(collection.iterdir()) != {
        marker,
        collection / "plugins",
    }:
        msg = "Scaffolding changed files outside the owning plugin directories"
        raise AssertionError(msg)


def main() -> None:
    """Accept explicit candidate artifacts; normal CI consumes the versioned toolkit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True)
    parser.add_argument("--toolkit", default=os.environ.get("PLUGIN_TOOLKIT") or TOOLKIT)
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
