"""Check natural album repeat boundaries using the synthetic Soundscape media fixture."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from run_soundscape_album import SHARED, TESTS, Host, prepare


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def _environment(scratch: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "DEVELOPER_DIR": os.environ.get("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer"),
    }
    for key in ("HOME", "TMPDIR", "CLANG_MODULE_CACHE_PATH", "SWIFT_MODULECACHE_PATH"):
        folder = scratch / key.lower()
        folder.mkdir()
        env[key] = str(folder)
    return env


def _sources(scratch: Path) -> list[Path]:
    sources = [
        SHARED / "AlbumLibrary.swift",
        SHARED / "SoundscapePlayback.swift",
        TESTS / "SoundscapeAlbumTests.swift",
        TESTS / "SoundscapeRepeatFixture.swift",
    ]
    pins = {}
    for source in sources:
        data = source.read_bytes()
        pins[str(source)] = hashlib.sha256(data).hexdigest()
        (scratch / source.name).write_bytes(data)
    for source in (Path(__file__), TESTS / "run_soundscape_album.py"):
        pins[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    write_json(scratch / "source-pins.json", pins)
    return sources


def _compile(scratch: Path, env: dict[str, str], iphoneos_build_only: bool) -> Path:
    sources = _sources(scratch)
    shim = scratch / "GroupURL.swift"
    shim.write_text("import Foundation\nenum ActivityWidgetData { static func sharedCatalogURL() -> URL? { nil } }\n")
    sdk = "iphoneos" if iphoneos_build_only else "macosx"
    target = "arm64-apple-ios18.0" if iphoneos_build_only else platform.machine() + "-apple-macos15.0"
    sdk_path = subprocess.run(
        ["xcrun", "--sdk", sdk, "--show-sdk-path"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    binary = scratch / ("RepeatProof.o" if iphoneos_build_only else "repeat-fixture")
    command = [
        "xcrun",
        "--sdk",
        sdk,
        "swiftc",
        "-parse-as-library",
        "-swift-version",
        "5",
        "-sdk",
        sdk_path,
        "-target",
        target,
        "-framework",
        "AVFoundation",
        "-framework",
        "WidgetKit",
        *[str(scratch / source.name) for source in sources],
        str(shim),
        "-o",
        str(binary),
    ]
    if iphoneos_build_only:
        command.extend(["-emit-object", "-whole-module-optimization"])
    write_json(scratch / "compile-command.json", command)
    with (scratch / "compile.log").open("w") as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
    return binary


def _build_receipt(scratch: Path, binary: Path) -> None:
    write_json(
        scratch / "BUILD-ONLY.json",
        {
            "sdk": "iphoneos",
            "target": "arm64-apple-ios18.0",
            "object_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "runtime_verified": False,
            "signed": False,
        },
    )


@dataclass
class _ProbeContext:
    scratch: Path
    binary: Path
    env: dict[str, str]
    catalog_url: str


def _matrix(context: _ProbeContext, origin: str) -> list[dict[str, object]]:
    results = []
    for mode in ("Off", "Album", "Track"):
        for index in (0, 1):
            root = context.scratch / f"{origin}-{mode}-{index}"
            if origin == "offline":
                shutil.copytree(context.scratch / "verified", root)
            result = _probe(context, origin, root, mode, index)
            if result is not None:
                results.append(result)
    return results


def _offline_isolation(context: _ProbeContext) -> None:
    port = urlsplit(context.catalog_url).port
    if port is None:
        message = "catalog URL has no listener port"
        raise RuntimeError(message)
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            message = "loopback listener survived shutdown"
            raise RuntimeError(message)
    write_json(
        context.scratch / "offline-isolation.json",
        {
            "server_stopped": True,
            "network_policy": (context.scratch / "offline.sb").read_text(),
            "catalog": context.catalog_url,
        },
    )


def _execute(scratch: Path, binary: Path, env: dict[str, str]) -> None:
    (scratch / "offline.sb").write_text("(version 1)\n(allow default)\n(deny network*)\n")
    routes = prepare(scratch)
    # Album download/cancel probes need a hold; natural repeat needs both short clips immediately.
    for route in routes.values():
        route["second"] = False
    host = Host(routes, scratch)
    context = _ProbeContext(scratch, binary, env, host.url + "/candidate-catalog.json")
    try:
        results = _matrix(context, "remote")
        _probe(context, "seed", scratch / "verified")
    finally:
        host.stop()
    # Each offline probe runs in a fresh child with all networking denied after listener shutdown.
    _offline_isolation(context)
    results.extend(_matrix(context, "offline"))
    if len(results) != 12:
        message = "incomplete matrix"
        raise RuntimeError(message)
    write_json(
        scratch / "PASS.json",
        {
            "cases": results,
            "count": len(results),
            "scope": "injected native player, muted synthetic AVFoundation media",
        },
    )


def run(args: argparse.Namespace) -> None:
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    env = _environment(scratch)
    binary = _compile(scratch, env, args.iphoneos_build_only)
    if args.iphoneos_build_only:
        _build_receipt(scratch, binary)
        return
    _execute(scratch, binary, env)


def _probe(
    context: _ProbeContext,
    origin: str,
    root: Path,
    mode: str = "Off",
    index: int = 0,
) -> dict[str, object] | None:
    scratch = context.scratch
    name = f"{origin}-{mode}-{index}"
    evidence = scratch / (name + ".json")
    argv = [str(context.binary), origin, str(root), context.catalog_url, mode, str(index), str(evidence)]
    if origin == "offline":
        argv = ["/usr/bin/sandbox-exec", "-f", str(scratch / "offline.sb"), *argv]
    write_json(scratch / (name + "-command.json"), argv)
    with (scratch / (name + ".log")).open("w") as log:
        subprocess.run(argv, env=context.env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    return _evidence(evidence, origin, name)


def _evidence(evidence: Path, origin: str, name: str) -> dict[str, object] | None:
    events = json.loads(evidence.read_text())
    expected = "seed-verified" if origin == "seed" else "PASS"
    if events[-1]["event"] != expected:
        message = "missing terminal evidence"
        raise RuntimeError(message)
    if origin != "seed":
        names = [event["event"] for event in events]
        if not all(
            event in names
            for event in ("cycle-Album", "cycle-Track", "cycle-Off", "before-boundary", "natural-end-observed")
        ):
            message = "incomplete boundary evidence"
            raise RuntimeError(message)
        return {"case": name, "pid": events[0]["pid"], "evidence": evidence.name}

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    gate = parser.add_mutually_exclusive_group(required=True)
    gate.add_argument("--execute", action="store_true", help="coordinator-gated native execution")
    gate.add_argument("--iphoneos-build-only", action="store_true", help="coordinator-gated unsigned object build")
    parser.add_argument("--scratch", type=Path, required=True, help="new private directory")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
