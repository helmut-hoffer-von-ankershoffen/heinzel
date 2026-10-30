# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
"""Exercise the built product site under its GitHub Pages prefix, including non-JavaScript navigation."""

import json
import re
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "site"


class SiteError(Exception):
    """The built site failed a behavior, layout or accessibility acceptance check."""


def require(condition: bool, message: str) -> None:
    """Fail with the visible outcome that could not be verified.

    Raises:
        SiteError: If an acceptance criterion is not met.
    """
    if not condition:
        raise SiteError(message)


class Handler(SimpleHTTPRequestHandler):
    """Serve only built artifacts under the production project's path prefix."""

    def handle(self) -> None:
        """Treat a browser closing its keepalive connection as normal teardown."""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            return

    def do_GET(self) -> None:
        """Map the deployed project prefix to the build root."""
        if not self.path.startswith("/heinzel/"):
            self.send_error(404)
            return
        self.path = self.path.removeprefix("/heinzel")
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - standard library callback
        """Keep passing checks quiet; failures are reported by the browser assertions."""


def audit(page: "Page", axe: Path, name: str) -> None:
    """Check every WCAG A/AA rule supported by the existing axe dependency."""
    page.add_script_tag(path=str(axe))
    result = page.evaluate(
        """async () => await axe.run(document, {runOnly: {type: 'tag',
        values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']}})"""
    )
    violations = result["violations"]
    (REPORTS / f"{name}-axe.json").write_text(json.dumps(violations, indent=2))
    require(not violations, f"Accessibility: {name}: {json.dumps(violations, indent=2)}")


def copy_command(page: "Page", identity: str) -> None:
    """Verify a real completed clipboard write or the actual fallback text selection."""
    button = page.locator(f'[data-copy="{identity}"]')
    expected = page.locator(f"#{identity}").inner_text()
    button.click()
    expect(button).to_have_text(re.compile(r"^(Copied|Select command)$"), timeout=3000)
    if button.inner_text() == "Copied":
        require(page.evaluate("window.__hzCopied") == expected, "Copy must complete writing the command")
    else:
        require(page.evaluate("window.getSelection().toString()") == expected, "Fallback must select the command")


def catalog_flow(page: "Page", total: int) -> None:
    """Filter by capabilities and category, recover empty results and follow a detail page."""
    search = page.get_by_role("searchbox", name="Find a plugin")
    search.fill("no-plugin-has-this-made-up-capability")
    require(page.locator("[data-plugin]:visible").count() == 0, "Search must narrow the actual cards")
    require(page.locator("#plugin-empty").is_visible(), "Empty search needs useful feedback")
    page.get_by_role("button", name="Reset filters").click()
    require(page.locator("[data-plugin]:visible").count() == total, "Reset must recover the whole catalog")
    require(search.evaluate("element => element === document.activeElement"), "Reset must restore keyboard focus")
    page.get_by_role("combobox", name="Category", exact=True).select_option("themes")
    require(page.locator("[data-plugin]:visible").count() > 0, "Theme filtering must expose actual themes")
    require(
        page.locator('[data-plugin]:visible:not([data-category="themes"])').count() == 0,
        "Category selection must hide other categories",
    )
    page.get_by_role("button", name="Reset filters").click()
    page.get_by_role("checkbox", name="Included in the app").check()
    require(page.locator("[data-plugin]:visible").count() > 0, "Included plugins must remain discoverable")
    page.get_by_role("button", name="Reset filters").click()
    search.fill(page.locator("[data-plugin] h3 a").first.inner_text())
    search.press("Tab")
    require(page.locator("#plugin-category").evaluate("e => e === document.activeElement"), "Keyboard order")
    page.locator("[data-plugin]:visible h3 a").first.click()
    require(page.get_by_role("heading", name="Install and try").is_visible(), "Detail page must load")
    copy_command(page, "plugin-install")


def sweep(browser: "Browser", engine: str, base: str, axe: Path, total: int) -> None:
    """Exercise desktop/phone and light/dark combinations with real rendered artifacts."""
    for width in (1440, 390):
        for mode in ("light", "dark"):
            context = browser.new_context(viewport={"width": width, "height": 960}, color_scheme=mode)
            context.add_init_script("""(() => {
                if (!navigator.clipboard?.writeText) return;
                const write = navigator.clipboard.writeText.bind(navigator.clipboard);
                navigator.clipboard.writeText = async value => {
                    await write(value); window.__hzCopied = value;
                };
            })()""")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error, found=errors: found.append(str(error)))
            for route, name in (
                ("", "home"),
                ("CATALOG/", "catalog"),
                ("guides/install/", "install"),
                ("guides/architecture/", "architecture"),
                ("guides/contribute/", "contribute"),
                ("PLUGIN_AUTHORING/", "authoring"),
            ):
                response = page.goto(base + route)
                require(response is not None and response.ok, f"Route must resolve: {route}")
                page.locator("h1").first.wait_for()
                if name == "architecture":
                    # Material replaces the source PRE with its rendered diagram's closed-shadow host DIV.
                    page.wait_for_function("document.querySelector('.mermaid')?.tagName === 'DIV'")
                require(
                    page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"),
                    f"Horizontal overflow: {engine}/{width}/{mode}/{name}",
                )
                require(
                    page.locator("img").evaluate_all("els => els.every(e => !e.complete || e.naturalWidth > 0)"),
                    f"Broken image: {name}",
                )
                identity = f"{engine}-{width}-{mode}-{name}"
                audit(page, axe, identity)
                page.screenshot(path=str(REPORTS / f"{identity}.png"), full_page=True)
                if name == "catalog":
                    catalog_flow(page, total)
                    audit(page, axe, identity + "-detail")
                if name == "install":
                    copy_command(page, "app-install")
            require(not errors, f"Uncaught browser errors: {errors}")
            context.close()
    context = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base + "CATALOG/")
    require(page.locator("[data-plugin]:visible").count() == total, "Every plugin must be reachable without JS")
    page.locator("[data-plugin] h3 a").first.click()
    require(page.locator("#plugin-install").is_visible(), "Static plugin detail must work without JS")
    context.close()


def main() -> None:
    """Serve and test the build; require the existing locked axe dependency."""
    candidates = sorted((ROOT / "web/node_modules/.pnpm").glob("axe-core@*/node_modules/axe-core/axe.min.js"))
    require(bool(candidates), "Install locked web dependencies first: uv run --script scripts/web_build.py install")
    REPORTS.mkdir(parents=True, exist_ok=True)
    snapshot = json.loads((ROOT / "site/catalog.json").read_text())
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "site")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                browser = getattr(playwright, engine).launch()
                try:
                    sweep(
                        browser,
                        engine,
                        f"http://127.0.0.1:{server.server_port}/heinzel/",
                        candidates[0],
                        len(snapshot["plugins"]),
                    )
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print("Site accepted: Chromium + WebKit, desktop + phone, light + dark, filters, keyboard, no-JS and axe")


if __name__ == "__main__":
    if "--install" in sys.argv:
        raise SystemExit(
            subprocess.call(  # noqa: S603 - fixed interpreter/module, only a fixed dependency switch
                [
                    sys.executable,
                    "-m",
                    "playwright",
                    "install",
                    "chromium",
                    "webkit",
                    *(["--with-deps"] if "--with-deps" in sys.argv else []),
                ],
                timeout=600,
            )
        )
    main()
