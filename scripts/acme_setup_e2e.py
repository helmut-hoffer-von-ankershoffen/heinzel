#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""ACME setup end to end on an ISOLATED profile: add `acme` from the catalog, assert GitHub and Atlassian come too.

Usage: scripts/acme_setup_e2e.py --hz /path/to/hz [--keep] [--with-bindings]

Needs an `hz` whose core carries plugin dependency installs and a catalog `acme` whose pyproject declares
`requires-plugins`. Everything happens under a throwaway HOME (state, config, uv tool store, logs); the live profile
and its daemon are never read or touched. It signs in nowhere: `gh auth login` and `acli jira auth login --web` are
the owner's steps, which the script prints at the end.

`--with-bindings` (ADR 0145) also proves "install ACME and everything is set up": fake `gh`, `acli` and `gws`
(tests/fixtures/connectors/fake_clis) go first on PATH; the add installs the recommended connectors too and writes
ACME's six bindings; the checklist then holds only the person's own sign-in rows; after the fake sign-ins and the
Jira site it is done. A second throwaway profile proves that a binding the person set before stays theirs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger("acme_setup_e2e")
EXPECTED = ("heinzel-github", "heinzel-atlassian", "heinzel-acme")
OWNER_STEPS = ("gh auth login", "acli jira auth login --web")
LIVE_MARKERS = ("Application Support/hz", ".local/share/hz")
FAKE_CLIS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "connectors" / "fake_clis"
EXPECTED_WITH_RECOMMENDED = (*EXPECTED, "heinzel-google", "heinzel-zoom")
ACME_BINDINGS = {
    "code-host": "github",
    "tracker": "atlassian",
    "documents": "google",
    "calendar": "google",
    "mail": "google",
    "meetings": "zoom",
}
FAKE_SIGN_INS = (("gh", "auth", "login"), ("acli", "jira", "auth", "login", "--web"), ("gws", "auth", "login"))
FAKE_SITE = ("system", "config", "set", "atlassian.site_url", "https://fake-acme.atlassian.net")


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


ARGUMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/=+-]*")
"""What an argument to hz may look like here: the script's own subcommands, flags and values, nothing a shell reads."""


def executable(path: str) -> str:
    """The `hz` executable to test: an existing, executable file named `hz`, as an absolute path.

    Raises:
        SetupError: when `path` is anything else.
    """
    try:
        found = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        message = "no hz at that path"
        raise SetupError(message, path) from exc
    if found.name != "hz" or not found.is_file() or not os.access(found, os.X_OK):
        message = "--hz must name an executable file called hz"
        raise SetupError(message, str(found))
    return str(found)


def run_hz(hz: str, env: dict[str, str], *args: str) -> dict | list:
    """Run `hz <args> --json` in the isolated environment, logging one start and one outcome event.

    Raises:
        SetupError: when hz exits non-zero, or an argument isn't one this script passes.
    """
    if Path(hz).name != "hz" or not Path(hz).is_absolute():
        message = "run_hz takes the checked hz executable"
        raise SetupError(message, hz)
    if bad := [arg for arg in args if not ARGUMENT.fullmatch(arg)]:
        message = "refusing an argument this script never passes"
        raise SetupError(message, repr(bad[0]))
    started = time.monotonic()
    LOG.info("acme_e2e.step.start", extra={"step": " ".join(args[:3])})
    command = [hz, *args, "--json"]
    done = subprocess.run(command, env=env, capture_output=True, text=True, check=False, timeout=1800)  # noqa: S603
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


def run_object(hz: str, env: dict[str, str], *args: str) -> dict[str, Any]:
    """`run_hz` for a command that answers one JSON object.

    Raises:
        SetupError: when it answers anything else.
    """
    answer = run_hz(hz, env, *args)
    if not isinstance(answer, dict):
        message = f"hz {' '.join(args)} did not answer an object"
        raise SetupError(message, str(answer)[:200])
    return answer


def binding_states(change: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """Capability -> (state, adapter) from a confirmed add's `bindings`."""
    return {b["capability"]: (b["state"], b.get("adapter")) for b in change.get("bindings", [])}


def wrong_bindings(change: dict[str, Any], expected: dict[str, str], kept: dict[str, str] | None = None) -> list[str]:
    """What differs from `expected` written bindings, `kept` ones naming the person's adapter in their reason."""
    kept = kept or {}
    states = binding_states(change)
    problems = [
        f"{capability}: {states.get(capability)}"
        for capability, adapter in expected.items()
        if capability not in kept and states.get(capability) != ("written", adapter)
    ]
    reasons = {b["capability"]: b.get("reason", "") for b in change.get("bindings", [])}
    problems += [
        f"{capability} not kept as {theirs}: {states.get(capability)} {reasons.get(capability)}"
        for capability, theirs in kept.items()
        if states.get(capability, ("",))[0] != "kept" or theirs not in reasons.get(capability, "")
    ]
    return problems


def not_owner_only(checklist: dict[str, Any]) -> list[str]:
    """Checklist rows still open that the person isn't the only one able to do: setup should have done them."""
    return [
        f"{row['connector']}: {row['state']} ({row['line']})"
        for row in checklist.get("rows", [])
        if row["state"] != "connected" and not row["owner_only"]
    ]


def open_rows(checklist: dict[str, Any]) -> list[str]:
    """The connectors of the checklist that aren't connected yet."""
    return [row["connector"] for row in checklist.get("rows", []) if row["state"] != "connected"]


def with_fakes(env: dict[str, str]) -> dict[str, str]:
    """`env` with the fake vendor CLIs first on PATH.

    Raises:
        SetupError: when the fixtures are missing.
    """
    if not all((FAKE_CLIS / name).is_file() for name in ("gh", "acli", "gws")):
        message = "fake vendor CLIs are missing"
        raise SetupError(message, str(FAKE_CLIS))
    return {**env, "PATH": f"{FAKE_CLIS}{os.pathsep}{env.get('PATH', '')}"}


def prove_bindings(hz: str, env: dict[str, str]) -> None:
    """Install ACME with its recommended connectors: bindings set, only sign-ins left, then green after them.

    Raises:
        SetupError: when a binding, a checklist row or the sign-in result is not what ADR 0145 promises.
    """
    change = run_object(hz, env, "system", "plugin", "add", "acme", "--confirm")
    absent = missing(run_hz(hz, env, "system", "plugin", "list"), EXPECTED_WITH_RECOMMENDED)
    if absent:
        message = "the recommended connectors did not come with acme"
        raise SetupError(message, str(absent))
    if problems := wrong_bindings(change, ACME_BINDINGS):
        message = "the add did not write ACME's bindings"
        raise SetupError(message, "; ".join(problems))
    checklist = run_object(hz, env, "system", "plugin", "checklist", "--module", "acme")
    if leftovers := not_owner_only(checklist):
        message = "setup left work that isn't the person's own"
        raise SetupError(message, "; ".join(leftovers))
    for command in FAKE_SIGN_INS:
        subprocess.run(list(command), env=env, check=True, capture_output=True, timeout=60)  # noqa: S603 - the fakes
    run_hz(hz, env, *FAKE_SITE)
    checklist = run_object(hz, env, "system", "plugin", "checklist", "--module", "acme")
    if not checklist.get("done"):
        message = "still open after the sign-ins"
        raise SetupError(message, str(open_rows(checklist)))


def prove_person_wins(hz: str, env: dict[str, str]) -> None:
    """A tracker binding the person set before ACME stays theirs.

    Raises:
        SetupError: when the add changed it.
    """
    run_hz(hz, env, "system", "plugin", "add", "github", "--confirm")
    run_hz(hz, env, "system", "config", "set", "capabilities.tracker", "github")
    change = run_object(hz, env, "system", "plugin", "add", "acme", "--confirm")
    if problems := wrong_bindings(change, {"tracker": "atlassian"}, kept={"tracker": "github"}):
        message = "the person's binding did not win"
        raise SetupError(message, "; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    """Run the proof on a throwaway profile and print the owner-only steps."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hz", required=True, help="path to the hz executable under test")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway profile for inspection")
    parser.add_argument(
        "--with-bindings", action="store_true", help="also prove bindings, the checklist and fake sign-ins (ADR 0145)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    homes = [Path(tempfile.mkdtemp(prefix="hz-acme-e2e-"))]
    try:
        hz = executable(args.hz)
        if args.with_bindings:
            prove_bindings(hz, with_fakes(isolated_env(homes[0])))
            homes.append(Path(tempfile.mkdtemp(prefix="hz-acme-e2e-")))
            prove_person_wins(hz, with_fakes(isolated_env(homes[1])))
        else:
            prove(hz, isolated_env(homes[0]))
    except SetupError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if not args.keep:
            for home in homes:
                shutil.rmtree(home, ignore_errors=True)
    if args.with_bindings:
        print("OK: ACME set up its six bindings; after the fake sign-ins the checklist is done; yours stayed yours.")
        return 0
    print("OK: heinzel-acme brought heinzel-github and heinzel-atlassian.")
    print("Owner-only next steps (the script never signs in): " + "; ".join(OWNER_STEPS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
