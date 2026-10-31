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
import shlex
from pathlib import Path
from typing import Any

from site_catalog import (
    CATEGORIES,
    COLOR,
    MAX_PROJECTS,
    NAME,
    REPOSITORY,
    REVISION,
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
HELPER_CHARACTERS = 80


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
        "Arrow keys work too; select an image to open it full size.</p>"
        if count > 1
        else f'<p class="hz-gallery-hint" id="{identity}-hint">Select the image to open it full size.</p>'
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
    """Offer deliberate YouTube playback and a no-JavaScript watch link."""
    cards = []
    for sound in project.get("soundscapes", []):
        title, video = escape(sound["title"]), escape(sound["video"])
        cards.append(
            f'<article class="hz-soundscape"><h3>{title}</h3>'
            f'<div data-soundscape="{video}" data-title="{title}">'
            f'<button type="button" data-play-soundscape hidden>Play {title}</button></div>'
            f'<p><a href="https://www.youtube.com/watch?v={video}">Listen on YouTube ↗</a></p></article>'
        )
    if not cards:
        return ""
    return (
        "## Soundscapes\n\n<p>YouTube loads only when you press Play. "
        "Playback may depend on video availability and embedding permissions.</p>"
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


def detail(project: Object) -> str:
    """A searchable static detail page with immutable installation and source provenance."""
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

{command(project["install"], "plugin-install")}

This command previews the immutable source. Review it, then repeat with `--confirm` to install.
Run `hz system doctor` afterward. [Install Heinzel first](../../guides/install.md).

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


def on_files(files: Any, config: Any) -> Any:  # noqa: ANN401 - MkDocs owns the event objects
    """Generate public catalog pages and inert palette artwork from an explicit snapshot."""
    from mkdocs.structure.files import File  # noqa: PLC0415 - docs-only dependency; unit rendering needs no MkDocs

    snapshot = load_snapshot(Path(os.environ.get("HZ_SITE_CATALOG", str(SNAPSHOT))))
    for project in snapshot["plugins"]:
        files.append(File.generated(config, f"catalog/{project['id']}/index.md", content=detail(project)))
        for index, theme in enumerate(project["themes"]):
            for mode in ("light", "dark"):
                files.append(
                    File.generated(
                        config, f"catalog/art/{project['id']}-{index}-{mode}.svg", content=theme_art(theme, mode)
                    )
                )
    files.append(File.generated(config, "catalog.json", content=json.dumps(snapshot, ensure_ascii=False)))
    config.extra["hz_release"] = snapshot["release"]
    config.extra["hz_plugin_pages"] = {f"catalog/{item['id']}/index.md": item for item in snapshot["plugins"]}
    return files


def page_helpers(path: str, page_url: str, project: Object | None = None) -> list[Object]:
    """Give each page a brief contextual voice and a useful next step."""
    reviewer = {
        "art": "reviewer",
        "name": "Otto",
        "quote": "Look around before inviting a helper in.",
        "link": href("CATALOG/", page_url),
        "action": "Browse plugins",
    }
    if project is not None:
        feature = project["highlights"][0] if project.get("highlights") else project["summary"]
        words = str(feature).split()
        brief = " ".join(words[:HELPER_WORDS])
        if len(brief) > HELPER_CHARACTERS:
            brief = brief[: HELPER_CHARACTERS - 1].rstrip() + "…"
        elif len(words) > HELPER_WORDS:
            brief += "…"
        guide = {
            "art": "draftkeeper" if project.get("themes") else "timekeeper",
            "name": "Ada" if project.get("themes") else "Pip",
            "quote": brief,
            "link": "#screenshots",
            "action": "See it working",
        }
        return [guide, reviewer]
    guide = {
        "art": "draftkeeper",
        "name": "Ada",
        "quote": "Give your helpers a clear, useful job.",
        "link": href("WORKFLOWS/", page_url),
        "action": "Explore contracts",
    }
    if path.startswith(("guides/install", "guides/first-steps")):
        guide.update(
            art="timekeeper",
            name="Pip",
            quote="Let's find your first useful screen together.",
            link=href("guides/first-steps/", page_url),
            action="First steps",
        )
    elif "ARCHITECTURE" in path or "architecture" in path or path.startswith("adr/"):
        guide.update(
            quote="Small pieces can do useful work together.", link=href("PLUGINS/", page_url), action="Explore plugins"
        )
    elif path in {"CLI_REFERENCE.md", "CONFIGURATION.md", "MODULES.md"}:
        guide.update(
            art="reviewer",
            name="Otto",
            quote="Find the command for your next step.",
            link=href("CLI_REFERENCE/", page_url),
            action="Command reference",
        )
    return [guide]


def on_page_context(
    context: Object,
    *,
    page: Any,  # noqa: ANN401 - MkDocs owns event objects
    config: Any,  # noqa: ANN401 - MkDocs owns event objects
    **_: Any,  # noqa: ANN401 - MkDocs owns event objects
) -> Object:
    """Attach explainers in the shared layout, including automatically discovered future plugin pages."""
    path = page.file.src_uri
    project = config.extra.get("hz_plugin_pages", {}).get(path)
    context["hz_helpers"] = page_helpers(path, page.url, project)
    return context


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_: Any) -> str:  # noqa: ANN401 - MkDocs owns event objects
    """Fill presentation markers while keeping the authored Markdown useful on GitHub."""
    del config
    if page.file.src_uri.startswith("plugins/"):
        page.meta["search"] = {"exclude": True}
    if "<!-- hz:" not in markdown:
        return markdown
    snapshot = load_snapshot(Path(os.environ.get("HZ_SITE_CATALOG", str(SNAPSHOT))))
    renderers = {
        "catalog": lambda: cards(snapshot, page.url),
        "featured": lambda: cards(snapshot, page.url, featured=True),
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
