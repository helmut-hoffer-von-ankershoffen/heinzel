# /// script
# requires-python = ">=3.12"
# dependencies = ["nodejs-wheel-binaries==24.19.0"]
# ///
"""Run the web head's frontend tools (web/) and build its app into the package, with no prerequisite beyond uv.

Node.js comes from the `nodejs-wheel-binaries` wheel (the version uv.lock has through `pyright[nodejs]`; a test keeps
them equal) and pnpm is a pinned, checksummed binary from scripts/tools.toml, group `web` (ADR 0036, ADR 0039).
pnpm's store is shared by every clone and worktree, so installs work offline once it has the packages. hatch_build.py
loads this file to build the app into the wheel and the sdist; scripts/web.py (the typed client, the dev server)
builds on it.

Usage: uv run --script scripts/web_build.py COMMAND
    pnpm [ARG ...]       run pnpm in web/
    exec TOOL [ARG ...]  run one of web/'s tools (biome, tsc, vitest, ...), installing the dependencies first if stale
    install              install web/'s dependencies as locked, offline when the store has them
    build                build the app into src/heinzel/heads/web/static, stamped with the digest of its sources
    licenses             check the licenses of what the app bundles against .license-allowlist (it ships in the wheel)
    licenses --json      print them as `pnpm licenses list --prod --json` does (scripts/attributions.py reads it)
"""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
STATIC = ROOT / "src" / "heinzel" / "heads" / "web" / "static"
STAMP = ".source-sha256"
SOURCES = ("package.json", "pnpm-lock.yaml", "index.html", "vite.config.ts", "tsconfig.json", "src", "public")
LOCKED = ("package.json", "pnpm-lock.yaml")
"""What web/node_modules is installed from; `installed()` compares their digest with the one the last install left."""
INSTALLED = ".hz-installed"
"""What the built app is made from; a change to any of them makes the build stale."""
NODE_REQUIREMENT = "nodejs-wheel-binaries==24.19.0"
"""The Node.js this script and the build hook use: the version uv.lock has (a test keeps them equal)."""
TOOLS = ROOT / "scripts" / "tools.py"
TIMEOUT = 900  # an install from an empty store, or a cold build, on a slow machine


class WebError(Exception):
    """The frontend toolchain can't run; the message says why and what to do."""


def node_bin() -> Path:
    """The directory holding node from the nodejs-wheel-binaries wheel.

    Raises:
        WebError: the wheel isn't installed in this environment.
    """
    try:
        import nodejs_wheel  # noqa: PLC0415 - in this script's environment, the dev group and the build hook's
    except ImportError as error:
        msg = f"Node.js isn't installed ({NODE_REQUIREMENT}); run `make install`"
        raise WebError(msg) from error
    return Path(nodejs_wheel.__file__).parent / "bin"


def environment() -> dict[str, str]:
    """The environment pnpm runs in: this Node.js first on PATH, so pnpm and the tools it starts use it."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(node_bin()), env.get("PATH", "")])
    env["npm_config_update_notifier"] = "false"
    return env


def pnpm_command(*arguments: str) -> list[str]:
    """The command line that runs the pinned pnpm, installing it first if needed (`tools.py run`)."""
    return [sys.executable, str(TOOLS), "run", "pnpm", *arguments]


def pnpm(*arguments: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run pnpm in web/.

    Raises:
        WebError: pnpm failed and `check` is set.
    """
    done = subprocess.run(  # noqa: S603 - our own interpreter and script; arguments are ours
        pnpm_command(*arguments),
        cwd=WEB,
        env=environment(),
        check=False,
        text=True,
        capture_output=capture,
        timeout=TIMEOUT,
    )
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[-2000:] if capture else ""
        msg = f"pnpm {' '.join(arguments)} failed with exit code {done.returncode}" + (f":\n{detail}" if detail else "")
        raise WebError(msg)
    return done


def locked_digest(web: Path = WEB) -> str:
    """One sha256 over what web/node_modules is installed from."""
    digest = hashlib.sha256()
    for name in LOCKED:
        path = web / name
        digest.update(name.encode() + b"\0" + (path.read_bytes() if path.is_file() else b"") + b"\0")
    return digest.hexdigest()


def install(web: Path = WEB) -> None:
    """Install web/'s dependencies exactly as locked, from the store without network when it has them; stamp it."""
    pnpm("install", "--frozen-lockfile", "--prefer-offline", "--reporter=silent")
    (web / "node_modules" / INSTALLED).write_text(locked_digest(web) + "\n", encoding="utf-8")


def installed(web: Path = WEB) -> bool:
    """Whether web/node_modules holds what package.json and the lockfile say, by content, not by time stamps."""
    stamp = web / "node_modules" / INSTALLED
    return stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == locked_digest(web)


def ensure_installed() -> None:
    """Install web/'s dependencies unless they are current; one process at a time, as make runs steps in parallel."""
    if installed():
        return
    lock = ROOT / ".tools" / "web-install.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            if not installed():
                install()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def source_digest(web: Path = WEB) -> str:
    """One sha256 over the app's sources, in a stable order."""
    digest = hashlib.sha256()
    for name in SOURCES:
        path = web / name
        # Dotfiles (.DS_Store) are left out: the sdist drops what git ignores, and its wheel must find the same digest.
        found = (
            sorted(p for p in path.rglob("*") if p.is_file() and not p.name.startswith(".")) if path.is_dir() else []
        )
        files = found or ([path] if path.is_file() else [])
        for file in files:
            digest.update(file.relative_to(web).as_posix().encode() + b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()


def current(static: Path = STATIC, web: Path = WEB) -> bool:
    """Whether the built app is there, built from the sources as they are now (or there are no sources to build)."""
    if not (static / "index.html").is_file():
        return False
    if not (web / "package.json").is_file():
        return True
    stamp = static / STAMP
    return stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == source_digest(web)


def build(static: Path = STATIC) -> Path:
    """Build the app into `static` and stamp it with the digest of its sources."""
    ensure_installed()
    pnpm("exec", "vite", "build", "--outDir", str(static), "--emptyOutDir", "--logLevel", "warn")
    (static / STAMP).write_text(source_digest() + "\n", encoding="utf-8")
    return static


def ensure_built(static: Path = STATIC, web: Path = WEB) -> Path:
    """Build the app once when stale, serializing concurrent test and setup processes.

    Vite uses ``--emptyOutDir``; concurrent callers can otherwise observe an empty static directory while another
    process rebuilds it. Recheck freshness while holding the lock so only the first stale caller rebuilds.
    """
    lock = ROOT / ".tools" / "web-build.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            return build(static) if not current(static, web) else static
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def allowed_licenses(allowlist: Path = ROOT / ".license-allowlist") -> list[str]:
    """The licenses .license-allowlist allows, one per line, comments left out."""
    lines = (line.strip() for line in allowlist.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def refused_licenses(listing: dict[str, list[dict[str, object]]], allowed: list[str]) -> list[str]:
    """`pnpm licenses list --json`'s packages whose license names none of `allowed` (matched like pip-licenses)."""
    return sorted(
        f"{package['name']} ({license_})"
        for license_, packages in listing.items()
        for package in packages
        if not any(name in license_ for name in allowed)
    )


def licenses(*, as_json: bool = False) -> int:
    """Check every runtime dependency of the app against .license-allowlist (they ship in the wheel), or list them."""
    ensure_installed()
    output = pnpm("licenses", "list", "--prod", "--json", capture=True).stdout
    if as_json:
        print(output.strip())
        return 0
    listing = json.loads(output)
    refused = refused_licenses(listing, allowed_licenses())
    if refused:
        print("Web dependencies under licenses not on .license-allowlist:", *refused, sep="\n  ", file=sys.stderr)
        return 1
    print("Web dependency licenses: all allowed.")
    return 0


def _exec(arguments: list[str]) -> int:
    ensure_installed()
    return pnpm("exec", *arguments, check=False).returncode


def _install() -> int:
    install()
    print("✓ web/node_modules: the frontend's dependencies, as locked in web/pnpm-lock.yaml")
    return 0


def _build() -> int:
    print(f"✓ web app built into {build().relative_to(ROOT)}")
    return 0


def _ensure() -> int:
    print(f"✓ web app ready in {ensure_built().relative_to(ROOT)}")
    return 0


def main(argv: list[str], extra: dict[str, Callable[[list[str]], int]] | None = None) -> int:
    """Run one command; see the module docstring. `extra` adds commands (scripts/web.py's)."""
    command, *arguments = argv or ["--help"]
    commands: dict[str, Callable[[list[str]], int]] = {
        "pnpm": lambda args: pnpm(*args, check=False).returncode,
        "exec": _exec,
        "install": lambda _: _install(),
        "build": lambda _: _build(),
        "ensure": lambda _: _ensure(),
        "licenses": lambda args: licenses(as_json="--json" in args),
        **(extra or {}),
    }
    if command not in commands:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return commands[command](arguments)
    except WebError as error:
        print(f"✗ {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
