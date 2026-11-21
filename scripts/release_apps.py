# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Promote the macOS apps the release candidate's CI built, instead of building them again after the tag.

Publish runs `python -I scripts/release_apps.py promote TAG DIRECTORY` at the tag's checkout. It looks for a
successful CI run of the release branch `release/TAG` in this repository, downloads that run's `notifier` and
`release-desktop-macos-arm64` artifacts and promotes them only when every check holds:

- both desktop manifests name the tag's version, darwin and arm64, and one archive whose sha256 and size match;
- both were built from one commit whose tree is the tag's tree (the tested bytes are the shipped bytes);
- the notifier archive holds the app's executable and no unsafe path.

The manifests then name the tag's commit, the one the release binds (`make desktop-bind-release-assets`), and the
script prints `promoted=true` with the run and the built commit for `$GITHUB_OUTPUT`. Anything else prints
`promoted=false` and the reason, and exits 0: Publish then builds both apps on macOS as before, so a missing or
different candidate can slow a release down but never change what it ships.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA = re.compile(r"[0-9a-f]{40}")
ARCHIVE = re.compile(r"heinzel-desktop(-swift)?-[0-9.]+-darwin-arm64\.tar\.gz")
DESKTOP = "release-desktop-macos-arm64"
NOTIFIER = "notifier"
MANIFESTS = ("desktop-artifact-manifest.json", "desktop-swift-artifact-manifest.json")
MANIFEST_KEYS = {"schema", "version", "commit", "platform", "arch", "archive"}
NOTIFIER_EXECUTABLE = "HeinzelNotifier.app/Contents/MacOS/notifier"
MAX_RUNS = 20
TIMEOUT = 300
USAGE = "Usage: python -I scripts/release_apps.py promote TAG DIRECTORY"

Get = Callable[[str], dict[str, Any]]
Download = Callable[[int, str, Path], object]


class NotPromotedError(Exception):
    """The candidate's apps cannot be promoted; the message is plain text without secrets."""


def require(condition: object, message: str) -> None:
    """Refuse the promotion with `message` unless `condition` holds.

    Raises:
        NotPromotedError: The condition does not hold.
    """
    if not condition:
        raise NotPromotedError(message)


def run(*args: str) -> str:
    """A fixed git or gh command, no shell; its output."""
    return subprocess.run(list(args), capture_output=True, text=True, check=True, timeout=TIMEOUT).stdout  # noqa: S603


def gh_json(path: str) -> dict[str, Any]:
    """One GitHub API read of this repository with the job's read-only token."""
    return json.loads(run("gh", "api", f"repos/{REPOSITORY}/{path}"))


def gh_download(run_id: int, name: str, dest: Path) -> object:
    """Download one named artifact of one run into `dest`."""
    return run("gh", "run", "download", str(run_id), "-R", REPOSITORY, "-n", name, "-D", str(dest))


def file_sha256(path: Path) -> str:
    """The sha256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_runs(tag: str, get: Get) -> list[dict[str, Any]]:
    """Successful CI runs of exactly this release branch in this repository, newest first."""
    branch = f"release/{tag}"
    listed = get(f"actions/workflows/ci.yml/runs?branch={branch}&status=success&per_page={MAX_RUNS}")
    runs = [
        r
        for r in listed.get("workflow_runs", [])
        if r.get("head_branch") == branch
        and r.get("conclusion") == "success"
        and r.get("event") in {"pull_request", "workflow_dispatch"}
        and (r.get("head_repository") or {}).get("full_name") == REPOSITORY
        and r.get("path") == ".github/workflows/ci.yml"
        and type(r.get("id")) is int
    ]
    return sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)


def check_desktop(directory: Path, version: str) -> tuple[str, dict[str, dict[str, Any]]]:
    """Both manifests: the release's version and platform, one built commit, archives equal to their records.

    Returns:
        The built commit and the manifests by file name.
    """
    commits = set()
    manifests = {}
    for name in MANIFESTS:
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"{name} is missing")
        manifest = json.loads(path.read_text())
        archive = manifest.get("archive") or {}
        require(
            set(manifest) == MANIFEST_KEYS
            and manifest["schema"] == 1
            and manifest["version"] == version
            and manifest["platform"] == "darwin"
            and manifest["arch"] == "arm64",
            f"{name} does not describe darwin arm64 {version}",
        )
        file = directory / str(archive.get("name", ""))
        require(ARCHIVE.fullmatch(file.name), f"{name} names an unexpected archive")
        require(file.is_file() and not file.is_symlink(), f"{file.name} is missing")
        require(file.stat().st_size == archive.get("size"), f"{file.name} differs in size from its manifest")
        require(file_sha256(file) == archive.get("sha256"), f"{file.name} differs in sha256 from its manifest")
        require(SHA.fullmatch(str(manifest["commit"])), f"{name} names no commit")
        commits.add(manifest["commit"])
        manifests[name] = manifest
    require(len(commits) == 1, "The two desktop manifests name different commits")
    return commits.pop(), manifests


def check_notifier(directory: Path) -> None:
    """The notifier archive holds the app's executable and only relative paths."""
    archive = directory / "notifier.tar.gz"
    require(archive.is_file() and not archive.is_symlink(), "notifier.tar.gz is missing")
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    require(NOTIFIER_EXECUTABLE in names, "The notifier archive lacks the app's executable")
    require(all(not n.startswith("/") and ".." not in n.split("/") for n in names), "Unsafe notifier archive paths")


def _verified(tag: str, run_id: int, folder: Path, tag_tree: str, seams: tuple[Get, Download]) -> tuple[str, dict]:
    get, download = seams
    download(run_id, DESKTOP, folder / "desktop")
    download(run_id, NOTIFIER, folder / "notifier")
    built, manifests = check_desktop(folder / "desktop", tag[1:])
    check_notifier(folder / "notifier")
    tree = get(f"git/commits/{built}").get("tree", {}).get("sha")
    require(tree == tag_tree, f"run {run_id} built {built[:12]}, whose tree is not the tag's")
    return built, manifests


def promote(  # noqa: PLR0913 - the seams tests replace go by keyword
    tag: str,
    output: Path,
    tag_commit: str,
    tag_tree: str,
    *,
    get: Get = gh_json,
    download: Download = gh_download,
) -> dict[str, Any]:
    """Promote the newest provable candidate run's apps into `output` (`desktop/`, `notifier/`).

    Returns:
        The run, the built commit and the tree.

    Raises:
        NotPromotedError: No run's apps could be proven; the reasons of every tried run.
    """
    require(TAG.fullmatch(tag), "Not a final release tag")
    require(SHA.fullmatch(tag_commit) and SHA.fullmatch(tag_tree), "The tag's commit and tree are required")
    runs = candidate_runs(tag, get)
    require(runs, f"No successful CI run of release/{tag}")
    reasons = []
    for candidate in runs:
        folder = output / f"run-{candidate['id']}"
        try:
            built, manifests = _verified(tag, candidate["id"], folder, tag_tree, (get, download))
        except (NotPromotedError, subprocess.SubprocessError, OSError, ValueError, tarfile.TarError) as error:
            reasons.append(f"run {candidate['id']}: {str(error)[:200]}")
            shutil.rmtree(folder, ignore_errors=True)
            continue
        for name, manifest in manifests.items():
            (folder / "desktop" / name).write_text(json.dumps({**manifest, "commit": tag_commit}, indent=2) + "\n")
        shutil.move(folder / "desktop", output / "desktop")
        shutil.move(folder / "notifier", output / "notifier")
        shutil.rmtree(folder, ignore_errors=True)
        return {"run": candidate["id"], "built_commit": built, "tree": tag_tree}
    raise NotPromotedError("; ".join(reasons)[:900])


def main(argv: list[str]) -> int:
    """`promote TAG DIRECTORY`: print `$GITHUB_OUTPUT` lines; a refusal is an answer, never a failure."""
    command, *rest = argv or [""]
    if command != "promote" or len(rest) != len(("TAG", "DIRECTORY")):
        sys.exit(USAGE)
    tag, output = rest[0], Path(rest[1])
    try:
        output.mkdir(parents=True, exist_ok=False)
        tag_commit = run("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").strip()
        tag_tree = run("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{tree}}").strip()
        require(os.environ.get("GITHUB_SHA") == tag_commit, "The checkout is not the tag's commit")
        facts = promote(tag, output, tag_commit, tag_tree)
    except (NotPromotedError, subprocess.SubprocessError, OSError, ValueError) as error:
        print("promoted=false")
        print("reason=" + " ".join(str(error).split())[:900])
        return 0
    print("promoted=true")
    print(f"run={facts['run']}")
    print(f"built_commit={facts['built_commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
