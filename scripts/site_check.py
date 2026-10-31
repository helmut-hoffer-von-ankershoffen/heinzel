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
from typing import TYPE_CHECKING, Any

from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "site"
MAX_HELPERS = 5
HOME_STORIES = 3
TOUR_STORIES = 3
TOUR_SLIDES = 3
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
    page.evaluate("""() => new Promise(resolve => {
        const track = document.querySelector('[data-gallery-track]');
        let last = track.scrollLeft;
        let stable = 0;
        function settled() {
            const position = track.scrollLeft;
            stable = Math.abs(position - last) < .5 ? stable + 1 : 0;
            last = position;
            if (stable >= 4) resolve();
            else requestAnimationFrame(settled);
        }
        requestAnimationFrame(settled);
    })""")
    require(
        page.evaluate(FITS_VIEWPORT),
        "The gallery scrolls internally, never the whole page",
    )


def zoom_flow(page: "Page", axe: Path, identity: str, selector: str = ".hz-screenshots figure a") -> None:
    """Inspect an actual image, zoom to native pixels, pan, fit and return focus."""
    link = page.locator(selector).first
    source = link.locator("img")
    link.scroll_into_view_if_needed()
    page.wait_for_function(
        "image => image.complete && image.naturalWidth > 0", arg=source.element_handle(), timeout=15000
    )
    source.evaluate("image => image.decode()")
    link.click()
    viewer = page.locator("dialog.hz-image-viewer")
    expect(viewer).to_be_visible()
    image = viewer.locator("img")
    image.evaluate("image => image.decode()")
    page.get_by_role("button", name="Actual size", exact=True).click()
    require(image.evaluate("image => Math.abs(image.width - image.naturalWidth) <= 1"), "Actual pixels")
    page.get_by_role("button", name="Zoom in", exact=True).click()
    require(image.evaluate("image => image.width > image.naturalWidth"), "Zoom must enlarge the screenshot")
    viewport = viewer.locator(".hz-image-viewport")
    viewport.evaluate("element => element.scrollTo(element.scrollWidth, element.scrollHeight)")
    require(
        viewport.evaluate(
            "element => (element.scrollWidth <= element.clientWidth && "
            "element.scrollHeight <= element.clientHeight) || "
            "element.scrollLeft > 0 || element.scrollTop > 0"
        ),
        "An oversized screenshot must pan inside its viewport",
    )
    page.get_by_role("button", name="Fit", exact=True).click()
    require(
        image.evaluate(
            "image => image.width <= image.parentElement.clientWidth + 1 && "
            "image.height <= image.parentElement.clientHeight + 1"
        ),
        "Fit must reveal the whole screenshot",
    )
    expect(viewer.get_by_role("link", name="Open original")).to_have_attribute(
        "href", link.evaluate("link => link.href")
    )
    audit(page, axe, identity + "-zoom")
    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    require(link.evaluate("link => link === document.activeElement"), "Closing zoom must return focus")


def tour_flow(page: "Page", axe: Path, identity: str) -> None:
    """Each tour story keeps one guide and an independent, keyboard-navigable lead-plus-zoom gallery."""
    stories = page.locator(".hz-tour-story")
    require(stories.count() == TOUR_STORIES, "The product tour must show three stories")
    for index in range(TOUR_STORIES):
        story = stories.nth(index)
        story.scroll_into_view_if_needed()
        slides = story.locator(".hz-screenshots figure")
        require(slides.count() == TOUR_SLIDES, "Each story needs a lead, a zoomed detail and a dark view")
        counter = story.locator("[data-gallery-count]")
        expect(counter).to_have_text(f"1 of {TOUR_SLIDES}")
        story.get_by_role("button", name=re.compile("^Next .* screenshot$")).click()
        expect(counter).to_have_text(f"2 of {TOUR_SLIDES}")
    require(
        page.locator(".hz-tour-story[data-heinzel-helper]").count() == TOUR_STORIES,
        "Every story carries exactly one character guide",
    )
    zoom_flow(page, axe, identity, ".hz-tour-story .hz-screenshots figure a")
    require(page.evaluate(FITS_VIEWPORT), "The tour must not overflow the viewport")


def gallery_shape_flow(page: "Page") -> None:
    """Square and portrait fixtures retain natural proportions instead of a landscape letterbox."""
    track = page.locator("[data-gallery-track]")
    track.focus()
    track.press("Home")
    image = track.locator("img").first
    for width, height in ((384, 382), (390, 844)):
        image.evaluate(
            """async (image, size) => {
                const [width, height] = size;
                image.src = 'data:image/svg+xml,' + encodeURIComponent(
                    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">`
                    + '<rect width="100%" height="100%" fill="gray"/></svg>');
                await image.decode();
            }""",
            [width, height],
        )
        require(
            image.evaluate(
                "image => Math.abs(image.width / image.height - image.naturalWidth / image.naturalHeight) < .01"
            ),
            "Square and portrait images must keep their natural proportions",
        )
        page.wait_for_function(
            """() => {
                const track = document.querySelector('[data-gallery-track]');
                return track.clientHeight >= track.firstElementChild.getBoundingClientRect().height + 15;
            }"""
        )
        require(page.evaluate(FITS_VIEWPORT), "Portrait captures must never widen the page")


def media_flow(page: "Page", base: str, snapshot: dict[str, Any], axe: Path, identity: str) -> None:
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
            if images.count():
                zoom_flow(media, axe, identity + "-" + project["id"])
                gallery_shape_flow(media)
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


def sweep(browser: "Browser", engine: str, base: str, axe: Path, snapshot: dict[str, Any]) -> None:
    """Exercise desktop/phone and light/dark combinations with real rendered artifacts."""
    total = len(snapshot["plugins"])
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
                ("guides/tour/", "tour"),
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
                broken = page.evaluate(
                    "() => [...document.images].filter(e => e.complete && e.naturalWidth === 0).map(e => e.currentSrc)"
                )
                require(not broken, f"Broken image: {engine}/{width}/{mode}/{name}: {broken}")
                require(
                    1 <= page.locator("[data-heinzel-helper]").count() <= MAX_HELPERS,
                    f"Every page needs one to five character explainers: {name}",
                )
                identity = f"{engine}-{width}-{mode}-{name}"
                audit(page, axe, identity)
                if name == "home":
                    stories = page.locator(".hz-product-stories > .hz-product-story")
                    require(
                        stories.count() == HOME_STORIES
                        and stories.evaluate_all(
                            "stories => stories.every(story => story.querySelectorAll(':scope > figure').length === 1)"
                        ),
                        "The product tour must retain three direct stories with one full-width image each",
                    )
                    manual_theme_flow(page, mode, axe, identity)
                    zoom_flow(page, axe, identity, ".hz-product-story figure a")
                page.screenshot(path=str(REPORTS / f"{identity}.png"), full_page=True)
                if name == "catalog":
                    catalog_flow(page, total)
                    audit(page, axe, identity + "-detail")
                follow_up = {
                    "install": partial(copy_command, page, "app-install"),
                    "tour": partial(tour_flow, page, axe, identity),
                }.get(name)
                if follow_up:
                    follow_up()
            media_flow(page, base, snapshot, axe, f"{engine}-{width}-{mode}")
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
                    sweep(
                        browser,
                        engine,
                        f"http://127.0.0.1:{server.server_port}/heinzel/",
                        candidates[0],
                        snapshot,
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
