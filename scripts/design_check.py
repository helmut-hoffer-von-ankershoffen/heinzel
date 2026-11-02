# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
"""Computed-style check: the built Pages site and the running app against design/tokens.json.

The site is served from `site/` under its Pages prefix (build it first: `make docs-site`). The app is `hz ui` started
in throw-away directories, driven by web/e2e/design-tokens.mjs. Light and dark, phone (375x812) and desktop (1280x900).

Usage: uv run --script scripts/design_check.py [--site] [--app]   (both without a flag)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TOKENS = json.loads((ROOT / "design" / "tokens.json").read_text(encoding="utf-8"))
VIEWPORTS = {"phone": (375, 812), "desktop": (1280, 900)}
STARTUP_SECONDS = 90
UV = shutil.which("uv") or "uv"


class CheckError(Exception):
    """A computed style did not equal its token."""


def token(group: str, name: str) -> object:
    """The value of a named token."""
    return next(t["value"] for t in TOKENS[group]["tokens"] if t["name"] == name)


def pixels(value: str) -> str:
    """A token length in px at the browser's 16px root; Material's 20px root would stretch rem on the site."""
    if value.endswith("rem"):
        return f"{float(value.removesuffix('rem')) * 16:g}px"
    return value


class Handler(SimpleHTTPRequestHandler):
    """Serve the built site under the production project's path prefix."""

    def do_GET(self) -> None:
        """Map the deployed prefix to the build root."""
        if not self.path.startswith("/heinzel/"):
            self.send_error(404)
            return
        self.path = self.path.removeprefix("/heinzel")
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - standard library callback
        """Stay quiet; failures are reported by the assertions."""


PROBE = """([colours, lengths]) => {
  const probe = document.createElement('div');
  probe.style.position = 'absolute';
  document.body.append(probe);
  const out = {};
  for (const [key, value] of Object.entries(colours)) {
    probe.style.color = value;
    out[key] = getComputedStyle(probe).color;
  }
  for (const [key, value] of Object.entries(lengths)) {
    probe.style.width = value;
    out[key] = getComputedStyle(probe).width;
  }
  probe.remove();
  return out;
}"""

READ = """() => {
  const button = document.querySelector('.hz-button');
  const card = document.querySelector('.hz-feature');
  const body = getComputedStyle(document.body);
  const b = getComputedStyle(button);
  return {
    scheme: document.body.dataset.mdColorScheme,
    rootPrimary: getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim(),
    bodyPrimary: body.getPropertyValue('--color-primary').trim(),
    bodyBackground: body.backgroundColor,
    bodyColor: body.color,
    buttonBackground: b.backgroundColor,
    buttonColor: b.color,
    buttonRadius: b.borderTopLeftRadius,
    buttonHeight: button.getBoundingClientRect().height,
    cardRadius: getComputedStyle(card).borderTopLeftRadius,
  };
}"""


def expect(label: str, got: object, want: object) -> None:
    """Fail naming the check, what was seen and what the tokens say.

    Raises:
        CheckError: If the two differ.
    """
    if got != want:
        message = f"{label}: computed {got!r}, tokens say {want!r}"
        raise CheckError(message)


def check_site(playwright_api: object) -> list[dict[str, object]]:
    """Home page of the built site, both schemes and sizes.

    Raises:
        CheckError: If a computed style differs from its token.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "site")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    seen: list[dict[str, object]] = []
    try:
        browser = playwright_api.chromium.launch()  # type: ignore[attr-defined]
        try:
            for mode in ("light", "dark"):
                slot = 0 if mode == "light" else 1
                for size, (width, height) in VIEWPORTS.items():
                    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=mode)
                    page = context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/heinzel/")
                    page.wait_for_selector(".hz-button")
                    pick = {
                        name: next(t["value"] for t in TOKENS["color"]["tokens"] if t["name"] == name)[mode]
                        for name in (
                            "color-background",
                            "color-foreground",
                            "color-primary",
                            "color-primary-foreground",
                        )
                    }
                    lengths = {
                        "lg": pixels(str(token("radius", "radius-lg"))),
                        "xl": pixels(str(token("radius", "radius-xl"))),
                    }
                    want = page.evaluate(PROBE, [pick, lengths])
                    got = page.evaluate(READ)
                    label = f"site {mode} {size}"
                    expect(f"{label}: body[data-md-color-scheme]", got["scheme"], "slate" if slot else "default")
                    expect(f"{label}: --color-primary on body", got["bodyPrimary"], pick["color-primary"])
                    if not slot:
                        expect(f"{label}: --color-primary on html", got["rootPrimary"], pick["color-primary"])
                    expect(f"{label}: body background", got["bodyBackground"], want["color-background"])
                    expect(f"{label}: body colour", got["bodyColor"], want["color-foreground"])
                    expect(f"{label}: .hz-button background", got["buttonBackground"], want["color-primary"])
                    expect(f"{label}: .hz-button colour", got["buttonColor"], want["color-primary-foreground"])
                    expect(f"{label}: .hz-button radius", got["buttonRadius"], want["lg"])
                    expect(f"{label}: .hz-feature radius", got["cardRadius"], want["xl"])
                    target = int(str(token("spacing", "target")).removesuffix("px"))
                    if got["buttonHeight"] < target:
                        message = f"{label}: .hz-button is {got['buttonHeight']}px high, tokens say {target}px"
                        raise CheckError(message)
                    seen.append({"label": label, **got})
                    context.close()
        finally:
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return seen


def start_app(directory: Path) -> tuple[subprocess.Popen[str], str]:
    """`hz ui` on a free port in isolated directories; its URL, once it prints one.

    Raises:
        CheckError: If it prints no URL in time.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    env |= {
        "HZ_CONFIG_HOME": str(directory / "config"),
        "HZ_DATA_DIR": str(directory / "data"),
        "HZ_CACHE_DIR": str(directory / "cache"),
        "HZ_STATE_DIR": str(directory / "state"),
        "HZ_LOG_DIR": str(directory / "log"),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        "BROWSER": "true",
    }
    process = subprocess.Popen(
        ["uv", "run", "python", "-m", "heinzel", "ui", "--port", "0"],  # noqa: S607
        cwd=ROOT,
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    found: list[str] = []
    ready = threading.Event()

    def read() -> None:
        for line in process.stderr or ():
            match = re.search(r"(http://127\.0\.0\.1:\d+/\S*#token=\S+)", line)
            if match and not found:
                found.append(match.group(1))
                ready.set()

    threading.Thread(target=read, daemon=True).start()
    if not ready.wait(STARTUP_SECONDS):
        process.terminate()
        message = "hz ui printed no URL"
        raise CheckError(message)
    return process, found[0]


def check_app() -> dict[str, object]:
    """Run the Playwright driver against a fresh `hz ui`.

    Raises:
        CheckError: If the driver fails.
    """
    subprocess.run(  # noqa: S603 - fixed uv invocation and script
        [UV, "run", "--quiet", "--script", str(ROOT / "scripts" / "web_build.py"), "build"],
        cwd=ROOT,
        check=True,
        timeout=600,
    )
    with tempfile.TemporaryDirectory(prefix="heinzel-design-") as directory:
        process, url = start_app(Path(directory))
        try:
            driven = subprocess.run(  # noqa: S603 - fixed uv invocation and script
                [
                    UV,
                    "run",
                    "--quiet",
                    "--script",
                    str(ROOT / "scripts" / "web_build.py"),
                    "exec",
                    "node",
                    "e2e/design-tokens.mjs",
                    url,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        finally:
            process.terminate()
            process.wait(timeout=30)
    if driven.returncode:
        message = f"app: {driven.stderr.replace(url.split('#token=')[-1], '<token>')[-3000:]}"
        raise CheckError(message)
    return json.loads(driven.stdout.strip().splitlines()[-1])


def main() -> None:
    """Run the site check, the app check, or both."""
    only_site, only_app = "--site" in sys.argv, "--app" in sys.argv
    if not only_app:
        with sync_playwright() as playwright_api:
            for row in check_site(playwright_api):
                print("site", json.dumps(row))
        print("Site computed styles equal the tokens: light and dark, 375x812 and 1280x900")
    if not only_site:
        result = check_app()
        for row in result["observed"]:  # type: ignore[index]
            print("app", json.dumps(row))
        print("App computed styles equal the tokens: light and dark, 375x812 and 1280x900")


if __name__ == "__main__":
    try:
        main()
    except CheckError as error:
        print(f"Design check failed: {error}", file=sys.stderr)
        sys.exit(1)
