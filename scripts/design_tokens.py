"""One token file for the app's default theme and the GitHub Pages site: generate the site's CSS, check for drift.

`design/tokens.json` is the single source. This script

- writes `docs/assets/website/tokens.css` from it (write mode), and
- fails (`--check`) when the token file, web/src/index.css, the kernel's DEFAULT_THEME, the avatar palette in
  web/src/components/heinzel.ts, the generated CSS, the site's own stylesheets or the README badges disagree.

Usage: uv run python scripts/design_tokens.py [--check]
"""

# Project-bound: runs in the project environment (`uv run python`) because it imports the kernel's default theme.

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "design" / "tokens.json"
INDEX_CSS = ROOT / "web" / "src" / "index.css"
AVATAR_TS = ROOT / "web" / "src" / "components" / "heinzel.ts"
WEBSITE = ROOT / "docs" / "assets" / "website"
TOKENS_CSS = WEBSITE / "tokens.css"
SITE_STYLES = (WEBSITE / "site.css", WEBSITE / "trust.css")
GENERATE_DOCS = ROOT / "scripts" / "generate_docs.py"
INDEX_HTML = ROOT / "web" / "index.html"
MANIFEST = ROOT / "web" / "public" / "manifest.webmanifest"

HEADER = "/* generated from design/tokens.json by scripts/design_tokens.py; do not edit */\n"
DARK_SELECTOR = '[data-md-color-scheme="slate"]'
"""Material's dark scheme: the attribute sits on <body>, so the dark values override from there down."""
BRIDGE = {
    "--md-default-bg-color": "var(--color-background)",
    "--md-default-fg-color": "var(--color-foreground)",
    "--md-default-fg-color--light": "var(--color-muted-foreground)",
    "--md-primary-fg-color": "var(--color-primary)",
    "--md-accent-fg-color": "var(--color-primary)",
    "--md-code-bg-color": "var(--color-card)",
    "--md-code-fg-color": "var(--color-foreground)",
    "--md-typeset-a-color": "var(--color-primary)",
}
"""Material's variables, pointed at the tokens. Material's own palette rules would otherwise win in dark mode."""
SITE_VARIABLES = {
    "--radius-lg": "radius-lg",
    "--radius-xl": "radius-xl",
    "--page-max": "page-max",
    "--home-max": "home-max",
    "--target": "target",
}
"""CSS variable on the site to the token (radius, spacing groups) that sets it."""
BLACK_ALPHA = re.compile(r"^#0{3}[0-9a-f]$|^#0{6}[0-9a-f]{2}$", re.IGNORECASE)
"""The one literal colour the site's stylesheets may keep: black with an alpha, as a shadow or a backdrop."""
LITERAL_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", re.IGNORECASE)
BLACK_RGB = re.compile(r"rgba?\(\s*0[\s,]+0[\s,]+0\s*[,/]", re.IGNORECASE)
BADGE_TOKEN = "badge-green"  # noqa: S105 - a token name, not a secret


def load() -> dict:
    """The parsed token file."""
    return json.loads(TOKENS.read_text(encoding="utf-8"))


def colours(data: dict) -> dict[str, tuple[str, str]]:
    """Each colour token's (light, dark) value; a token with one value has it in both modes."""
    result: dict[str, tuple[str, str]] = {}
    for token in data["color"]["tokens"]:
        value = token["value"]
        result[token["name"]] = (value["light"], value["dark"]) if isinstance(value, dict) else (value, value)
    return result


def app_colours(data: dict) -> dict[str, tuple[str, str]]:
    """The `color-*` tokens: the ones the app's CSS and the site declare as custom properties."""
    return {name: pair for name, pair in colours(data).items() if name.startswith("color-")}


def plain(data: dict, group: str) -> dict[str, str]:
    """A flat group of tokens (spacing, radius) as name to value."""
    return {token["name"]: token["value"] for token in data[group]["tokens"]}


def styles(data: dict) -> dict[str, dict]:
    """Every type style by name."""
    return {style["name"]: style for group in data["type"]["groups"] for style in group["styles"]}


def pixels(value: str) -> str:
    """A rem length as px at the browser's 16px root: Material sets the site's root to 20px, so rem would grow."""
    if value.endswith("rem"):
        return f"{round(float(value.removesuffix('rem')) * 16, 2):g}px"
    return value


def render_css(data: dict) -> str:
    """The Pages site's token stylesheet: light values on :root, dark ones under Material's slate scheme."""
    palette = app_colours(data)
    spacing = plain(data, "spacing")
    radius = plain(data, "radius")
    families = data["type"]["families"]
    shared = {"--font-sans": families["sans"], "--font-mono": families["mono"]}
    for variable, name in SITE_VARIABLES.items():
        shared[variable] = pixels(radius.get(name) or spacing[name])
    lines = [HEADER, ":root {"]
    lines += [f"  --{name}: {light};" for name, (light, _dark) in palette.items()]
    lines += [f"  {variable}: {value};" for variable, value in shared.items()]
    lines += ["}", f"{DARK_SELECTOR} {{"]
    lines += [f"  --{name}: {dark};" for name, (light, dark) in palette.items() if dark != light]
    lines += ["}", ":root,", "[data-md-color-scheme] {"]
    lines += [f"  {variable}: {value};" for variable, value in BRIDGE.items()]
    lines += ["}", ""]
    return "\n".join(lines)


def strip_comments(css: str) -> str:
    """CSS without its comments."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def block(css: str, selector: str) -> str:
    """The body of the first `selector { ... }` rule, braces balanced; empty if there is none."""
    start = css.find(selector + " {")
    if start < 0:
        return ""
    depth, begin = 0, css.index("{", start)
    for index in range(begin, len(css)):
        depth += {"{": 1, "}": -1}.get(css[index], 0)
        if depth == 0:
            return css[begin + 1 : index]
    return ""


def declarations(body: str) -> dict[str, str]:
    """`--name: value;` pairs of a rule body."""
    return {name: " ".join(value.split()) for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)}


def normal(value: str) -> str:
    """A colour in a comparable form: lower case, and any fully transparent colour is `transparent`."""
    value = value.strip().lower()
    if value == "transparent" or re.fullmatch(r"#[0-9a-f]{6}00", value) or re.fullmatch(r"#[0-9a-f]{3}0", value):
        return "transparent"
    return value


def check_index_css(data: dict) -> list[str]:
    """web/src/index.css: `@theme` is the light palette, `.dark` the dark one, plus corners, sizes and fonts."""
    css = strip_comments(INDEX_CSS.read_text(encoding="utf-8"))
    light, dark = declarations(block(css, "@theme")), declarations(block(css, ".dark"))
    problems = []
    for name, (want_light, want_dark) in app_colours(data).items():
        variable = f"--{name}"
        if normal(light.get(variable, "")) != normal(want_light):
            problems.append(f"index.css {variable}: {light.get(variable)!r}, tokens say {want_light!r}")
        if variable in dark and normal(dark[variable]) != normal(want_dark):
            problems.append(f"index.css .dark {variable}: {dark[variable]!r}, tokens say {want_dark!r}")
        if variable not in dark and want_dark != want_light and name != "color-main-tint":
            problems.append(f"index.css .dark has no {variable}, tokens say {want_dark!r}")
        initial = re.search(rf"@property {re.escape(variable)} \{{[^}}]*initial-value:\s*([^;]+);", css)
        if initial and normal(initial.group(1)) != normal(want_light):
            problems.append(
                f"index.css @property {variable} initial-value {initial.group(1)!r}, tokens say {want_light!r}"
            )
    problems += check_scalars(data, light, "index.css")
    return problems


def scalars(data: dict) -> dict[str, str]:
    """Radii, type sizes, density and fonts that the app declares as CSS custom properties."""
    spacing, radius, type_styles = plain(data, "spacing"), plain(data, "radius"), styles(data)
    return {
        "--radius-lg": radius["radius-lg"],
        "--radius-xl": radius["radius-xl"],
        "--text-ui": type_styles["ui"]["fontSize"],
        "--text-ui-sm": type_styles["ui-sm"]["fontSize"],
        "--icon": spacing["icon"],
        "--row": spacing["row"],
        "--font-sans": data["type"]["families"]["sans"],
        "--font-mono": data["type"]["families"]["mono"],
    }


def check_scalars(data: dict, declared: dict[str, str], where: str) -> list[str]:
    """Each scalar token against the declared custom properties."""
    return [
        f"{where} {variable}: {declared.get(variable)!r}, tokens say {want!r}"
        for variable, want in scalars(data).items()
        if declared.get(variable) != want
    ]


def check_default_theme(data: dict) -> list[str]:
    """The kernel's DEFAULT_THEME: every colour, both radii, sizes, density and fonts."""
    sys.path.insert(0, str(ROOT / "src"))
    from heinzel.kernel._themes import DEFAULT_THEME  # noqa: PLC0415 - project import, only when checking

    problems = []
    palette = colours(data)
    for mode, theme_colours in (("light", DEFAULT_THEME.light), ("dark", DEFAULT_THEME.dark)):
        for field, value in theme_colours.model_dump().items():
            name = "color-" + field.replace("_", "-")
            if value is None:
                continue
            if name not in palette:
                problems.append(f"DEFAULT_THEME.{mode}.{field} has no token {name}")
                continue
            want = palette[name][0 if mode == "light" else 1]
            if normal(value) != normal(want):
                problems.append(f"DEFAULT_THEME.{mode}.{field}: {value!r}, tokens say {want!r}")
    theme = {
        "--radius-xl": DEFAULT_THEME.radius,
        "--radius-lg": DEFAULT_THEME.radius_control,
        "--text-ui": DEFAULT_THEME.font_size,
        "--text-ui-sm": DEFAULT_THEME.font_size_small,
        "--icon": DEFAULT_THEME.icon_size,
        "--row": DEFAULT_THEME.row_height,
        "--font-sans": DEFAULT_THEME.font_sans,
        "--font-mono": DEFAULT_THEME.font_mono,
    }
    return problems + check_scalars(data, theme, "DEFAULT_THEME")


def check_avatar(data: dict) -> list[str]:
    """The palette constants in heinzel.ts against the hat, shirt, heinzel and state tokens."""
    source = AVATAR_TS.read_text(encoding="utf-8")
    palette = colours(data)
    found: dict[str, str] = {}
    for prefix, constant in (("shirt", "SHIRTS"), ("hat", "HATS")):
        body = re.search(rf"export const {constant} = \{{(.*?)\}} as const", source, re.DOTALL)
        if not body:
            return [f"heinzel.ts has no {constant}"]
        for key, value in re.findall(r'"?([a-z-]+)"?:\s*"(#[0-9A-Fa-f]{6})"', body.group(1)):
            found[f"{prefix}-{key}"] = value
    for part, constant in (("face", "FACE"), ("ink", "INK"), ("trousers", "TROUSERS"), ("boots", "BOOTS")):
        match = re.search(rf'^const {constant} = "(#[0-9A-Fa-f]{{6}})";', source, re.MULTILINE)
        if match:
            found[f"heinzel-{part}"] = match.group(1)
    for state, value in re.findall(r'case "(\w+)":\s*return mark\("(#[0-9A-Fa-f]{6})"', source):
        found[f"state-{state}"] = value
    wanted = {name for name in palette if name.startswith(("hat-", "shirt-", "heinzel-", "state-"))}
    problems = [f"heinzel.ts has no {name}" for name in sorted(wanted - found.keys())]
    problems += [f"heinzel.ts {name} has no token" for name in sorted(found.keys() - wanted)]
    problems += [
        f"heinzel.ts {name}: {found[name]!r}, tokens say {palette[name][0]!r}"
        for name in sorted(found.keys() & wanted)
        if found[name].lower() != palette[name][0].lower()
    ]
    return problems


def check_first_paint(data: dict) -> list[str]:
    """The splash's inline colours, the browser's theme colour and the manifest follow the tokens."""
    palette = colours(data)
    page = INDEX_HTML.read_text(encoding="utf-8")
    problems = []
    for mode, selector, index in (("light", ":root {", 0), ("dark", ":root.dark {", 1)):
        body = page.split(selector, 1)[1].split("}", 1)[0] if selector in page else ""
        found = declarations(body)
        for key in ("background", "foreground", "muted-foreground", "primary"):
            want = palette[f"color-{key}"][index]
            if normal(found.get(f"--splash-{key}", "")) != normal(want):
                problems.append(
                    f"index.html splash {mode} {key}: {found.get(f'--splash-{key}')!r}, tokens say {want!r}"
                )
        meta = re.search(
            rf'name="theme-color" content="(#[0-9a-fA-F]+)" media="\(prefers-color-scheme: {mode}\)"', page
        )
        want = palette["color-background"][index]
        if not meta or meta.group(1).lower() != want:
            problems.append(f"index.html theme-color {mode}: {meta and meta.group(1)!r}, tokens say {want!r}")
        fallback = re.search(r'let background = dark \? "(#\w+)" : "(#\w+)"', page)
        if not fallback or fallback.group(1 + (mode == "light")).lower() != want:
            problems.append(f"index.html first-paint background for {mode} disagrees with {want!r}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    want = palette["color-background"][0]
    problems += [
        f"manifest {key}: {manifest.get(key)!r}, tokens say {want!r}"
        for key in ("theme_color", "background_color")
        if str(manifest.get(key, "")).lower() != want
    ]
    return problems


def check_generated(data: dict) -> list[str]:
    """tokens.css is exactly what write mode would produce."""
    current = TOKENS_CSS.read_text(encoding="utf-8") if TOKENS_CSS.exists() else ""
    if current != render_css(data):
        return [f"{TOKENS_CSS.relative_to(ROOT)} is out of date; run: uv run python scripts/design_tokens.py"]
    return []


def literal_colours(css: str) -> list[str]:
    """Colour literals in a stylesheet other than black with an alpha (a shadow, a backdrop)."""
    text = strip_comments(css)
    found = [m for m in LITERAL_COLOUR.findall(text) if m.startswith("#") and not BLACK_ALPHA.match(m)]
    found += [m for m in re.findall(r"\brgba?\([^)]*\)", text, re.IGNORECASE) if not BLACK_RGB.match(m)]
    return found


def check_site_styles() -> list[str]:
    """The site's own stylesheets take every colour from a token."""
    return [
        f"{path.name}: colour literal {literal} (use a token from tokens.css)"
        for path in SITE_STYLES
        for literal in literal_colours(path.read_text(encoding="utf-8"))
    ]


def check_badges(data: dict) -> list[str]:
    """Static always-true badges use the token green; the Python badge stays blue."""
    source = GENERATE_DOCS.read_text(encoding="utf-8")
    wanted = {t["name"]: t["value"] for t in data["color"]["tokens"]}.get(BADGE_TOKEN, "")
    problems = []
    if wanted != "#44bb00":
        problems.append(f"token {BADGE_TOKEN} is {wanted!r}, expected '#44bb00'")
    if f"license-MIT-{wanted.removeprefix('#')}" not in source:
        problems.append(f"generate_docs.py: the License badge must use {wanted.removeprefix('#')}")
    if "-blue" not in source.split("python-{versions}", 1)[-1][:12]:
        problems.append("generate_docs.py: the Python badge must stay blue")
    return problems


def problems_found() -> list[str]:
    """Every disagreement between the token file and what is meant to follow it."""
    data = load()
    return [
        *check_index_css(data),
        *check_default_theme(data),
        *check_avatar(data),
        *check_first_paint(data),
        *check_generated(data),
        *check_site_styles(),
        *check_badges(data),
    ]


def main() -> int:
    """Write tokens.css, or with `--check` report drift."""
    if "--check" in sys.argv:
        problems = problems_found()
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print("Design tokens agree: app CSS, default theme, avatar palette, first paint, site CSS and badges")
        return 1 if problems else 0
    TOKENS_CSS.write_text(render_css(load()), encoding="utf-8")
    print(f"Wrote {TOKENS_CSS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
