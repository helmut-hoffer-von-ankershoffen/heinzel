# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
"""Exercise the built product site under its GitHub Pages prefix, including non-JavaScript navigation."""

import itertools
import json
import re
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "site"
MAX_HELPERS = 5
FITS_VIEWPORT = "document.documentElement.scrollWidth <= innerWidth + 1"


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


def manual_theme_flow(page: "Page", mode: str, axe: Path, identity: str) -> None:
    """Manual palette choices must update artwork independently of the operating system's preference."""
    initial = "default" if mode == "light" else "slate"
    selected = "slate" if mode == "light" else "default"
    for target in (selected, initial):
        page.locator('[data-md-component="palette"] label:visible').click()
        expect(page.locator("body")).to_have_attribute("data-md-color-scheme", target)
        suffix = "hero-dark.svg" if target == "slate" else "hero-light.svg"
        page.wait_for_function(
            "suffix => document.querySelector('.hz-hero-art img').currentSrc.endsWith(suffix)", arg=suffix
        )
        page.locator(".hz-hero-art img").evaluate("image => image.decode()")
        if target == selected:
            audit(page, axe, identity + "-manual-toggle")
            page.reload()
            expect(page.locator("body")).to_have_attribute("data-md-color-scheme", target)
            page.wait_for_function(
                "suffix => document.querySelector('.hz-hero-art img').currentSrc.endsWith(suffix)", arg=suffix
            )
            page.locator(".hz-hero-art img").evaluate("image => image.decode()")


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


def gallery_flow(page: "Page", count: int) -> None:
    """Navigate a real gallery with buttons, keyboard and native horizontal scrolling.

    Raises:
        SiteError: If the gallery cannot be navigated or makes the page overflow.
    """
    if count <= 1:
        return
    track = page.locator("[data-gallery-track]")
    counter = page.locator("[data-gallery-count]")
    previous = page.get_by_role("button", name="Previous screenshot", exact=True)
    following = page.get_by_role("button", name="Next screenshot", exact=True)
    expect(counter).to_have_text(f"1 of {count}")
    expect(previous).to_be_disabled()
    following.click()
    expect(counter).to_have_text(f"2 of {count}")
    track.focus()
    track.press("End")
    expect(counter).to_have_text(f"{count} of {count}")
    expect(following).to_be_disabled()
    track.press("Home")
    expect(counter).to_have_text(f"1 of {count}")
    track.scroll_into_view_if_needed()
    box = track.bounding_box()
    if box is None:
        msg = "Screenshot gallery must be visible for horizontal scrolling"
        raise SiteError(msg)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(80, box["height"] / 2))
    page.mouse.wheel(1500, 0)
    page.wait_for_function("document.querySelector('[data-gallery-track]').scrollLeft > 0")
    require(
        page.evaluate(FITS_VIEWPORT),
        "The gallery scrolls internally, never the whole page",
    )


def media_flow(page: "Page", base: str, snapshot: dict[str, Any]) -> None:
    """Check gallery images and explicit playback without contacting YouTube."""
    for project in snapshot["plugins"]:
        if not project.get("screenshots") and not project.get("soundscapes"):
            continue
        media = page.context.new_page()
        try:
            requests = []
            media.route("https://*.youtube-nocookie.com/**", lambda route: route.abort())
            media.on("request", lambda request, found=requests: found.append(request.url))
            media.goto(base + f"catalog/{project['id']}/")
            require(
                media.evaluate(FITS_VIEWPORT),
                "Plugin media must fit desktop and phone layouts",
            )
            require(
                1 <= media.locator("[data-heinzel-helper]").count() <= MAX_HELPERS,
                "Every generated plugin page needs contextual character explainers",
            )
            images = media.locator(".hz-screenshots img")
            require(images.count() == len(project.get("screenshots", [])), "Every declared capture must be visible")
            gallery_flow(media, images.count())
            for image in images.all():
                image.scroll_into_view_if_needed()
                image.evaluate("image => image.decode()")
            require(not any("youtube" in url for url in requests), "YouTube must not load before Play")
            sounds = project.get("soundscapes", [])
            if sounds:
                media.get_by_role("button", name=f"Play {sounds[0]['title']}", exact=True).click()
                player = media.locator(".hz-soundscape iframe")
                expect(player).to_have_attribute(
                    "src", f"https://www.youtube-nocookie.com/embed/{sounds[0]['video']}?autoplay=1"
                )
                expect(player).to_have_attribute("title", sounds[0]["title"])
        finally:
            media.close()


HELPERS = """() => [...document.querySelectorAll('[data-heinzel-helper]')].map(el => {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return {gutter: el.hasAttribute('data-gutter'), fixed: style.position === 'fixed',
        hidden: style.visibility === 'hidden', lane: el.dataset.lane || '', top: r.top + scrollY, height: r.height,
        rect: {left: r.left, right: r.right, top: r.top, bottom: r.bottom}};
})"""
OBSTACLES = """() => [...document.querySelectorAll(
    '.md-content,.md-sidebar__inner > .md-nav,.md-header,.md-tabs,.md-footer')]
    .map(el => el.getBoundingClientRect()).filter(r => r.width && r.height)
    .map(r => ({left: r.left, right: r.right, top: r.top, bottom: r.bottom}))"""
GUTTER_WIDTH = 1100
CHARACTER_WIDTHS = (1024, 1280, 1440, 1920, 390)
SETTLE = """async position => {
    document.querySelectorAll('.md-sidebar__inner > .md-nav').forEach(nav => {
        if (position === 'navigation') nav.style.minHeight = '500px';
        else nav.style.removeProperty('min-height');
    });
    const y = ['top', 'navigation'].includes(position) ? 0 : position === 'middle'
        ? document.body.scrollHeight / 2 : document.body.scrollHeight;
    window.scrollTo(0, y);
    await document.fonts.ready;
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
}"""


def settled(page: "Page") -> None:
    """Wait (bounded) for lifted guides' artwork, which loads eagerly because it is in view."""
    page.wait_for_function(
        "[...document.querySelectorAll('[data-gutter] img')].every(image => image.complete && image.naturalWidth > 0)",
        timeout=10_000,
    )


def check_characters(page: "Page", where: str, *, expect_both: bool = False) -> list[dict[str, Any]]:
    """Characters stay within budget and out of the way at the page's current width and scroll position."""
    width = page.evaluate("innerWidth")
    helpers = page.evaluate(HELPERS)
    require(1 <= len(helpers) <= MAX_HELPERS, f"One to five characters per page: {where}")
    require(page.evaluate(FITS_VIEWPORT), f"Character layout overflows: {where}")
    lifted = [item for item in helpers if item["gutter"]]
    require(len(lifted) <= 2, f"At most two gutter guides: {where}")  # noqa: PLR2004
    require(
        all(item["gutter"] == item["fixed"] for item in helpers),
        f"Only gutter guides may be fixed (never overlay inline characters): {where}",
    )
    if width < GUTTER_WIDTH:
        require(not lifted, f"Tablet and phone widths keep characters inline: {where}")
    if expect_both:
        candidates = [item for item in helpers if item["lane"]]
        require(len(lifted) == len(candidates) > 0, f"Free desktop gutters must show every lane guide: {where}")
    obstacles = page.evaluate(OBSTACLES)
    for item in lifted:
        r = item["rect"]
        inside = r["left"] >= 0 and r["right"] <= width and r["bottom"] <= page.evaluate("innerHeight")
        require(inside, f"Gutter guide leaves the viewport: {where}")
        if item["hidden"]:
            continue  # pushed beneath the header by the footer: unseen, never overlapping
        require(r["top"] >= 0, f"Gutter guide above the viewport: {where}")
        for box in obstacles:
            overlap = r["left"] < box["right"] and r["right"] > box["left"]
            overlap = overlap and r["top"] < box["bottom"] and r["bottom"] > box["top"]
            require(not overlap, f"Gutter guide overlaps the article, navigation, header or footer: {where}")
    return helpers


def helper_layout(browser: "Browser", base: str, snapshot: dict[str, Any], axe: Path) -> None:
    """Keep characters clear of the article, navigation and footer at every supported width, on every plugin page."""
    resource = next(
        (item for item in snapshot["plugins"] if item["name"] == "heinzel-resource"), snapshot["plugins"][0]
    )
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        for width in CHARACTER_WIDTHS:
            page.set_viewport_size({"width": width, "height": 844 if width < GUTTER_WIDTH else 960})
            for route in ["", *(f"catalog/{item['id']}/" for item in snapshot["plugins"]), "guides/install/"]:
                page.goto(base + route)
                settled(page)
                helpers = check_characters(page, f"{width}/{route or 'home'}")
                if width == 390:  # noqa: PLR2004
                    tops = sorted((item["top"], item["height"]) for item in helpers)
                    for (first, height), (second, _) in itertools.pairwise(tops):
                        require(
                            second - first >= page.evaluate("innerHeight") - height,
                            f"At most one character per phone screen: {route or 'home'}",
                        )
            page.goto(base + f"catalog/{resource['id']}/")
            settled(page)
            for position in ("top", "middle", "navigation", "bottom"):
                page.evaluate(SETTLE, position)
                check_characters(page, f"{width}/{position}", expect_both=width in (1440, 1920) and position == "top")
                require(not errors, f"Character layout script errors at {width}/{position}")
                page.screenshot(path=str(REPORTS / f"helpers-{width}-{position}.png"))
            if width in (1440, 390):
                audit(page, axe, f"helpers-{width}")
    finally:
        page.close()


def helper_fallbacks(browser: "Browser", base: str, snapshot: dict[str, Any]) -> None:
    """Without JavaScript every waypoint is on the page; with reduced motion nothing moves."""
    route = f"catalog/{snapshot['plugins'][0]['id']}/"
    plain = browser.new_context(java_script_enabled=False, viewport={"width": 1920, "height": 960})
    page = plain.new_page()
    page.goto(base + route)
    helpers = page.evaluate(HELPERS)
    require(len(helpers) >= 2, "Waypoints are server-rendered without JavaScript")  # noqa: PLR2004
    require(not any(item["fixed"] or item["gutter"] for item in helpers), "No gutter without JavaScript")
    require(page.locator(".hz-page-helper a:visible").count() == len(helpers), "Every waypoint link must show")
    plain.close()
    calm = browser.new_context(reduced_motion="reduce", viewport={"width": 1920, "height": 960})
    page = calm.new_page()
    page.goto(base + route)
    settled(page)
    still = page.evaluate(
        """() => [...document.querySelectorAll('[data-heinzel-helper], [data-heinzel-helper] *')].every(el => {
        const style = getComputedStyle(el);
        return style.animationName === 'none' && parseFloat(style.transitionDuration) === 0;
    }) && !document.querySelector('[data-heinzel-helper] [aria-live]')"""
    )
    require(still, "Characters add no motion or live regions")
    calm.close()


def sweep(browser: "Browser", engine: str, base: str, axe: Path, snapshot: dict[str, Any]) -> None:
    """Exercise desktop/phone and light/dark combinations with real rendered artifacts."""
    total = len(snapshot["plugins"])
    helper_layout(browser, base, snapshot, axe)
    helper_fallbacks(browser, base, snapshot)
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
                    page.evaluate(FITS_VIEWPORT),
                    f"Horizontal overflow: {engine}/{width}/{mode}/{name}",
                )
                require(
                    page.locator("img").evaluate_all("els => els.every(e => !e.complete || e.naturalWidth > 0)"),
                    f"Broken image: {name}",
                )
                require(
                    1 <= page.locator("[data-heinzel-helper]").count() <= MAX_HELPERS,
                    f"Every page needs one to five character explainers: {name}",
                )
                identity = f"{engine}-{width}-{mode}-{name}"
                audit(page, axe, identity)
                if name == "home":
                    manual_theme_flow(page, mode, axe, identity)
                page.screenshot(path=str(REPORTS / f"{identity}.png"), full_page=True)
                if name == "catalog":
                    catalog_flow(page, total)
                    audit(page, axe, identity + "-detail")
                if name == "install":
                    copy_command(page, "app-install")
            media_flow(page, base, snapshot)
            require(not errors, f"Uncaught browser errors: {errors}")
            context.close()
    context = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base + "CATALOG/")
    require(page.locator("[data-plugin]:visible").count() == total, "Every plugin must be reachable without JS")
    page.locator("[data-plugin] h3 a").first.click()
    require(page.locator("#plugin-install").is_visible(), "Static plugin detail must work without JS")
    for project in snapshot["plugins"]:
        for _sound in project.get("soundscapes", []):
            page.goto(base + f"catalog/{project['id']}/")
            expect(page.get_by_role("link", name="Listen on YouTube ↗").first).to_be_visible()
            require(page.locator("iframe").count() == 0, "No-JS media must retain links without loading players")
            break
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
                    base = f"http://127.0.0.1:{server.server_port}/heinzel/"
                    if "--characters" in sys.argv:  # the character guides alone: quick while iterating on them
                        helper_layout(browser, base, snapshot, candidates[0])
                        helper_fallbacks(browser, base, snapshot)
                    else:
                        sweep(browser, engine, base, candidates[0], snapshot)
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if "--characters" in sys.argv:
        print("Characters accepted: Chromium + WebKit, 390/1024/1280/1440/1920, gutters, no-JS, reduced motion, axe")
        return
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
