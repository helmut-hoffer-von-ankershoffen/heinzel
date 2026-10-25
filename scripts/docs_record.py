# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
"""Record the scenes of every plugin's documentation page, and the README's intro (ADR 0058).

For each page `hz system plugin docs show` describes, every scene becomes a recording beside the page, in
`docs/plugins/<distribution>/assets/`: `<scene>.gif`, `.webp` and `.mp4`, a still `<scene>.png` for people who turn
motion off, and `<scene>.md`, the transcript the page embeds. A CLI scene is a VHS tape of real `hz` commands; a UI
scene is `hz ui` driven by Playwright, one screenshot per step, in light and then in dark mode (`<scene>-dark.*`).

Everything runs in a sandbox like the README demo's: its own uv tool directory, its own `HZ_*` directories, a made-up
profile, no update check, the `environment` credential backend and no real connector, so a recording shows no
machine's settings and nothing leaves it. `hz` is installed editable from this checkout (the core), or Heinzelmännchen
as the test group pins it plus this plugin (a plugin repository, one plugin or a `plugins/*` workspace).

Tools: vhs, ttyd, ffmpeg and jq from scripts/tools.toml (group `demo`, installed on demand), Playwright's Chromium
(`playwright install chromium`, pinned by Playwright's version). Transcripts pass scripts/check_confidentiality.py.

Usage: uv run --script scripts/docs_record.py [--plugin NAME] [--scene NAME] [--directory docs/plugins] [--intro]
                                              [--intro-only] [--keep] [--budget MB]
  --intro   also render the README's intro, docs/assets/demo.{gif,webp,mp4}: the demo tape's CLI part, then the
            `[intro] scenes` of docs/scenes.toml (the core only); --intro-only renders nothing else
"""

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path("/tmp/hz-docs")  # noqa: S108 - a short, fixed path: `hz system doctor` shows it in recordings
SCENES = ROOT / "docs" / "scenes.toml"
INTRO_TAPE = ROOT / "docs" / "assets" / "demo.tape"
INTRO_OUT = ROOT / "docs" / "assets" / "demo"
TOOLS = ROOT / "scripts" / "tools.py"
CONFIDENTIAL = ROOT / "scripts" / "check_confidentiality.py"
WIDTH, HEIGHT = 1200, 720
"""Every recording's size: the demo tape's terminal, and the viewport of the web app."""
FRAMERATE = 12
GIF_FPS = 10
BUDGET_MB = 2.0
SCALES = (1.0, 0.8, 0.6)
"""Sizes tried until a recording fits the budget."""
TAPE_HEADER = """\
Set Shell bash
Set Theme "Catppuccin Mocha"
Set FontSize 16
Set Width {width}
Set Height {height}
Set Padding 20
Set Framerate {framerate}
Set TypingSpeed 35ms
Set PlaybackSpeed 1.0
Set WaitPattern /\\$\\s*$/

Hide
Type `export PS1='$ '`
Enter
Type "clear"
Enter
Show
"""
PROFILE = {
    "HZ_UPDATE_CHECK": "false",
    "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
    "HZ_ORG__HANDLE": "mira",
    "HZ_ORG__NAME": "Mira Example",
    "HZ_ORG__EMAIL": "mira@example.com",
    "HZ_ORG__ROLE": "engineering-manager",
    "HZ_ORG__TIMEZONE": "Europe/Berlin",
    "NO_COLOR": "",
    "TERM": "xterm-256color",
}
CONTRACT = """\
---
name: {name}
kind: responsibility
description: {description}
hz:
  schema: 1
  title: {title}
{body}---

### Goal

It is true.

### Requires

- `in0`: what it holds.

### Maintains

- `{output}`: it.

### Done when

- [ ] It is.
"""
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PALETTE = "split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
ENCODINGS = (
    ("gif", ["-vf", "fps={fps},scale={w}:-1:flags=lanczos," + PALETTE, "-loop", "0"]),
    (
        "webp",
        [
            "-vf",
            "fps={fps},scale={w}:-1:flags=lanczos",
            "-c:v",
            "libwebp_anim",
            "-lossless",
            "0",
            "-q:v",
            "70",
            "-loop",
            "0",
        ],
    ),
    ("mp4", ["-vf", "scale={w}:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p", "-an"]),
)
"""What a recording is published as, from its MP4: the GIF GitHub renders, a WebP a fraction of its size, the video."""
SHOWN_MB = 0.1
"""Files at least this large are reported with their size."""
TRANSCRIPT_LINES = 40
"""How many lines of a command's output the transcript keeps."""


class RecordError(Exception):
    """Something the recorder needs is missing or failed."""


@dataclass
class Scene:
    """A scene as `hz system plugin docs show` describes it."""

    name: str
    title: str
    kind: str
    commands: list[str] = field(default_factory=list)
    hold: float = 2.5
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Page:
    """A page and its scenes."""

    distribution: str
    scenes: list[Scene]


# The sandbox.


def environment(sandbox: Path) -> dict[str, str]:
    """The sandbox's environment: no `HZ_*` of this machine, its own directories, the made-up profile."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    env |= {
        "UV_TOOL_DIR": str(sandbox / "tools"),
        "UV_TOOL_BIN_DIR": str(sandbox / "bin"),
        "HZ_CONFIG_HOME": str(sandbox / "config"),
        "HZ_DATA_DIR": str(sandbox / "data"),
        "HZ_CACHE_DIR": str(sandbox / "cache"),
        "HZ_STATE_DIR": str(sandbox / "state"),
        "HZ_LOG_DIR": str(sandbox / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(sandbox / "units"),
        "HZ_WORKFLOW__USER_DIR": str(sandbox / "workflows"),
        "PATH": f"{sandbox / 'bin'}{os.pathsep}{ROOT / '.tools' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env |= PROFILE
    return env


def seed(sandbox: Path, env: dict[str, str]) -> None:
    """Two made-up contracts, one reading the other, so the workflow and team graph screens show a flow."""
    workflows = Path(env["HZ_WORKFLOW__USER_DIR"])
    workflows.mkdir(parents=True, exist_ok=True)
    digest = (
        "  requires: [tracker:read]\n"
        "  inputs:\n    in0: {read: tracker.issues_updated, args: {project: ACME}, window: 7d}\n"
        "  outputs:\n    digest: {format: markdown}\n"
    )
    newsletter = (
        '  inputs:\n    in0: {read: "contract:me/weekly-digest#digest", stage: draft}\n'
        "  outputs:\n    issue: {format: markdown}\n"
    )
    for name, title, description, body, output in (
        ("weekly-digest", "Weekly digest", "Keeps the team's week in one page.", digest, "digest"),
        ("newsletter", "Team newsletter", "Keeps stakeholders informed.", newsletter, "issue"),
    ):
        text = CONTRACT.format(name=name, title=title, description=description, body=body, output=output)
        (workflows / f"{name}.prose.md").write_text(text, encoding="utf-8")
    for key in ("config", "data", "cache", "state", "log"):
        (sandbox / key).mkdir(parents=True, exist_ok=True)
    # The core's test team (made-up people, their contracts and telemetry) gives the team graph something to show.
    team = ROOT / "tests" / "fixtures" / "graph" / "team"
    if team.is_dir():
        shutil.copytree(team, sandbox / "team", dirs_exist_ok=True)
        env["HZ_TEAM__CHECKOUT"] = str(sandbox / "team")


def plugin_directories(root: Path) -> list[Path]:
    """The plugin projects a repository holds: `plugins/*` of a workspace, else the repository itself."""
    members = sorted(p.parent for p in root.glob("plugins/*/pyproject.toml"))
    return members or [root]


def heinzel_requirement(root: Path) -> str:
    """How a plugin repository pins Heinzelmännchen: the `heinzel @ ...` line of its test group, else `heinzel`."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for group in data.get("dependency-groups", {}).values():
        for item in group:
            if isinstance(item, str) and re.match(r"^heinzel(\s*@|\s*[<>=!~]|$)", item):
                return item
    return "heinzel"


def is_core(root: Path) -> bool:
    """Whether this checkout is Heinzelmännchen itself."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data.get("project", {}).get("name")) == "heinzel"


def web_app_built(root: Path, env: dict[str, str]) -> None:
    """Build the web app the core's editable install serves, when it is missing (CI has no build yet)."""
    if is_core(root) and not (root / "src" / "heinzel" / "heads" / "web" / "static" / "index.html").is_file():
        print("building the web app", flush=True)
        run(
            ["uv", "run", "--quiet", "--script", str(root / "scripts" / "web_build.py"), "build"], env=env, timeout=1800
        )


def install(root: Path, env: dict[str, str]) -> None:
    """Install `hz` into the sandbox, editable.

    The core comes with its first-party plugins; a plugin repository gets the Heinzelmännchen its test group pins,
    with the repository's plugin or plugins.
    """
    if is_core(root):
        command = ["uv", "tool", "install", "--quiet", "--reinstall", "--editable", str(root)]
    else:
        command = ["uv", "tool", "install", "--quiet", "--reinstall", heinzel_requirement(root)]
    for directory in plugin_directories(root):
        if directory != root:
            command += ["--with-editable", str(directory)]
    run(command, env=env)


def which(program: str, env: dict[str, str]) -> str:
    """The program on the sandbox's PATH, so nothing runs from the working directory.

    Raises:
        RecordError: If it isn't there.
    """
    found = shutil.which(program, path=env["PATH"])
    if found is None:
        msg = f"{program} not found on PATH; `uv run --script scripts/tools.py doctor` says how to install it"
        raise RecordError(msg)
    return found


def run(
    command: list[str], *, env: dict[str, str], timeout: int = 900, capture: bool = False, cwd: Path | None = None
) -> str:
    """Run a command in the sandbox's environment; its stderr tail when it fails.

    Raises:
        RecordError: If the command fails.
    """
    resolved = [which(command[0], env), *command[1:]]
    result = subprocess.run(  # noqa: S603 - fixed argument lists
        resolved, env=env, check=False, capture_output=True, text=True, timeout=timeout, cwd=cwd
    )
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).splitlines()[-15:])
        msg = f"{' '.join(command[:3])} failed ({result.returncode}):\n{tail}"
        raise RecordError(msg)
    return result.stdout if capture else ""


def tools(env: dict[str, str]) -> None:
    """vhs, ttyd, ffmpeg and jq from scripts/tools.toml, and Playwright's Chromium.

    Raises:
        RecordError: If a tool is missing after the install.
    """
    run(["uv", "run", "--quiet", "--script", str(TOOLS), "install", "demo"], env=env)
    missing = [tool for tool in ("vhs", "ttyd", "ffmpeg") if shutil.which(tool, path=env["PATH"]) is None]
    if missing:
        msg = f"{', '.join(missing)} not found; `uv run --script scripts/tools.py doctor` says how to install them"
        raise RecordError(msg)
    run([sys.executable, "-m", "playwright", "install", "chromium"], env=env, timeout=1200)


# The model.


def pages(env: dict[str, str], *, plugin: str | None, scenes: Path | None, builtin: bool) -> list[Page]:
    """The pages and their scenes, from the sandbox's `hz`."""
    command = ["hz", "system", "plugin", "docs", "show", "--json"]
    if plugin:
        command += ["--plugin", plugin]
    if scenes is not None and scenes.is_file():
        command += ["--scenes", str(scenes)]
    if builtin:
        command.append("--builtin")
    data = json.loads(run(command, env=env, capture=True))
    for warning in data.get("warnings", []):
        print(f"! {warning}", file=sys.stderr)
    return [
        Page(
            distribution=page["distribution"],
            scenes=[
                Scene(
                    name=s["name"],
                    title=s["title"],
                    kind=s["kind"],
                    commands=s.get("commands", []),
                    hold=float(s.get("hold", 2.5)),
                    steps=s.get("steps", []),
                )
                for s in page["scenes"]
            ],
        )
        for page in data["pages"]
    ]


# CLI scenes.


def tape(scene: Scene, output: str) -> str:
    """The VHS tape of a CLI scene: the fixed settings, a quiet prompt, then each command, waited for and held.

    `output` is relative to where vhs runs: VHS reads an absolute path's leading slash as a command.
    """
    lines = [f"Output {output}", "", TAPE_HEADER.format(width=WIDTH, height=HEIGHT, framerate=FRAMERATE)]
    for index, command in enumerate(scene.commands):
        if index:
            lines += ["Hide", 'Type "clear"', "Enter", "Show"]
        escaped = command.replace("`", "\\`")
        lines += [f"Type `{escaped}`", "Sleep 300ms", "Enter", "Wait@60s", f"Sleep {scene.hold}s", ""]
    lines.append("Sleep 1s")
    return "\n".join(lines) + "\n"


def record_cli(scene: Scene, work: Path, env: dict[str, str]) -> Path:
    """Render a CLI scene with VHS into an MP4 and return it.

    Raises:
        RecordError: If vhs fails or writes nothing.
    """
    video = work / f"{scene.name}.mp4"
    tape_file = work / f"{scene.name}.tape"
    tape_file.write_text(tape(scene, video.name), encoding="utf-8")
    run(["vhs", tape_file.name], env=env, timeout=1800, cwd=work)
    if not video.is_file():
        msg = f"vhs wrote no video for scene {scene.name!r}"
        raise RecordError(msg)
    return video


def transcript_cli(scene: Scene, env: dict[str, str]) -> str:
    """What the commands print, run once more on a plain terminal, as the page's text alternative."""
    parts = []
    plain = env | {"TERM": "dumb", "COLUMNS": "100", "NO_COLOR": "1"}
    for command in scene.commands:
        result = subprocess.run(  # noqa: S602 - the scene's own `hz ...` lines, checked by `hz system plugin docs show`
            command, shell=True, env=plain, check=False, capture_output=True, text=True, timeout=600
        )
        output = [line.rstrip() for line in ANSI.sub("", result.stdout or result.stderr).rstrip().splitlines()]
        if len(output) > TRANSCRIPT_LINES:
            output = [*output[:TRANSCRIPT_LINES], f"… ({len(output) - TRANSCRIPT_LINES} more lines)"]
        parts.append("```console\n$ " + command + "\n" + "\n".join(output) + "\n```")
    return "\n\n".join(parts) + "\n"


# UI scenes.


@contextlib.contextmanager
def web_app(env: dict[str, str]) -> Iterator[str]:
    """`hz ui --no-browser --port 0` in the sandbox, stopped afterwards.

    Yields:
        The session URL, token included, once the app is up.

    Raises:
        RecordError: If the app doesn't start within a minute.
    """
    process = subprocess.Popen(  # noqa: S603 - a fixed command, resolved in the sandbox
        [which("hz", env), "ui", "--no-browser", "--port", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None  # noqa: S101 - Popen with PIPE
        url = ""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line.startswith("http"):
                url = line.strip()
                break
            if process.poll() is not None:
                break
        if not url:
            error = process.stderr.read() if process.stderr else ""
            msg = f"hz ui didn't start: {error[-800:]}"
            raise RecordError(msg)
        yield url
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(30)


def heading_of(page: Any) -> str:  # noqa: ANN401 - Playwright's page, imported lazily
    """The screen's heading for the transcript, or nothing: decoration, never a reason to fail."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: PLC0415 - installed by `make docs-record`

    try:
        return (page.locator("main h1, h1").first.text_content(timeout=1000) or "").strip()
    except PlaywrightTimeout:
        return ""


def settled(page: Any) -> None:  # noqa: ANN401 - Playwright's page, imported lazily
    """Wait until nothing on the screen says it is loading; the app's CSP forbids polling a script, so a locator."""
    page.get_by_text("Loading…").first.wait_for(state="hidden", timeout=30000)


def record_ui(scene: Scene, url: str, work: Path, *, dark: bool) -> tuple[list[tuple[Path, float]], str]:
    """Drive a UI scene with Playwright; one PNG per screenshot step, with its hold, and the transcript.

    Raises:
        TimeoutError: Playwright's, when a step's element or text doesn't appear and the step isn't `optional`.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: PLC0415 - installed by `make docs-record`
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    frames: list[tuple[Path, float]] = []
    notes: list[str] = []
    suffix = "-dark" if dark else ""
    base = url.split("#", 1)[0].rstrip("/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT}, color_scheme="dark" if dark else "light", device_scale_factor=1
        )
        page = context.new_page()
        page.goto(url)
        # The wizard is closed, so the screens show; the pick of light or dark follows the emulated scheme.
        page.evaluate('localStorage.setItem("hz.onboarding", JSON.stringify({step: "you", visited: [], closed: true}))')
        page.evaluate("localStorage.removeItem('hz.theme')")
        page.goto(url)
        settled(page)
        for index, step in enumerate(scene.steps, 1):
            action, target, role = step.get("action"), step.get("target", ""), step.get("role", "")
            optional = bool(step.get("optional"))
            try:
                if action == "goto":
                    page.goto(f"{base}{target}")
                    settled(page)
                    notes.append(f"Open `{target}`.")
                elif action in {"click", "hover"}:
                    element = page.get_by_role(role or "button", name=target).first
                    (element.click if action == "click" else element.hover)()
                    notes.append(f"{'Click' if action == 'click' else 'Hover over'} the {role or 'button'} “{target}”.")
                elif action == "fill":
                    page.get_by_label(target).fill(step.get("value", ""))
                    notes.append(f"Type into “{target}”.")
                elif action == "key":
                    page.keyboard.press(target)
                    notes.append(f"Press {target}.")
                elif action == "wait":
                    page.get_by_text(target).first.wait_for()
                    notes.append(f"Wait for “{target}”.")
                elif action == "screenshot":
                    page.wait_for_timeout(400)  # transitions settle
                    frame = work / f"{scene.name}{suffix}-{index:02d}.png"
                    page.screenshot(path=str(frame))
                    frames.append((frame, float(step.get("hold", 1.5))))
                    notes.append(f"**{step.get('caption') or 'Screen'}** — {heading_of(page)}")
            except PlaywrightTimeout:
                if not optional:
                    raise
                notes.append(f"(skipped: {action} {target!r} wasn't available)")
        browser.close()
    return frames, "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1)) + "\n"


# Encoding.


def frames_to_video(frames: list[tuple[Path, float]], video: Path, env: dict[str, str]) -> None:
    """Stills with hold times into an MP4 of the recording's size, through ffmpeg's concat demuxer."""
    listing = video.with_suffix(".txt")
    lines = []
    for frame, hold in frames:
        lines += [f"file '{frame}'", f"duration {hold}"]
    lines.append(f"file '{frames[-1][0]}'")  # the concat demuxer needs the last frame named twice to hold it
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    scale = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2"
    run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-vf",
         f"{scale},fps={FRAMERATE},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(video)],
        env=env,
    )  # fmt: skip


def concat_videos(videos: list[Path], output: Path, env: dict[str, str]) -> None:
    """Several MP4s of the recording's size into one."""
    listing = output.with_suffix(".txt")
    listing.write_text("".join(f"file '{v}'\n" for v in videos), encoding="utf-8")
    normalized = []
    for index, video in enumerate(videos):
        same = output.with_name(f"{output.stem}-part{index}.mp4")
        scale = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2"
        run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", f"{scale},fps={FRAMERATE},format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", str(same)],
            env=env,
        )  # fmt: skip
        normalized.append(same)
    listing.write_text("".join(f"file '{v}'\n" for v in normalized), encoding="utf-8")
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(output),
        ],
        env=env,
    )


def publish(video: Path, target: Path, env: dict[str, str], *, budget_mb: float, still: str = "last") -> list[Path]:
    """From an MP4: `target.gif`, `.webp`, `.mp4` and a `.png` still, each inside the size budget.

    The still is for people who turn motion off: the last frame of a CLI scene, where the output stands, or the
    first screen of a UI scene.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    budget = int(budget_mb * 1024 * 1024)
    for extension, arguments in ENCODINGS:
        out = target.with_suffix(f".{extension}")
        for scale in SCALES:
            width = int(WIDTH * scale) // 2 * 2
            filled = [a.format(fps=GIF_FPS, w=width) for a in arguments]
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), *filled, str(out)], env=env)
            if out.stat().st_size <= budget:
                break
        else:
            print(
                f"! {out.relative_to(ROOT)} is {out.stat().st_size / 1e6:.1f} MB, over the {budget_mb} MB budget",
                file=sys.stderr,
            )
        written.append(out)
    frame = target.with_suffix(".png")
    seek = ["-sseof", "-0.3"] if still == "last" else []
    scale = f"scale={WIDTH}:-1"
    run(
        ["ffmpeg", "-y", "-loglevel", "error", *seek, "-i", str(video), "-frames:v", "1", "-vf", scale, str(frame)],
        env=env,
    )
    written.append(frame)
    return written


def confidential(text: str, env: dict[str, str]) -> None:
    """Refuse a transcript that holds a confidential term, when the terms are set (`CONFIDENTIAL_TERMS`).

    Raises:
        RecordError: If the check fails.
    """
    if not CONFIDENTIAL.is_file():
        return
    result = subprocess.run(  # noqa: S603 - a fixed command
        [which("uv", env), "run", "--quiet", "--script", str(CONFIDENTIAL), "--text", text],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        msg = "a transcript holds a confidential term; the recording was not written"
        raise RecordError(msg)


# Putting it together.


def record_page(  # noqa: PLR0913 - the page, where it goes, the sandbox, and what to narrow to
    page: Page, directory: Path, work: Path, env: dict[str, str], *, only: str | None, budget_mb: float
) -> list[Path]:
    """Every scene of a page into its assets directory.

    Raises:
        RecordError: If a tool fails or a transcript holds a confidential term.
    """
    assets = directory / page.distribution / "assets"
    written: list[Path] = []
    for scene in page.scenes:
        if only and scene.name != only:
            continue
        print(f"  {page.distribution}: {scene.kind} scene {scene.name!r}", flush=True)
        scene_work = work / page.distribution / scene.name
        scene_work.mkdir(parents=True, exist_ok=True)
        if scene.kind == "cli":
            video = record_cli(scene, scene_work, env)
            transcript = transcript_cli(scene, env)
            confidential(transcript, env)
            written += publish(video, assets / f"{scene.name}.x", env, budget_mb=budget_mb)
        else:
            with web_app(env) as url:
                frames, transcript = record_ui(scene, url, scene_work, dark=False)
                dark_frames, _ = record_ui(scene, url, scene_work, dark=True)
            confidential(transcript, env)
            for suffix, shots in (("", frames), ("-dark", dark_frames)):
                video = scene_work / f"{scene.name}{suffix}.mp4"
                frames_to_video(shots, video, env)
                written += publish(video, assets / f"{scene.name}{suffix}.x", env, budget_mb=budget_mb, still="first")
        (assets / f"{scene.name}.md").write_text(transcript, encoding="utf-8")
        written.append(assets / f"{scene.name}.md")
    return written


def record_intro(work: Path, env: dict[str, str], *, budget_mb: float) -> list[Path]:
    """The README's intro: the demo tape's CLI part, then the `[intro] scenes` of docs/scenes.toml, in one recording.

    Raises:
        RecordError: If a named scene isn't declared, or a tool fails.
    """
    data = tomllib.loads(SCENES.read_text(encoding="utf-8")) if SCENES.is_file() else {}
    names: list[str] = list(data.get("intro", {}).get("scenes", []))
    core = next(p for p in pages(env, plugin="heinzel", scenes=SCENES, builtin=True) if p.distribution == "heinzel")
    by_name = {scene.name: scene for scene in core.scenes}
    videos: list[Path] = []
    if INTRO_TAPE.is_file():
        print("  intro: the demo tape", flush=True)
        text = INTRO_TAPE.read_text(encoding="utf-8")
        video = work / "intro-cli.mp4"
        text = re.sub(r"(?m)^Output .*$", f"Output {video.name}", text)
        tape_file = work / "intro-cli.tape"
        tape_file.write_text(text, encoding="utf-8")
        run(["vhs", tape_file.name], env=env, timeout=1800, cwd=work)
        videos.append(video)
    for name in names:
        scene = by_name.get(name)
        if scene is None:
            msg = f"[intro] scenes names {name!r}, which docs/scenes.toml doesn't declare"
            raise RecordError(msg)
        print(f"  intro: {scene.kind} scene {name!r}", flush=True)
        scene_work = work / "intro" / name
        scene_work.mkdir(parents=True, exist_ok=True)
        if scene.kind == "cli":
            videos.append(record_cli(scene, scene_work, env))
        else:
            with web_app(env) as url:
                frames, _ = record_ui(scene, url, scene_work, dark=False)
            video = scene_work / f"{name}.mp4"
            frames_to_video(frames, video, env)
            videos.append(video)
    combined = work / "intro.mp4"
    concat_videos(videos, combined, env)
    return publish(combined, INTRO_OUT.with_suffix(".x"), env, budget_mb=budget_mb)


def main(argv: list[str] | None = None) -> int:
    """Record the scenes, and with --intro the README's intro; 0 when every recording was written."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plugin", help="one plugin, by short name or distribution")
    parser.add_argument("--scene", help="one scene, by name")
    parser.add_argument("--directory", type=Path, default=ROOT / "docs" / "plugins", help="where the pages are")
    parser.add_argument("--intro", action="store_true", help="also render the README's intro (the core only)")
    parser.add_argument("--intro-only", action="store_true", help="render the intro and no plugin page's scenes")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox and the work directory")
    parser.add_argument("--budget", type=float, default=BUDGET_MB, help="size budget per recording, in MB")
    args = parser.parse_args(argv)
    args.intro = args.intro or args.intro_only
    if args.intro and not is_core(ROOT):
        print("✗ --intro renders the README's intro of Heinzelmännchen itself; a plugin has none", file=sys.stderr)
        return 2
    sandbox = SANDBOX
    shutil.rmtree(sandbox, ignore_errors=True)
    env = environment(sandbox)
    work = Path(tempfile.mkdtemp(prefix="hz-docs-work-"))
    try:
        tools(env)
        seed(sandbox, env)
        web_app_built(ROOT, env)
        print("installing hz into the sandbox", flush=True)
        install(ROOT, env)
        written: list[Path] = []
        if not args.intro_only:
            for page in pages(env, plugin=args.plugin, scenes=SCENES if SCENES.is_file() else None, builtin=False):
                written += record_page(page, args.directory, work, env, only=args.scene, budget_mb=args.budget)
        if args.intro:
            written += record_intro(work, env, budget_mb=args.budget)
    except RecordError as error:
        print(f"✗ {error}", file=sys.stderr)
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(sandbox, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"kept {sandbox} and {work}")
    for path in written:
        size = path.stat().st_size / 1e6
        print(f"✓ {path.relative_to(ROOT)} ({size:.1f} MB)" if size >= SHOWN_MB else f"✓ {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
