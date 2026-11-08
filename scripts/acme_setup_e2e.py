#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""ACME setup end to end on an ISOLATED profile: add `acme` from the catalog, assert GitHub and Atlassian come too.

Usage: scripts/acme_setup_e2e.py --hz /path/to/hz [--keep]

Needs an `hz` whose core carries plugin dependency installs and a catalog `acme` whose pyproject declares
`requires-plugins`. Everything happens under a throwaway HOME (state, config, uv tool store, logs); the live profile
and its daemon are never read or touched. It signs in nowhere: `gh auth login` and `acli jira auth login --web` are
the owner's steps, which the script prints at the end.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOG = logging.getLogger("acme_setup_e2e")
EXPECTED = ("heinzel-github", "heinzel-atlassian", "heinzel-acme")
OWNER_STEPS = ("gh auth login", "acli jira auth login --web")
LIVE_MARKERS = ("Application Support/hz", ".local/share/hz")


class SetupError(Exception):
    """The isolated ACME setup did not do what the plugin catalog promised."""

    def __init__(self, what: str, detail: str = "") -> None:
        """Say what failed, with the detail that names it."""
        super().__init__(f"{what}: {detail}" if detail else what)


def isolated_env(home: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """An environment whose hz and uv directories all live under `home`; a live profile is refused.

    Raises:
        SetupError: when `home` looks like a live profile.
    """
    if any(marker in str(home) for marker in LIVE_MARKERS):
        message = "refusing a live hz profile"
        raise SetupError(message, str(home))
    env = {k: v for k, v in (base if base is not None else os.environ).items() if not k.startswith(("HZ_", "UV_"))}
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        HZ_CONFIG_HOME=str(home / "config" / "hz"),
        HZ_DATA_DIR=str(home / "data" / "hz"),
        HZ_STATE_DIR=str(home / "state" / "hz"),
        HZ_CACHE_DIR=str(home / "cache" / "hz"),
        HZ_LOG_DIR=str(home / "logs" / "hz"),
        UV_TOOL_DIR=str(home / "uv-tools"),
        UV_TOOL_BIN_DIR=str(home / "uv-bin"),
    )
    return env


def installed(listing: dict | list) -> dict[str, str]:
    """Distribution -> version from `hz system plugin list --json`."""
    items = listing if isinstance(listing, list) else listing.get("plugins", listing.get("items", []))
    return {item["distribution"]: item["version"] for item in items}


def missing(listing: dict | list, expected: tuple[str, ...] = EXPECTED) -> list[str]:
    """The expected distributions the listing lacks."""
    have = installed(listing)
    return [name for name in expected if name not in have]


def required_in(preview: dict) -> list[str]:
    """The plugins a preview says it installs too."""
    return sorted(n["distribution"] for n in preview.get("needs", []) if n.get("required"))


def run_hz(hz: str, env: dict[str, str], *args: str) -> dict | list:
    """Run `hz <args> --json` in the isolated environment, logging one start and one outcome event.

    Raises:
        SetupError: when hz exits non-zero.
    """
    started = time.monotonic()
    LOG.info("acme_e2e.step.start", extra={"step": " ".join(args[:3])})
    done = subprocess.run([hz, *args, "--json"], env=env, capture_output=True, text=True, check=False)  # noqa: S603
    LOG.info(
        "acme_e2e.step.outcome",
        extra={
            "step": " ".join(args[:3]),
            "outcome": "ok" if done.returncode == 0 else "error",
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    )
    if done.returncode:
        message = f"hz {' '.join(args)} failed ({done.returncode})"
        raise SetupError(message, done.stderr.strip()[-400:])
    return json.loads(done.stdout)


def prove(hz: str, env: dict[str, str]) -> None:
    """Preview, confirm and list the plugins.

    Raises:
        SetupError: when GitHub and Atlassian do not come with ACME.
    """
    needs = required_in(run_hz(hz, env, "system", "plugin", "add", "acme"))
    if needs != ["heinzel-atlassian", "heinzel-github"]:
        message = "the preview should name github and atlassian as required"
        raise SetupError(message, str(needs))
    run_hz(hz, env, "system", "plugin", "add", "acme", "--confirm")
    absent = missing(run_hz(hz, env, "system", "plugin", "list"))
    if absent:
        message = "not installed together with acme"
        raise SetupError(message, str(absent))


def main(argv: list[str] | None = None) -> int:
    """Run the proof on a throwaway profile and print the owner-only steps."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hz", required=True, help="path to the hz executable under test")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway profile for inspection")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    home = Path(tempfile.mkdtemp(prefix="hz-acme-e2e-"))
    try:
        prove(args.hz, isolated_env(home))
    except SetupError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(home, ignore_errors=True)
    print("OK: heinzel-acme brought heinzel-github and heinzel-atlassian.")
    print("Owner-only next steps (the script never signs in): " + "; ".join(OWNER_STEPS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
