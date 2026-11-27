"""Serve checked theme fonts, logos and Soundscape assets within the web head."""

import hashlib
import re

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from heinzel.kernel import FONT_ROUTE, LOGO_ROUTE, SOUND_ROUTE, HeinzelError, Kernel, SoundAssets, ThemeFonts

NOT_FOUND = 404
FONT_THEME = re.compile(r"[a-z](?:-?[a-z0-9]){0,63}")
FONT_FILE = re.compile(r"([0-9]{1,2})\.woff2")
LOGO_FILE = re.compile(r"(light|dark|mark|heinzel-(?:hat-(?:young|grown|old)|outfit-(?:baby|young|grown|old)))\.svg")
SOUND_FILE = re.compile(r"(layer-[0-9]|earcon-[a-z]{1,16})\.(ogg|opus|mp3)")
IMMUTABLE = "public, max-age=31536000, immutable"


def add_fonts(app: FastAPI, kernel: Kernel) -> None:
    """Serve the themes' font files at `FONT_ROUTE`, outside `/api`, behind the app's middleware (ADR 0044)."""

    @app.get(FONT_ROUTE + "/{theme}/{file}", include_in_schema=False)
    def theme_font(theme: str, file: str) -> Response:
        # Not async: it reads files, so FastAPI runs it in its thread pool and other requests don't wait.
        return font_response(kernel, theme, file)


def font_response(kernel: Kernel, theme: str, file: str) -> Response:
    """Font `<index>.woff2` of the available theme `theme`, or 404; served without the token (ADR 0044)."""
    # Checked against fixed patterns, then looked up by theme name and index: a request never builds a path.
    index = FONT_FILE.fullmatch(file)
    content = None
    if FONT_THEME.fullmatch(theme) and index and kernel.provides(ThemeFonts):
        try:
            content = kernel.resolve(ThemeFonts).font(theme, int(index.group(1)))
        except HeinzelError:  # system.theme can't be built (a module it needs is off): no theme ships fonts
            content = None
    if content is None:
        return JSONResponse(status_code=NOT_FOUND, content={"error": "No theme ships that font."})
    # The list's URL carries the content's version (`?v=`), so a changed font is a new URL: the answer is immutable.
    return Response(content, media_type="font/woff2", headers={"Cache-Control": IMMUTABLE})


def add_logos(app: FastAPI, kernel: Kernel) -> None:
    """Serve the themes' logos at `LOGO_ROUTE`, outside `/api`, behind the app's middleware (ADR 0044)."""

    @app.get(LOGO_ROUTE + "/{theme}/{file}", include_in_schema=False)
    def theme_logo(theme: str, file: str, v: str | None = None) -> Response:
        # Not async: it reads files, so FastAPI runs it in its thread pool and other requests don't wait.
        return logo_response(kernel, theme, file, version=v)


def logo_response(kernel: Kernel, theme: str, file: str, *, version: str | None = None) -> Response:
    """An available theme's logo or content-versioned avatar layer, or 404; tokenless original artwork."""
    # Checked against fixed patterns, then looked up by theme name and logo name: a request never builds a path.
    name = LOGO_FILE.fullmatch(file)
    content = None
    if FONT_THEME.fullmatch(theme) and name and kernel.provides(ThemeFonts):
        try:
            content = kernel.resolve(ThemeFonts).logo(theme, name.group(1))
        except HeinzelError:  # system.theme can't be built (a module it needs is off): no theme ships a logo
            content = None
    if (
        content is not None
        and name
        and name.group(1).startswith("heinzel-")
        and version != hashlib.sha256(content).hexdigest()[:16]
    ):
        content = None
    if content is None:
        return JSONResponse(status_code=NOT_FOUND, content={"error": "No theme ships that logo."})
    # The list's URL carries the content's version (`?v=`), so a changed logo is a new URL: the answer is immutable.
    return Response(content, media_type="image/svg+xml", headers={"Cache-Control": IMMUTABLE})


def add_sounds(app: FastAPI, kernel: Kernel) -> None:
    """Serve the soundscapes' audio files at `SOUND_ROUTE`, outside `/api`, behind the app's middleware (ADR 0058)."""

    @app.get(SOUND_ROUTE + "/{soundscape}/{file}", include_in_schema=False)
    def soundscape_file(soundscape: str, file: str) -> Response:
        # Not async: it reads files, so FastAPI runs it in its thread pool and other requests don't wait.
        return sound_response(kernel, soundscape, file)


def sound_response(kernel: Kernel, soundscape: str, file: str) -> Response:
    """Audio file `<layer-n|earcon-kind>.<ext>` of the available `soundscape`, or 404; served without the token.

    Its type is the one its checked signature says, and the extension asked for must be the declared file's.
    """
    # Checked against fixed patterns, then looked up by name and part: a request never builds a path.
    match = SOUND_FILE.fullmatch(file)
    found = None
    if FONT_THEME.fullmatch(soundscape) and match and kernel.provides(SoundAssets):
        try:
            found = kernel.resolve(SoundAssets).asset(soundscape, match.group(1), match.group(2))
        except Exception:  # noqa: BLE001 - system.sound can't be built or read: no soundscape ships files
            found = None
    if found is None:
        return JSONResponse(status_code=NOT_FOUND, content={"error": "No soundscape ships that file."})
    content, media_type = found
    # The list's URL carries the content's version (`?v=`), so a changed file is a new URL: the answer is immutable.
    return Response(content, media_type=media_type, headers={"Cache-Control": IMMUTABLE})
