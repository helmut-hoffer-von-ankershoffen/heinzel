# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
"""Exercise the built product site under its GitHub Pages prefix, including non-JavaScript navigation."""

import itertools
import json
import re
import struct
import subprocess
import sys
import threading
import zlib
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
    """Serve the built artifacts from the root, as the production domain does."""

    def handle(self) -> None:
        """Treat a browser closing its keepalive connection as normal teardown."""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            return

    def do_GET(self) -> None:
        """Serve the build root; the production site lives at the root of its own domain."""
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


HELPERS = """() => [...document.querySelectorAll('[data-heinzel-helper]')].map(el => {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return {waypoint: el.classList.contains('hz-page-helper'), gutter: el.hasAttribute('data-gutter'),
        fixed: style.position === 'fixed',
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


def artwork() -> bytes:
    """A fixed 1200x800 picture, so page height never depends on the network."""
    width, height = 1200, 800
    rows = b"".join(b"\x00" + b"\xee\xe8\xdb" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def offline(page: "Page", base: str) -> None:
    """Serve only the built site; remote screenshots become a fixed picture and everything else is refused."""
    picture = artwork()

    def handle(route: Any) -> None:  # noqa: ANN401 - Playwright's route object
        url = route.request.url
        if url.startswith(base):
            route.continue_()
        elif route.request.resource_type == "image":
            route.fulfill(status=200, content_type="image/png", body=picture)
        else:
            route.abort()

    page.route("**/*", handle)


def settled(page: "Page") -> None:
    """Wait (bounded) for lifted guides' artwork, which loads eagerly because it is in view."""
    page.wait_for_function(
        "[...document.querySelectorAll('[data-gutter] img')].every(image => image.complete && image.naturalWidth > 0)",
        timeout=10_000,
    )
    page.evaluate("async () => { await document.fonts.ready; await new Promise(r => setTimeout(r, 150)); }")


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
        require(not any(item["hidden"] for item in lifted), f"Lifted guides must be visible: {where}")
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


def one_per_phone_screen(page: "Page", helpers: list[dict[str, Any]], route: str) -> None:
    """No phone screen holds two waypoints: they are at least a screen apart, less the first one's own height."""
    tops = sorted((item["top"], item["height"]) for item in helpers if item["waypoint"])
    for (first, height), (second, _) in itertools.pairwise(tops):
        require(
            second - first >= page.evaluate("innerHeight") - height,
            f"At most one character per phone screen: {route or 'home'}",
        )


def scroll_positions(page: "Page", width: int, errors: list[str]) -> None:
    """Scroll the open page top, middle, tall navigation and bottom, then back to the top, checking each."""
    for number, position in enumerate(("top", "middle", "navigation", "bottom", "top")):
        page.evaluate(SETTLE, position)
        page.wait_for_timeout(150)
        both = width in (1440, 1920) and position == "top"
        check_characters(page, f"{width}/{position}{number}", expect_both=both)
        require(not errors, f"Character layout script errors at {width}/{position}: {errors}")
        page.screenshot(path=str(REPORTS / f"helpers-{width}-{position}.png"))


def helper_layout(browser: "Browser", base: str, snapshot: dict[str, Any], axe: Path) -> None:
    """Keep characters clear of the article, navigation and footer at every supported width, on every plugin page."""
    resource = next(
        (item for item in snapshot["plugins"] if item["name"] == "heinzel-resource"), snapshot["plugins"][0]
    )
    page = browser.new_page()
    offline(page, base)
    errors: list[str] = []
    # Browsers report "ResizeObserver loop" when any observer's callback changes layout (the gallery's does);
    # the specification calls it benign, so only real script errors count here.
    page.on(
        "pageerror",
        lambda error: errors.append(f"{page.url}: {error}") if "ResizeObserver loop" not in str(error) else None,
    )
    try:
        for width in CHARACTER_WIDTHS:
            page.set_viewport_size({"width": width, "height": 844 if width < GUTTER_WIDTH else 960})
            for route in ["", *(f"catalog/{item['id']}/" for item in snapshot["plugins"]), "guides/install/"]:
                page.goto(base + route)
                settled(page)
                helpers = check_characters(page, f"{width}/{route or 'home'}")
                if width == 390:  # noqa: PLR2004
                    one_per_phone_screen(page, helpers, route)
            page.goto(base + f"catalog/{resource['id']}/")
            settled(page)
            scroll_positions(page, width, errors)
            if width in (1440, 390):
                audit(page, axe, f"helpers-{width}")
    finally:
        page.close()


def helper_hash(browser: "Browser", base: str, snapshot: dict[str, Any]) -> None:
    """Opening a page at a section keeps that section in view when the guides lift around it."""
    route = f"catalog/{snapshot['plugins'][0]['id']}/#install-and-try"
    for width in (1440, 1920):
        page = browser.new_page(viewport={"width": width, "height": 960})
        offline(page, base)
        page.goto(base + route)
        page.wait_for_timeout(600)
        top = page.evaluate("document.getElementById('install-and-try').getBoundingClientRect().top")
        require(0 <= top < 600, f"Lifting guides must not move the section being read ({width}: {top})")  # noqa: PLR2004
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
    helper_hash(browser, base, snapshot)
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
                    base = f"http://127.0.0.1:{server.server_port}/"
                    if "--characters" in sys.argv:  # the character guides alone: quick while iterating on them
                        helper_layout(browser, base, snapshot, candidates[0])
                        helper_hash(browser, base, snapshot)
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
