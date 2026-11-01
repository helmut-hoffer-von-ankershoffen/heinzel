# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging==26.3"]
# ///
"""Deterministic, escaped website views over the public catalog snapshot (no provider imports)."""

import hashlib
import html
import json
import os
import posixpath
import re
import shlex
import tomllib
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

from site_catalog import (
    CATEGORIES,
    COLOR,
    MAX_PROJECTS,
    NAME,
    REPOSITORY,
    REVISION,
    ROOT,
    SNAPSHOT,
    VERSION,
    VIDEO,
    Object,
    relative,
    text,
)


def load_snapshot(path: Path = SNAPSHOT) -> Object:
    """Validate the offline/publication boundary before generating any website files.

    Raises:
        ValueError: If the snapshot contains an unverified identity or unsafe presentation.
    """
    data = json.loads(path.read_text())
    if data.get("format") != 1 or not VERSION.fullmatch(str(data.get("release", {}).get("version", ""))):
        msg = "The website requires a versioned catalog and a complete release recommendation"
        raise ValueError(msg)
    data["release"]["install"] = (SNAPSHOT.parent / "install-command.txt").read_text().strip()
    data["release"]["install_script"] = (SNAPSHOT.parent / "install-script-command.txt").read_text().strip()
    if not 0 < len(data.get("plugins", [])) <= MAX_PROJECTS:
        msg = "Catalog must be bounded and nonempty"
        raise ValueError(msg)
    sources = [checked_source(source) for source in data.get("sources", [])]
    data["sources"] = sources
    seen = set()
    for project in data.get("plugins", []):
        source = checked_source(project.get("source", {}))
        project["source"] = source
        if source not in sources:
            msg = "Every project must belong to a confirmed-public snapshot source"
            raise ValueError(msg)
        if not REVISION.fullmatch(str(source.get("revision", ""))) or not NAME.fullmatch(project.get("name", "")):
            msg = "Catalog entries need immutable source and distribution identities"
            raise ValueError(msg)
        key = f"{source['repository']}:{project['directory']}:{project['name']}"
        identity = project["name"] + "-" + hashlib.sha256(key.encode()).hexdigest()[:10]
        if project.get("id") != identity or identity in seen:
            msg = "Catalog page identities must be unique and safe"
            raise ValueError(msg)
        seen.add(project["id"])
        if project["directory"] != ".":
            relative(project["directory"])
        text(project["title"], 100)
        text(project["summary"])
        validate_presentation(project)

    return data


def checked_source(source: Object) -> Object:
    """Return only public, immutable source identity fields.

    Raises:
        ValueError: If any source is private, incomplete or malformed.
    """
    if source.get("visibility") != "public" or not REPOSITORY.fullmatch(str(source.get("repository", ""))):
        msg = "Only confirmed-public source identities may enter the website"
        raise ValueError(msg)
    if not REVISION.fullmatch(str(source.get("revision", ""))):
        msg = "Public source identity must be immutable"
        raise ValueError(msg)
    return {key: source[key] for key in ("repository", "revision", "visibility")}


MAX_DECLARATIONS = 24
MAX_IMAGES = 8
HELPER_WORDS = 9
HEADING_LEVELS = 6
HELPER_CHARACTERS = 80
HELPER_FALLBACK = "See this helper working in the real UI."
# A character never promises what an owner's metadata merely mentions: no autonomy, delivery or endorsement.
BANNED_CUE = re.compile(
    r"autonomous|automatic|while you sleep|\b(sends?|publish(es)?|delivers?)\b|customer|endors|testimonial",
    re.IGNORECASE,
)


def validate_presentation(project: Object) -> None:
    """Narrow presentation data and reconstruct links and commands from checked identity.

    Raises:
        ValueError: If untrusted declarations or artwork exceed their boundary.
    """
    source = project["source"]
    for key in ("version", "core"):
        text(project[key], 500)
    for key in ("highlights", "provides", "requires"):
        if len(project[key]) > MAX_DECLARATIONS:
            msg = "Catalog declarations must be bounded"
            raise ValueError(msg)
        for value in project[key]:
            text(value, 180)
    project["category"] = project["category"] if project["category"] in CATEGORIES else "utilities"
    folder = project["directory"]
    project["url"] = f"https://github.com/{source['repository']}/tree/{source['revision']}/{folder}"
    spec = f"git+https://github.com/{source['repository']}.git@{source['revision']}"
    if folder != ".":
        spec += f"#subdirectory={folder}"
    project["install"] = f"hz system plugin add {shlex.quote(spec)}"
    validate_assets(project)


def validate_assets(project: Object) -> None:
    """Validate bounded theme labels and immutable raster artwork.

    Raises:
        ValueError: If a supplied asset is unsafe to display.
    """
    source = project["source"]
    if len(project["themes"]) > MAX_DECLARATIONS or len(project.get("artwork", [])) > MAX_IMAGES:
        msg = "Catalog assets must be bounded"
        raise ValueError(msg)
    for theme in project["themes"]:
        text(theme["title"], 100)
    validate_media(project)
    screenshots = project.get("screenshots", [])
    for artwork in [*project.get("artwork", []), *screenshots]:
        prefix = f"https://raw.githubusercontent.com/{source['repository']}/{source['revision']}/"
        if not artwork["url"].startswith(prefix):
            msg = "Artwork must come from its immutable public source"
            raise ValueError(msg)
        asset = relative(artwork["url"].removeprefix(prefix))
        if project["directory"] != "." and not asset.startswith(project["directory"] + "/"):
            msg = "Artwork must stay inside its owning project"
            raise ValueError(msg)
        if Path(asset).suffix not in {".png", ".jpg", ".webp"}:
            msg = "Supplier artwork must be raster images, never executable documents"
            raise ValueError(msg)


def validate_media(project: Object) -> None:
    """Validate bounded runtime capture labels and YouTube identities.

    Raises:
        ValueError: If supplied media metadata is unsafe.
    """
    screenshots = project.get("screenshots", [])
    soundscapes = project.get("soundscapes", [])
    if not isinstance(screenshots, list) or len(screenshots) > MAX_IMAGES:
        msg = "Screenshots must be bounded"
        raise ValueError(msg)
    for shot in screenshots:
        if shot.get("surface") not in {"ui", "cli"}:
            msg = "Screenshots need a UI/CLI surface"
            raise ValueError(msg)
        text(shot["caption"], 180)
    if not isinstance(soundscapes, list) or len(soundscapes) > MAX_DECLARATIONS:
        msg = "Soundscapes must be bounded"
        raise ValueError(msg)
    for sound in soundscapes:
        text(sound["title"], 100)
        if not isinstance(sound.get("video"), str) or not VIDEO.fullmatch(sound["video"]):
            msg = "Soundscapes require a YouTube video identity"
            raise ValueError(msg)


def escape(value: object) -> str:
    """Escape metadata as text, including inside HTML attributes."""
    return html.escape(str(value), quote=True)


def href(target: str, page_url: str) -> str:
    """A relative static-site URL that also works under GitHub Pages' project prefix."""
    base = page_url.rstrip("/") if page_url.endswith("/") else posixpath.dirname(page_url)
    return posixpath.relpath(target, base or ".") + ("/" if target.endswith("/") else "")


def palette(theme: Object, mode: str, token: str, fallback: str) -> str:
    """Only checked hex tokens can enter generated artwork or CSS."""
    value = theme.get("palette", {}).get(mode, {}).get(token, "")
    return value if isinstance(value, str) and COLOR.fullmatch(value) else fallback


def theme_art(theme: Object, mode: str) -> str:
    """An original inert UI illustration drawn from a declared palette, never supplier SVG."""
    background = palette(theme, mode, "background", "#faf8f4")
    card = palette(theme, mode, "card", "#ffffff")
    foreground = palette(theme, mode, "foreground", "#242c29")
    primary = palette(theme, mode, "primary", "#a4462d")
    sidebar = palette(theme, mode, "sidebar", "#efece5")
    border = palette(theme, mode, "border", "#d9ddd6")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="600" viewBox="0 0 960 600">
<title>{escape(theme["title"])} · {mode} palette preview</title>
<rect width="960" height="600" rx="28" fill="{background}"/>
<circle cx="846" cy="62" r="170" fill="{primary}" opacity=".09"/>
<rect x="52" y="54" width="856" height="492" rx="18" fill="{card}" stroke="{border}"/>
<path d="M70 54h170v492H70a18 18 0 0 1-18-18V72a18 18 0 0 1 18-18" fill="{sidebar}"/>
<circle cx="92" cy="96" r="16" fill="{primary}"/>
<path d="m84 97 7-9 9 9-9 7Z" fill="{card}"/>
<g fill="{foreground}" font-family="system-ui,sans-serif">
<text x="120" y="102" font-size="16" font-weight="650">Heinzel</text>
<text x="278" y="108" font-size="15">Your workspace</text>
<text x="278" y="170" font-size="28" font-weight="650">Room for good work.</text>
</g>
<rect x="72" y="142" width="148" height="34" rx="7" fill="{primary}" opacity=".14"/>
<g fill="{foreground}" opacity=".35">
<rect x="89" y="156" width="81" height="7" rx="3"/>
<rect x="89" y="202" width="93" height="7" rx="3"/>
<rect x="89" y="248" width="66" height="7" rx="3"/>
<rect x="89" y="294" width="102" height="7" rx="3"/>
</g>
<g stroke="{border}" fill="{background}">
<rect x="278" y="206" width="290" height="134" rx="12"/>
<rect x="588" y="206" width="280" height="134" rx="12"/>
<rect x="278" y="360" width="590" height="144" rx="12"/>
</g>
<g fill="{primary}">
<circle cx="310" cy="241" r="7"/>
<rect x="310" y="289" width="188" height="10" rx="5" opacity=".2"/>
<rect x="310" y="289" width="124" height="10" rx="5"/>
<path d="m619 301 36-34 34 11 32-30 40 11 44-32 30 8" fill="none" stroke="{primary}" stroke-width="5"
stroke-linecap="round"/>
<rect x="310" y="391" width="34" height="34" rx="9" opacity=".15"/>
<rect x="310" y="449" width="34" height="23" rx="7" opacity=".15"/>
</g>
<g fill="{foreground}" opacity=".7">
<rect x="328" y="237" width="112" height="8" rx="4"/>
<rect x="619" y="237" width="98" height="8" rx="4"/>
<rect x="364" y="397" width="258" height="8" rx="4"/>
<rect x="364" y="416" width="193" height="6" rx="3" opacity=".45"/>
<rect x="364" y="454" width="224" height="8" rx="4"/>
</g></svg>"""


def command(value: str, identity: str) -> str:
    """An accessible copy control with useful static text when JavaScript is unavailable."""
    return f"""<div class="hz-command">
<code id="{identity}">{escape(value)}</code>
<button type="button" data-copy="{identity}" aria-label="Copy command">Copy</button></div>"""


def cards(data: Object, page_url: str, *, featured: bool = False) -> str:
    """A complete server-rendered collection; JavaScript only narrows what is already present."""
    projects = sorted(data["plugins"], key=lambda p: (not bool(p["included_version"]), not p["maintained"], p["title"]))
    if featured:
        projects = [p for p in projects if p["included_version"]][:4]
    result = []
    for project in projects:
        target = href(f"catalog/{project['id']}/", page_url)
        category = project.get("category", "utilities")
        badge = "Included in the app" if project["included_version"] else "Source plugin"
        captures = [shot for shot in project.get("screenshots", []) if shot["surface"] == "ui"]
        if captures or project.get("artwork"):
            art = (captures or project["artwork"])[0]
            visual = (
                f'<img src="{escape(art["url"])}" alt="{escape(art.get("caption", art.get("alt", "Plugin preview")))}" '
                'loading="lazy" referrerpolicy="no-referrer">'
            )
        elif project["themes"]:
            image = href(f"catalog/art/{project['id']}-0-light.svg", page_url)
            visual = (
                f'<img src="{image}" alt="{escape(project["themes"][0]["title"])} palette preview" '
                'width="960" height="600" loading="lazy">'
            )
        else:
            initials = "".join(part[:1] for part in project["title"].split()[:2])
            visual = (
                f'<span class="hz-monogram" aria-hidden="true">{escape(initials)}</span>'
                f'<span class="hz-art-label">{escape(category)}</span>'
            )
        searchable = [
            project["title"],
            project["summary"],
            project["name"],
            *project["highlights"],
            *project["provides"],
            *project["requires"],
        ]
        searchable.extend(value for theme in project["themes"] for value in (theme["name"], theme["title"]))
        search = " ".join(searchable).lower()
        result.append(f"""<article class="hz-plugin" data-plugin data-category="{escape(category)}"
data-search="{escape(search)}" data-included="{str(bool(project["included_version"])).lower()}">
<a class="hz-plugin-art hz-art-{escape(category)}" href="{target}">{visual}</a>
<div class="hz-plugin-body">
<div class="hz-plugin-meta">
<span>{escape(category)}</span>
<span>{badge}</span>
</div>
<h3>
<a href="{target}">{escape(project["title"])}</a>
</h3>
<p>{escape(project["summary"])}</p>
<div class="hz-plugin-foot">
<span>Source v{escape(project["version"])}</span>
<a href="{target}" aria-label="Explore {escape(project["title"])}">Explore ↗</a>
</div>
</div></article>""")
    return '<div class="hz-plugin-grid">' + "\n".join(result) + "</div>"


def owner_art(project: Object) -> str:
    """Show optional source-owned raster artwork for themes as well as capability plugins."""
    images = "".join(
        f'<figure><img src="{escape(art["url"])}" alt="{escape(art["alt"])}" '
        'loading="lazy" referrerpolicy="no-referrer"><figcaption>Artwork from the owning plugin</figcaption></figure>'
        for art in project.get("artwork", [])
    )
    return f'<div class="hz-owner-art">{images}</div>' if images else ""


def home_gallery(data: Object, page_url: str) -> str:
    """Pair three real owner screenshots with imagined helpers and useful first outcomes."""
    stories = (
        {
            "plugin": "heinzel-clock",
            "scene": "world-clock",
            "art": "timekeeper",
            "name": "Pip",
            "title": "Find a moment for the whole team.",
            "quote": "I find time for everyone.",
            "link": "World Clock",
        },
        {
            "plugin": "heinzel-acme",
            "scene": "company-workflow",
            "art": "draftkeeper",
            "name": "Ada",
            "title": "See how the work connects.",
            "quote": "I turn changes into a draft.",
            "link": "Workflows",
        },
        {
            "plugin": "heinzel-acme-payments-checkout",
            "scene": "draft-in-terminal",
            "art": "reviewer",
            "name": "Otto",
            "title": "A draft you can make your own.",
            "quote": "You decide what happens next.",
            "link": "Review drafts",
        },
    )
    result = []
    for story in stories:
        project = next((item for item in data["plugins"] if item["name"] == story["plugin"]), None)
        if project is None:
            continue
        shots = project.get("screenshots", [])
        image = next((shot for shot in shots if shot["url"].endswith(f"/{story['scene']}.png")), None)
        if image is None:
            continue
        image_url = escape(image["url"])
        target = href(f"catalog/{project['id']}/", page_url)
        art = href(f"assets/website/helper-{story['art']}.svg", page_url)
        result.append(
            '<article class="hz-product-story" data-heinzel-helper>'
            '<div class="hz-story-intro">'
            f'<img src="{art}" alt="" width="140" height="230" loading="lazy">'
            f'<div><p class="hz-story-name">{escape(story["name"])}</p>'
            f'<p class="hz-story-quote">“{escape(story["quote"])}”</p>'
            f'<a href="{target}">{escape(story["link"])} →</a></div></div>'
            f'<figure><a href="{image_url}" aria-label="Open full-size {escape(story["title"])}">'
            f'<img src="{image_url}" alt="{escape(image["caption"])}" loading="lazy" referrerpolicy="no-referrer">'
            f"</a><figcaption>{escape(image['caption'])}</figcaption></figure></article>"
        )
    # Markdown's HTML parser needs block boundaries on separate lines for nested DIVs.
    return ('<div class="hz-product-stories" markdown="0">' + "".join(result) + "</div>").replace("><", ">\n<")


IMAGINED_ROLES: tuple[Object, ...] = (
    {
        "art": "timekeeper",
        "name": "Pip",
        "job": "Keeps the week in rhythm: zones, check-ins, what needs you today.",
        "link": "See World Clock",
        "target": "catalog/heinzel-clock/",
    },
    {
        "art": "draftkeeper",
        "name": "Ada",
        "job": "Drafts the weekly report from your tools. You edit and approve.",
        "link": "See the contract",
        "target": "HEINZELMAENNCHEN/",
    },
    {
        "art": "reviewer",
        "name": "Otto",
        "job": "Puts every proposed action in front of you before it runs.",
        "link": "See how approval works",
        "target": "guides/tour/",
    },
)
ROLE_WORDS = 12
ROLE_LIMIT = "HEINZELMAENNCHEN/#not-built-yet"


def imagined_roles(page_url: str) -> str:
    """Three imagined team jobs, each leaning on a released mechanism and linked to what is not built yet."""
    limit = href(ROLE_LIMIT, page_url)
    columns = []
    for role in IMAGINED_ROLES:
        art = href(f"assets/website/helper-{role['art']}.svg", page_url)
        columns.append(
            '<li class="hz-role">'
            f'<img src="{art}" alt="" width="96" height="160" loading="lazy">'
            f'<p class="hz-story-name">{escape(role["name"])}</p>'
            f'<p class="hz-role-job">{escape(role["job"])}</p>'
            f'<a href="{href(role["target"], page_url)}">{escape(role["link"])} →</a>'
            f'<a class="hz-role-limit" href="{limit}">What is not built yet</a></li>'
        )
    return (
        '<div class="hz-roles" markdown="0">'
        '<p class="hz-role-eyebrow">Imagined roles · drafts stay on your machine</p>'
        '<ul class="hz-role-list">' + "".join(columns) + "</ul></div>"
    ).replace("><", ">\n<")


def screenshot_gallery(project: Object) -> str:
    """A native swipeable UI/CLI gallery, enhanced with explicit keyboard/button navigation."""
    shots = project.get("screenshots", [])
    if not shots:
        return "## Screenshots\n\n<p>No screenshots published yet.</p>"
    identity = escape(project["id"] + "-screenshots")
    count = len(shots)
    figures = []
    for index, shot in enumerate(shots, 1):
        label = "User interface" if shot["surface"] == "ui" else "Command line"
        caption = escape(shot["caption"] or label + " screenshot")
        figures.append(
            f'<figure role="group" aria-roledescription="slide" aria-label="{index} of {count}">'
            f'<a href="{escape(shot["url"])}" aria-label="Open full-size {caption}">'
            f'<img src="{escape(shot["url"])}" alt="{caption}" '
            'loading="lazy" referrerpolicy="no-referrer"></a>'
            f'<figcaption><span class="hz-gallery-surface">{label}</span>{caption}</figcaption></figure>'
        )
    hint = (
        f'<p class="hz-gallery-hint" id="{identity}-hint">Swipe or scroll sideways to explore. '
        "Arrow keys work too; select an image to zoom and inspect it.</p>"
        if count > 1
        else f'<p class="hz-gallery-hint" id="{identity}-hint">Select the image to zoom and inspect it.</p>'
    )
    return (
        "## Screenshots\n\n"
        f'<section class="hz-gallery" data-gallery role="region" aria-roledescription="carousel" '
        f'aria-label="{escape(project["title"])} screenshots">'
        '<div class="hz-gallery-controls" data-gallery-controls hidden>'
        f'<button type="button" data-gallery-previous aria-label="Previous screenshot" aria-controls="{identity}">'
        '← Previous</button><span data-gallery-count role="status" aria-live="polite" aria-atomic="true">'
        f"1 of {count}</span>"
        f'<button type="button" data-gallery-next aria-label="Next screenshot" aria-controls="{identity}">'
        f"Next →</button></div>{hint}"
        f'<div class="hz-screenshots" id="{identity}" data-gallery-track tabindex="0" '
        f'role="group" aria-label="Screenshot slides" aria-describedby="{identity}-hint">'
        + "".join(figures)
        + "</div></section>"
    )


def soundscape_gallery(project: Object) -> str:
    """Offer each soundscape as a plain link to YouTube; nothing from YouTube loads on this site (privacy.md)."""
    cards = []
    for sound in project.get("soundscapes", []):
        title, video = escape(sound["title"]), escape(sound["video"])
        cards.append(
            f'<article class="hz-soundscape"><h3>{title}</h3>'
            f'<p><a href="https://www.youtube.com/watch?v={video}" rel="noopener noreferrer">'
            "Listen on YouTube ↗</a></p></article>"
        )
    if not cards:
        return ""
    return (
        "## Soundscapes\n\n<p>These open on YouTube in a new page when you follow the link; this site loads nothing "
        "from YouTube itself. Playback depends on video availability and the video owner.</p>"
        '<div class="hz-soundscapes">' + "".join(cards) + "</div>"
    )


def theme_gallery(data: Object, page_url: str) -> str:
    """Show every declared theme in light and dark with truthful palette-preview captions."""
    result = []
    for project in data["plugins"]:
        for index, theme in enumerate(project["themes"]):
            target = href(f"catalog/{project['id']}/", page_url)
            images = "".join(
                f'<img src="{href(f"catalog/art/{project['id']}-{index}-{mode}.svg", page_url)}" '
                f'alt="{escape(theme["title"])} {mode} palette preview" width="960" height="600" loading="lazy">'
                for mode in ("light", "dark")
            )
            result.append(
                f'<article class="hz-theme">{owner_art(project)}<div class="hz-theme-pair">{images}</div>'
                '<div class="hz-theme-caption">'
                f'<div><h3><a href="{target}">{escape(theme["title"])}</a></h3>'
                "<p>Light + dark · declared palette preview</p></div>"
                f'<a href="{target}">Explore theme ↗</a></div></article>'
            )
    return '<div class="hz-theme-gallery">' + "\n".join(result) + "</div>"


TOUR_ASSETS = ROOT / "docs" / "assets" / "tour"
TOUR_STORIES: tuple[Object, ...] = (
    {
        "id": "graph",
        "label": "workflow graph",
        "art": "draftkeeper",
        "name": "Ada",
        "cue": "Let me show you how these pieces connect.",
        "link": "Read about contracts",
        "target": "HEINZELMAENNCHEN/",
        "images": (
            (
                "graph-overview",
                "Overview",
                "A real workflow graph, populated from fictional local sources and contract declarations.",
            ),
            (
                "graph-contract-detail",
                "Zoomed detail",
                "Select the contract to see its connections and commands, here after zooming the graph.",
            ),
            ("graph-contract-detail-dark", "Dark mode", "The same contract details in dark mode."),
        ),
    },
    {
        "id": "terminal",
        "label": "terminal",
        "art": "reviewer",
        "name": "Otto",
        "cue": "Same work, another useful view.",
        "link": "Take your first steps",
        "target": "guides/first-steps/",
        "images": (
            (
                "terminal-tabs",
                "Overview",
                "Two terminal tabs in the local demo: hz graph and hz drafts, beside the contract.",
            ),
            (
                "terminal-draft-full",
                "Zoomed detail",
                "Full screen: a locally saved report from a demonstration runner, fictional data.",
            ),
            ("terminal-tabs-dark", "Dark mode", "The same two terminal tabs in dark mode."),
        ),
    },
    {
        "id": "chat",
        "label": "contextual chat",
        "art": "reviewer",
        "name": "Otto",
        "cue": "You can see exactly what I am asking to do.",
        "link": "How approval works",
        "target": "HEINZELMAENNCHEN/",
        "images": (
            (
                "chat-proposal",
                "Overview",
                "Real contextual chat and approval flow, with a scripted local demonstration runner; no model call.",
            ),
            (
                "chat-proposal-detail",
                "Zoomed detail",
                "The proposal shows the exact command and arguments. It runs only if you confirm.",
            ),
            ("chat-proposal-detail-dark", "Dark mode", "The same proposal in dark mode."),
        ),
    },
)


TOUR_FALLBACK = re.compile(r"<!-- hz:tour-story (\w+) -->.*?<!-- /hz:tour-story -->", re.DOTALL)


def tour_markup(identity: str, page_url: str) -> str:
    """The rendered story for a marker pair; the Markdown between the markers is the GitHub fallback.

    Raises:
        ValueError: If the marker names no tour story.
    """
    story = next((item for item in TOUR_STORIES if item["id"] == identity), None)
    if story is None:
        msg = f"Unknown tour story: {identity}"
        raise ValueError(msg)
    return tour_story(story, page_url).replace("><", ">\n<")


def png_size(path: Path) -> tuple[int, int]:
    """Read a PNG's pixel size from its header, so the page reserves space before the image loads.

    Raises:
        ValueError: If the file is not a PNG.
    """
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        msg = f"{path.name} is not a PNG"
        raise ValueError(msg)
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def tour_story(story: Object, page_url: str) -> str:
    """One guide, one cue and one swipeable lead-plus-zoom gallery of real product screens."""
    identity = f"tour-{story['id']}"
    count = len(story["images"])
    figures = []
    for index, (name, label, caption) in enumerate(story["images"], 1):
        width, height = png_size(TOUR_ASSETS / f"{name}.png")
        url = escape(href(f"assets/tour/{name}.png", page_url))
        alt = escape(caption)
        figures.append(
            f'<figure role="group" aria-roledescription="slide" aria-label="{index} of {count}">'
            f'<a href="{url}" aria-label="Open full-size: {alt}">'
            f'<img src="{url}" alt="{alt}" width="{width}" height="{height}" loading="lazy" '
            'referrerpolicy="no-referrer"></a>'
            f'<figcaption><span class="hz-gallery-surface">{escape(label)}</span>{alt}</figcaption></figure>'
        )
    art = href(f"assets/website/helper-{story['art']}.svg", page_url)
    return (
        f'<article class="hz-tour-story" id="{identity}" data-heinzel-helper>'
        '<div class="hz-story-intro">'
        f'<img src="{art}" alt="" width="140" height="230" loading="lazy">'
        f'<div><p class="hz-story-name">{escape(story["name"])}</p>'
        f'<p class="hz-story-quote">“{escape(story["cue"])}”</p>'
        f'<a href="{href(story["target"], page_url)}">{escape(story["link"])} →</a></div></div>'
        f'<section class="hz-gallery" data-gallery role="region" aria-roledescription="carousel" '
        f'aria-label="{escape(story["label"].capitalize())} screenshots">'
        '<div class="hz-gallery-controls" data-gallery-controls hidden>'
        f'<button type="button" data-gallery-previous aria-label="Previous {escape(story["label"])} screenshot" '
        f'aria-controls="{identity}-slides">← Previous</button>'
        '<span data-gallery-count role="status" aria-live="polite" aria-atomic="true">'
        f"1 of {count}</span>"
        f'<button type="button" data-gallery-next aria-label="Next {escape(story["label"])} screenshot" '
        f'aria-controls="{identity}-slides">Next →</button></div>'
        f'<p class="hz-gallery-hint" id="{identity}-hint">'
        "Swipe or scroll sideways. Select an image to zoom and inspect it.</p>"
        f'<div class="hz-screenshots" id="{identity}-slides" data-gallery-track tabindex="0" role="group" '
        f'aria-label="{escape(story["label"].capitalize())} slides" aria-describedby="{identity}-hint">'
        + "".join(figures)
        + "</div></section></article>"
    )


FIRST_PARTY_OWNER = tomllib.loads((ROOT / "docs" / "catalog-sources.toml").read_text())["composition_repository"].split(
    "/"
)[0]
"""Plugins owned by the maintainers resolve by short name through `hz system plugin add`."""


def install_steps(project: Object, plugins: list[Object]) -> tuple[str, str] | None:
    """The preview and install commands a person types, or none when the standard app already includes it.

    A maintainer-owned plugin with a unique short name uses that name, which `hz` resolves through its own catalog and
    pins to an immutable revision when it installs. Anything else uses the plain repository URL: the commit stays in
    the tool and in catalog.json, never in the command shown to people.
    """
    if project["included_version"]:
        return None
    short = project["name"].removeprefix("heinzel-")
    source = project["source"]
    owner = source["repository"].split("/")[0]
    taken = sum(1 for p in plugins if p["name"].removeprefix("heinzel-") == short)
    if owner == FIRST_PARTY_OWNER and taken <= 1:
        target = short
    else:
        target = f"https://github.com/{source['repository']}"
        if project["directory"] != ".":
            target = f"git+{target}#subdirectory={project['directory']}"
    target = shlex.quote(target)
    return f"hz system plugin add {target}", f"hz system plugin add {target} --confirm"


def install_section(project: Object, plugins: list[Object]) -> str:
    """The Install and try body: included plugins need nothing, others show a preview and then the install."""
    steps = install_steps(project, plugins)
    if steps is None:
        return (
            "Included in the standard app: there is nothing to install. "
            "Run `hz system plugin list` to see it, or [install Heinzel first](../../guides/install.md)."
        )
    preview, install = steps
    return f"""{command(preview, "plugin-preview")}

This shows what would be installed and changes nothing. Review it, then install:

{command(install, "plugin-install")}

Run `hz system doctor` afterward. [Install Heinzel first](../../guides/install.md)."""


def detail(project: Object, plugins: list[Object] | None = None) -> str:
    """A searchable static detail page with installation instructions and source provenance."""
    source = project["source"]
    title = escape(project["title"])
    included = project["included_version"]
    status = (
        f"Included in the standard app as version {escape(included)}."
        if included
        else "Optional plugin. Preview its source before installation."
    )
    highlights = "".join(f"<li>{escape(item)}</li>" for item in project["highlights"])
    themes = "".join(
        f'<h3>{escape(t["title"])}</h3><div class="hz-theme-pair">'
        f'<img src="../art/{project["id"]}-{i}-light.svg" alt="Light palette preview" width="960" height="600">'
        f'<img src="../art/{project["id"]}-{i}-dark.svg" alt="Dark palette preview" width="960" height="600"></div>'
        for i, t in enumerate(project["themes"])
    )
    return f"""---
title: {json.dumps(escape(project["title"]), ensure_ascii=False)}
description: {json.dumps(escape(project["summary"]), ensure_ascii=False)}
---
<div class="hz-detail-lead">
<p class="hz-eyebrow">{escape(project["category"])} / plugin</p>
<h1>{title}</h1>
<p class="hz-lead">{escape(project["summary"])}</p>
<p>{status}</p>
</div>

{screenshot_gallery(project)}

{owner_art(project)}

{soundscape_gallery(project)}

## What it brings

<ul class="hz-highlights">{highlights or "<li>See the owning README for its declared operations and setup.</li>"}</ul>

{themes}

## Install and try

{install_section(project, plugins or [project])}

## Compatibility and ownership

<table><thead><tr><th>Property</th><th>Declared metadata</th></tr></thead><tbody>
<tr><td>Distribution</td><td><code>{escape(project["name"])}</code></td></tr>
<tr><td>Source version</td><td>{escape(project["version"])}</td></tr>
<tr><td>Core requirement</td><td><code>{escape(project["core"])}</code></td></tr>
<tr><td>Provides</td><td>{escape(", ".join(project["provides"]) or "See owning documentation")}</td></tr>
<tr><td>Requires</td><td>{escape(", ".join(project["requires"]) or "Public SDK")}</td></tr>
</tbody></table>

Compatibility is declared by the owner; catalog inclusion is not a certification. Runtime behavior and permissions
are documented and tested in the owning repository. Theme illustrations show declared palettes,
not a runtime screenshot.

<div class="hz-source">
<strong>Confirmed public during refresh.</strong>
<br>
<a href="{escape(project["url"])}">Read the source and README ↗</a> ·
<a href="https://github.com/{source["repository"]}/commit/{source["revision"]}">Revision {source["revision"][:10]} ↗</a>
</div>

[← Explore the catalog](../../CATALOG.md) · [Build a plugin](../../PLUGIN_AUTHORING.md)
"""


LATEST_RELEASE_API = "https://api.github.com/repos/helmut-hoffer-von-ankershoffen/heinzel/releases/latest"
RELEASE_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def latest_release_tag() -> str | None:
    """The latest published release tag from the GitHub API, or None: a failed lookup must not fail or age the build."""
    request = urllib.request.Request(LATEST_RELEASE_API, headers={"Accept": "application/vnd.github+json"})
    if token := os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - fixed https URL
            tag = str(json.load(response).get("tag_name", ""))
    except (OSError, ValueError):
        return None
    return tag if RELEASE_TAG.fullmatch(tag) else None


def on_files(files: Any, config: Any) -> Any:  # noqa: ANN401 - MkDocs owns the event objects
    """Generate public catalog pages and inert palette artwork from an explicit snapshot."""
    from mkdocs.structure.files import File  # noqa: PLC0415 - docs-only dependency; unit rendering needs no MkDocs

    snapshot = load_snapshot(Path(os.environ.get("HZ_SITE_CATALOG", str(SNAPSHOT))))
    for project in snapshot["plugins"]:
        files.append(
            File.generated(config, f"catalog/{project['id']}/index.md", content=detail(project, snapshot["plugins"]))
        )
        for index, theme in enumerate(project["themes"]):
            for mode in ("light", "dark"):
                files.append(
                    File.generated(
                        config, f"catalog/art/{project['id']}-{index}-{mode}.svg", content=theme_art(theme, mode)
                    )
                )
    files.append(File.generated(config, "catalog.json", content=json.dumps(snapshot, ensure_ascii=False)))
    config.extra["hz_release"] = snapshot["release"]
    config.extra["hz_latest_release"] = latest_release_tag()
    config.extra["hz_plugin_pages"] = {f"catalog/{item['id']}/index.md": item for item in snapshot["plugins"]}
    return files


def helper(  # noqa: PLR0913,PLR0917 - one flat explicit record per character cue
    art: str,
    name: str,
    quote: str,
    link: str,
    action: str,
    page_url: str,
    *,
    section: str | None = None,
    lane: str | None = None,
) -> Object:
    """One character cue: who speaks, the cue, one link, the section it sits beside and its desktop lane."""
    return {
        "art": art,
        "name": name,
        "quote": quote,
        "link": link,
        "action": action,
        "src": href(f"assets/website/helper-{art}.svg", page_url),
        "section": section,
        "lane": lane,
    }


def plugin_helpers(project: Object, page_url: str) -> list[Object]:
    """Pip (Ada for themes) at the screenshots, Otto at the install, Ada at sound only when the plugin has any."""
    feature = project["highlights"][0] if project.get("highlights") else project["summary"]
    words = str(feature).split()
    brief = " ".join(words[:HELPER_WORDS])
    if len(brief) > HELPER_CHARACTERS:
        brief = brief[: HELPER_CHARACTERS - 1].rstrip() + "…"
    elif len(words) > HELPER_WORDS:
        brief += "…"
    if BANNED_CUE.search(brief):
        brief = HELPER_FALLBACK
    themed = bool(project.get("themes"))
    shown = bool(project.get("screenshots"))
    guides = [
        helper(
            "draftkeeper" if themed else "timekeeper",
            "Ada" if themed else "Pip",
            brief,
            "#screenshots" if shown else "#install-and-try",
            "See it working" if shown else "Try it",
            page_url,
            section="screenshots" if shown else "what-it-brings",
            lane="left",
        )
    ]
    if project.get("soundscapes"):
        guides.append(
            helper(
                "draftkeeper",
                "Ada",
                "Sound starts only when you choose.",
                "#soundscapes",
                "Sound options",
                page_url,
                section="soundscapes",
            )
        )
    guides.append(
        helper(
            "reviewer",
            "Otto",
            "Check source and fit before inviting it in.",
            "#install-and-try",
            "Install and try",
            page_url,
            section="install-and-try",
            lane="right",
        )
    )
    return guides


def page_helpers(path: str, page_url: str, project: Object | None = None) -> list[Object]:
    """Give each page one to five brief contextual voices, each with a useful next step."""
    if project is not None:
        return plugin_helpers(project, page_url)
    if path == "README.md":
        return [
            helper(
                "timekeeper",
                "Pip",
                "Your first useful screen is one command away.",
                href("guides/install/", page_url),
                "Install Heinzel",
                page_url,
                section="a-small-core-a-growing-world",
                lane="left",
            ),
            helper(
                "reviewer",
                "Otto",
                "Build a helper. Keep its work inspectable.",
                href("PLUGIN_AUTHORING/", page_url),
                "Build a plugin",
                page_url,
                section="your-first-contribution-can-be-a-plugin",
                lane="right",
            ),
        ]
    if path.startswith(("guides/install", "guides/first-steps")):
        return [
            helper(
                "timekeeper",
                "Pip",
                "Let's find your first useful screen together.",
                href("guides/first-steps/", page_url),
                "First steps",
                page_url,
                lane="any",
            )
        ]
    if path in {"CLI_REFERENCE.md", "CONFIGURATION.md", "MODULES.md"}:
        return [
            helper(
                "reviewer",
                "Otto",
                "Find the command for your next step.",
                href("CLI_REFERENCE/", page_url),
                "Command reference",
                page_url,
                lane="any",
            )
        ]
    if "ARCHITECTURE" in path or "architecture" in path or path.startswith("adr/"):
        quote, link, action = "Small pieces can do useful work together.", href("PLUGINS/", page_url), "Explore plugins"
    else:
        quote, link, action = (
            "Give your helpers a clear, useful job.",
            href("WORKFLOWS/", page_url),
            "Explore contracts",
        )
    return [helper("draftkeeper", "Ada", quote, link, action, page_url, lane="any")]


def waypoint(guide: Object, *, standalone: bool = False) -> str:
    """A character beside the section it explains. Server-rendered, so it works without JavaScript."""
    section = f' data-section="{escape(guide["section"])}"' if guide.get("section") else ""
    lane = f' data-lane="{escape(guide["lane"])}"' if guide.get("lane") else ""
    raw = "" if standalone else ' markdown="0"'
    return "\n".join(
        (
            (
                f'<aside class="hz-page-helper" data-heinzel-helper data-search-exclude{section}{lane} '
                f'aria-label="{escape(guide["name"])}: {escape(guide["action"])}"{raw}>'
            ),
            f'<p class="hz-helper-name">{escape(guide["name"])}</p>',
            f'<img src="{escape(guide["src"])}" alt="" width="140" height="232" loading="lazy">',
            '<div class="hz-helper-body">',
            f'<p class="hz-helper-quote">“{escape(guide["quote"])}”</p>',
            f'<a href="{escape(guide["link"])}">{escape(guide["action"])} →</a>',
            "</div>",
            "</aside>",
        )
    )


def slug(heading: str) -> str:
    """The anchor MkDocs' table-of-contents extension gives a heading (ASCII words joined by hyphens)."""
    heading = re.sub(r"<[^>]+>", "", heading).rstrip()
    if heading.endswith("}") and "{" in heading:  # an attr_list suffix such as {#id}
        heading = heading[: heading.rindex("{")]
    ascii_text = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[-\s]+", "-", re.sub(r"[^\w\s-]", "", ascii_text).strip().lower())


def headings_of(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line, level, anchor) of every ATX heading outside fenced code."""
    found: list[tuple[int, int, str]] = []
    fence = ""
    for index, line in enumerate(lines):
        marker = line.lstrip()[:3]
        level = len(line) - len(line.lstrip("#"))
        if marker in {"```", "~~~"}:
            fence = "" if fence == marker else fence or marker
        elif not fence and 1 <= level <= HEADING_LEVELS and line[level : level + 1] in {" ", "\t"}:
            found.append((index, level, slug(line[level:].strip().rstrip("#").strip())))
    return found


def place_waypoints(markdown: str, guides: list[Object]) -> str:
    """Put each waypoint after its section heading, else the page's first H2, else at the end of the page."""
    lines = markdown.split("\n")
    headings = headings_of(lines)
    inserts: dict[int, list[str]] = {}
    tail = []
    for guide in guides:
        wanted = guide.get("section")
        target = next((i for i, level, name in headings if (name == wanted if wanted else level == 2)), None)  # noqa: PLR2004
        if target is None:
            tail.append(waypoint(guide))
        else:
            inserts.setdefault(target, []).append(waypoint(guide))
    for index in sorted(inserts, reverse=True):
        lines[index + 1 : index + 1] = ["", *inserts[index], ""]
    return "\n".join(lines) + "".join(f"\n\n{html_block}\n" for html_block in tail)


def on_post_page(output: str, **_: Any) -> str:  # noqa: ANN401 - MkDocs owns the event objects
    """Drop the marker that kept Markdown out of a waypoint where no Markdown processor consumed it."""
    return output.replace(' markdown="0"', "")


def on_env(env: Any, **_: Any) -> Any:  # noqa: ANN401 - MkDocs owns the Jinja environment
    """Let templates such as the 404 page render the same waypoint markup."""
    from markupsafe import Markup  # noqa: PLC0415 - Jinja is installed only in the docs environment

    env.globals["hz_waypoints"] = lambda guides: Markup("\n".join(waypoint(guide, standalone=True) for guide in guides))  # noqa: S704 - escaped in waypoint()
    return env


DESCRIPTION_LIMIT = 155
MARKDOWN_SYNTAX = re.compile(r"!?\[([^\]]*)\]\([^)]*\)|[`*_]|<[^>]+>")


def derive_description(markdown: str) -> str:
    """The first plain paragraph as a search-result summary, or nothing when the page opens with something else."""
    for raw in re.split(r"\n\s*\n", markdown):
        block = raw.strip()
        if not block or block.startswith(("#", "<", "|", "```", "-", "*", ">", "!", "[")) or block[0].isdigit():
            continue
        plain = " ".join(MARKDOWN_SYNTAX.sub(lambda found: found.group(1) or "", block).split())
        if len(plain) < 40:  # noqa: PLR2004 - too short to describe a page
            continue
        if len(plain) <= DESCRIPTION_LIMIT:
            return plain
        return plain[:DESCRIPTION_LIMIT].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return ""


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_: Any) -> str:  # noqa: ANN401 - MkDocs owns event objects
    """Fill presentation markers and place the page's characters, keeping the authored Markdown useful on GitHub."""
    path = page.file.src_uri
    heading = re.search(r"^# (.+)$", markdown, re.MULTILINE)
    if "title" not in page.meta and heading and page.title != heading.group(1):
        # A generated notice above the heading hides it from MkDocs, which would call the page "Index".
        page.title = heading.group(1).strip()
    if not page.meta.get("description") and (derived := derive_description(markdown)):
        page.meta["description"] = derived
    if path.startswith("plugins/"):
        page.meta["search"] = {"exclude": True}
    markdown = place_waypoints(
        markdown, page_helpers(path, page.url, config.extra.get("hz_plugin_pages", {}).get(path))
    )
    if "<!-- hz:" not in markdown:
        return markdown
    markdown = TOUR_FALLBACK.sub(lambda found: tour_markup(found.group(1), page.url), markdown)
    snapshot = load_snapshot(Path(os.environ.get("HZ_SITE_CATALOG", str(SNAPSHOT))))
    renderers = {
        "catalog": lambda: cards(snapshot, page.url),
        "featured": lambda: cards(snapshot, page.url, featured=True),
        "home-gallery": lambda: home_gallery(snapshot, page.url),
        "imagined-roles": lambda: imagined_roles(page.url),
        "themes": lambda: theme_gallery(snapshot, page.url),
        "install": lambda: command(snapshot["release"]["install"], "app-install"),
        "install-script": lambda: command(snapshot["release"]["install_script"], "app-install-script"),
        "freshness": lambda: (
            '<p class="hz-freshness">Public metadata snapshot: '
            f"<time>{escape(snapshot['refreshed_at'][:16].replace('T', ' '))} UTC</time>. "
            "Refreshed hourly when GitHub Actions is available. "
            f'<a href="{href("catalog.json", page.url)}">Source data ↗</a></p>'
            + (
                '<p class="hz-freshness">Some topic sources could not be indexed at this refresh.</p>'
                if snapshot.get("notices")
                else ""
            )
        ),
    }
    for name, render in renderers.items():
        token = f"<!-- hz:{name} -->"
        if token in markdown:
            markdown = markdown.replace(token, render())
    return markdown
