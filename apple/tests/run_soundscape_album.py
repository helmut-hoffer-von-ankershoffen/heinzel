"""Synthetic macOS local-first proof. All generated files stay in --scratch."""

import argparse
import functools
import hashlib
import http.server
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SHARED = TESTS.parent / "Shared"


def prepare(run):
    routes = {}

    def asset(path, mime, second=False):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        key = "/media/sha256/" + digest + path.suffix
        routes[key] = {"path": str(path), "second": second}
        return {"key": key, "bytes": len(data), "sha256": digest, "mimeType": mime}

    tracks = []
    for index, color in enumerate(("navy", "teal"), 1):
        media = run / f"track-{index}.mp4"
        poster = run / f"poster-{index}.png"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=160x90:r=24:d=1.5",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={330 * index}:sample_rate=44100:duration=1.5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-shortest",
                str(media),
            ],
            check=True,
            timeout=60,
        )
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(media), "-frames:v", "1", str(poster)],
            check=True,
            timeout=60,
        )
        tracks.append(
            {
                "id": f"example-track-{index}",
                "position": index,
                "title": f"Example Track {index}",
                "media": asset(media, "video/mp4", index == 2),
                "poster": asset(poster, "image/png"),
            }
        )
    catalog = {
        "schemaVersion": 1,
        "albums": [{"id": "dreaming-of-electric-snow", "cover": tracks[0]["poster"], "tracks": tracks}],
    }
    (run / "candidate-catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    return routes


class AlbumHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, routes, run, **kwargs):
        self.routes = routes
        self.run = run
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        with (self.run / "requests.log").open("a") as out:
            out.write(f"{time.time():.6f} {fmt % args}\n")

    def payload(self):
        if self.path in ("/candidate-catalog.json", "/slow-catalog.json"):
            if self.path == "/slow-catalog.json":
                time.sleep(3)
            return (self.run / "candidate-catalog.json").read_bytes(), "application/json"
        route = self.routes[self.path]
        if route["second"]:
            time.sleep(3)
        path = Path(route["path"])
        return path.read_bytes(), ("video/mp4" if path.suffix == ".mp4" else "image/png")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            data, mime = self.payload()
        except KeyError:
            self.send_error(404)
            return
        size = len(data)
        requested = self.headers.get("Range")
        if requested:
            start, _, end = requested.removeprefix("bytes=").partition("-")
            lo = int(start) if start else max(0, size - int(end))
            hi = min(size - 1, int(end) if start and end else size - 1)
            if not 0 <= lo <= hi < size:
                self.send_error(416)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {lo}-{hi}/{size}")
            data = data[lo : hi + 1]
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Host:
    def __init__(self, routes, run):
        handler = functools.partial(AlbumHandler, routes=routes, run=run)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def execute(run, routes):
    env = os.environ.copy()
    for name in ("HOME", "TMPDIR", "CLANG_MODULE_CACHE_PATH", "SWIFT_MODULECACHE_PATH"):
        path = run / name.lower()
        path.mkdir()
        env[name] = str(path)
    (run / "defaults-suite.txt").write_text("example.soundscape.fixture." + uuid.uuid4().hex)
    sources = [
        SHARED / "AlbumLibrary.swift",
        SHARED / "SoundscapePlayback.swift",
        TESTS / "SoundscapeAlbumTests.swift",
        TESTS / "SoundscapeAlbumFixture.swift",
    ]
    pins = {}
    for source in sources:
        data = source.read_bytes()
        pins[str(source.relative_to(TESTS.parent))] = hashlib.sha256(data).hexdigest()
        (run / source.name).write_bytes(data)
    pins[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (run / "source-pins.json").write_text(json.dumps(pins, indent=2) + "\n")
    # Only shared-container lookup is shimmed; the injected product sources are unchanged.
    shim = run / "GroupURL.swift"
    shim.write_text("import Foundation\nenum ActivityWidgetData { static func sharedCatalogURL() -> URL? { nil } }\n")
    binary = run / "fixture"
    command = [
        "xcrun",
        "swiftc",
        "-parse-as-library",
        "-swift-version",
        "5",
        "-target",
        platform.machine() + "-apple-macos15.0",
        "-framework",
        "AVFoundation",
        "-framework",
        "WidgetKit",
        *[str(run / source.name) for source in sources],
        str(shim),
        "-o",
        str(binary),
    ]
    (run / "compile-command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (run / "compile.log").open("w") as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    host = Host(routes, run)
    catalog = host.url + "/candidate-catalog.json"

    def probe(mode, root, url=None):
        with (run / (mode + "-" + root.name + ".log")).open("w") as log:
            subprocess.run(
                [str(binary), mode, str(root), url or catalog],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=True,
            )

    try:
        probe("download", run / "complete")
        host.stop()
        host = None
        # Fresh process, ephemeral session, and the old loopback listener is gone.
        for mode in ("offline", "selection-write", "selection-read"):
            probe(mode, run / "complete")
        host = Host(routes, run)
        catalog = host.url + "/candidate-catalog.json"
        for mode in ("copied-controls", "copied-cancel"):
            target = run / mode
            shutil.copytree(run / "complete", target)
            probe(mode, target)
        for mode in ("controls", "cancel", "rapid-resume"):
            probe(mode, run / mode)
        probe("cancel-read", run / "cancel")
        for mode in ("pre-pause", "pre-cancel"):
            target = run / mode
            probe(mode, target, host.url + "/slow-catalog.json")
            probe("pre-read", target)
        (run / "PASS").write_text("All CLI assertions passed at source-pins.json.\n")
    finally:
        if host is not None:
            host.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True, help="new isolated directory; must not already exist")
    args = parser.parse_args()
    run = args.scratch.expanduser().resolve()
    run.mkdir(parents=True, exist_ok=False)
    routes = prepare(run)
    execute(run, routes)


if __name__ == "__main__":
    main()
