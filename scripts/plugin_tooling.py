# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Run the released plugin toolkit from a disposable, hash-locked wheel environment, without installing core."""

import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl"
    "#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7"
)

REQUIREMENTS = Path(__file__).with_name("plugin-tooling-requirements.txt")


def candidate_wheel(spec: str, directory: Path) -> Path:
    """Resolve an explicit developer wheel and enforce a remote URL's declared digest.

    Raises:
        ValueError: If the candidate is not a local wheel or an HTTPS wheel with a SHA-256 digest.
    """
    value = spec.rsplit(" @ ", 1)[-1]
    url = urlsplit(value)
    if url.scheme:
        expected = url.fragment.removeprefix("sha256=")
        if (
            url.scheme != "https"
            or not url.fragment.startswith("sha256=")
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or not url.path.endswith(".whl")
        ):
            msg = "Use a local candidate wheel or an HTTPS wheel URL with #sha256=<digest>"
            raise ValueError(msg)
        with urllib.request.urlopen(urlunsplit(url._replace(fragment="")), timeout=60) as response:  # noqa: S310 - explicit developer HTTPS candidate, bytes verified before installation
            content = response.read(128 * 1024 * 1024 + 1)
        if len(content) > 128 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
            msg = "Candidate toolkit wheel failed its integrity check"
            raise ValueError(msg)
        wheel = directory / Path(url.path).name
        wheel.write_bytes(content)
        return wheel
    wheel = Path(value).resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        msg = "Select an existing local toolkit wheel"
        raise ValueError(msg)
    return wheel


def run(program: str, arguments: list[str], requirements: Path = REQUIREMENTS, toolkit: str = TOOLKIT) -> int:
    """Install verified wheels before running the selected local developer command.

    Raises:
        ValueError: If the requested executable is outside the toolkit interface.
        RuntimeError: If uv is unavailable.
    """
    if program not in {"hz-plugin", "python"}:
        msg = "Choose hz-plugin or python from the verified toolkit environment"
        raise ValueError(msg)
    uv = shutil.which("uv")
    if uv is None:
        msg = "Install uv before running the plugin toolkit"
        raise RuntimeError(msg)
    with tempfile.TemporaryDirectory(prefix="hz-plugin-toolkit-") as temporary:
        environment = Path(temporary)
        interpreter = environment / "bin/python"
        subprocess.run([uv, "venv", "--python", "3.14", str(environment)], check=True, timeout=120)  # noqa: S603 - resolved uv, disposable environment
        selection = ["--require-hashes", "--requirements", str(requirements)]
        if toolkit != TOOLKIT:
            selection = [str(candidate_wheel(toolkit, environment))]
        subprocess.run(  # noqa: S603 - verified default closure or an explicitly selected developer wheel
            [uv, "pip", "install", "--no-build", "--python", str(interpreter), *selection],
            check=True,
            timeout=300,
        )
        return subprocess.run(  # noqa: S603 - allowlisted executable in the verified environment, intentional local argv
            [str(environment / "bin" / program), *arguments],
            check=False,
            timeout=1800,
        ).returncode


def main() -> int:
    """Keep the executable selection within the installed toolkit environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolkit", default=TOOLKIT)
    parser.add_argument("program", choices=["hz-plugin", "python"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    return run(args.program, args.arguments, toolkit=args.toolkit)


if __name__ == "__main__":
    raise SystemExit(main())
