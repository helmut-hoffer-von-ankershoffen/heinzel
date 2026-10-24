"""The web head's commands that need Heinzelmännchen itself: the typed client, and the dev server with hot reload.

Usage: uv run python scripts/web.py COMMAND
    client [--check]    regenerate web/openapi.json and web/src/api/schema.d.ts from the web head's API, or fail on
                        drift (`make fix`, `make check`), as the generated docs do
    dev                 the web head's API on a free port and Vite with hot reload in front of it (`make web-dev`)

Every other frontend command is scripts/web_build.py's, which needs no project environment.
"""

# Project-bound: runs in the project environment (`uv run python`), because it builds the web head's API from the
# kernel to read its OpenAPI document, and serves it for the dev server.

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import ModuleType

from heinzel import Config, Kernel, Settings
from heinzel.heads import web
from heinzel.kernel import boot, discover

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
OPENAPI = WEB / "openapi.json"
SCHEMA = WEB / "src" / "api" / "schema.d.ts"
VITE_PORT = 5173


def _web_build() -> ModuleType:
    spec = importlib.util.spec_from_file_location("web_build", ROOT / "scripts" / "web_build.py")
    if spec is None or spec.loader is None:  # pragma: no cover - the file is next to this one
        raise ImportError(name="web_build")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _web_build()


def openapi() -> str:
    """The OpenAPI document of the web head's API for the built-in modules; versionless, so releases don't drift."""
    config = Config.empty()
    kernel = Kernel(config.settings(Settings, "hz"), discover(plugins=False)[0], config=config)
    document = web.api_app(kernel, port=None, token="unused").openapi()  # noqa: S106 - never served
    document["info"].pop("version", None)
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def client(*, check: bool) -> int:
    """Regenerate web/openapi.json and the typed client's schema from it, or with `check` report drift."""
    document = openapi()
    build.ensure_installed()
    with tempfile.TemporaryDirectory() as directory:
        spec, schema = Path(directory) / "openapi.json", Path(directory) / "schema.d.ts"
        spec.write_text(document, encoding="utf-8")
        build.pnpm("exec", "openapi-typescript", str(spec), "--output", str(schema), "--root-types", capture=True)
        generated = {OPENAPI: document, SCHEMA: schema.read_text(encoding="utf-8")}
    stale = [path for path, text in generated.items() if not path.is_file() or path.read_text("utf-8") != text]
    if check:
        if stale:
            names = ", ".join(str(path.relative_to(ROOT)) for path in stale)
            print(f"The web client is out of date ({names}); run `make fix`.", file=sys.stderr)
            return 1
        return 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated[path], encoding="utf-8")
    return 0


def dev() -> int:
    """The web head's API on a free port, and Vite with hot reload in front of it, proxying /api to it."""
    build.ensure_installed()
    sock = web.free_socket()
    port = int(sock.getsockname()[1])
    sock.close()
    kernel = boot()
    started = threading.Event()

    def announce(url: str) -> None:
        print(f"Open {url} (Vite with hot reload; /api goes to the web head on port {port})", flush=True)
        started.set()

    def serve() -> None:
        base = f"http://localhost:{VITE_PORT}"
        web.serve(kernel, port=port, browser=False, announce=announce, static=None, base=base, restartable=False)

    threading.Thread(target=serve, daemon=True).start()
    if not started.wait(30):
        print("✗ the web head's API didn't start within 30 s", file=sys.stderr)
        return 1
    env = build.environment() | {"HZ_API_PORT": str(port)}
    vite = build.pnpm_command("exec", "vite", "--port", str(VITE_PORT), "--strictPort")
    # Until Ctrl-C, so no timeout.
    return subprocess.run(vite, cwd=WEB, env=env, check=False).returncode  # noqa: S603 - interactive: no timeout


if __name__ == "__main__":
    commands = {"client": lambda args: client(check="--check" in args), "dev": lambda _: dev()}
    sys.exit(build.main(sys.argv[1:], commands))
