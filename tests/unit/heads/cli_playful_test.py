import re
from pathlib import Path

import pytest
from rich.console import Console

from heinzel import Settings
from heinzel.heads.cli import _banner, _playful
from heinzel.heads.cli._art_data import BUST, PALETTE, SWARM
from heinzel.heads.cli._progress import ConsoleProgress

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "golden" / "banner.ans"
LINES = ["Heinzelmännchen", "hz 0.1.0", "open-source helpers", "that keep product teams running"]
CLEAN: dict[str, str] = {"TERM": "xterm-256color"}


def tty(*, system: str = "truecolor", width: int = 80) -> Console:
    return Console(force_terminal=True, color_system=system, width=width, record=True)  # type: ignore[arg-type]


def test_art_is_allowed_on_a_colour_terminal():
    assert _playful.allowed(Settings(), tty(), CLEAN)


@pytest.mark.parametrize(
    "env",
    [{"NO_COLOR": "1"}, {"CI": "true"}, {"TERM": "dumb"}],
    ids=["no-color", "ci", "dumb"],
)
def test_art_is_off_for_no_color_ci_and_dumb_terminals(env: dict[str, str]):
    assert not _playful.allowed(Settings(), tty(), {**CLEAN, **env})


def test_art_is_off_when_stdout_is_not_a_terminal():
    assert not _playful.allowed(Settings(), Console(force_terminal=False, width=80), CLEAN)


def test_art_is_off_when_the_setting_is_off():
    assert not _playful.allowed(Settings(playful=False), tty(), CLEAN)


def test_the_setting_reads_hz_playful(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HZ_PLAYFUL", "false")
    assert Settings().playful is False


@pytest.mark.parametrize("system", ["truecolor", "256", "standard"])
def test_banner_and_swarm_fit_80_columns_in_every_colour_depth(system: str):
    console = tty(system=system)
    console.print(_playful.banner(LINES), end="")
    console.print(_playful.swarm())
    assert max(len(line) for line in console.export_text().splitlines()) <= 80


def test_banner_matches_the_golden():
    console = tty()
    console.print(_playful.banner(LINES), end="")
    # the editors' and hooks' trailing-whitespace trim must not break the golden
    lines = [line.rstrip() for line in console.export_text(styles=True).splitlines()]
    assert lines == [line.rstrip() for line in GOLDEN.read_text().splitlines()]


def test_banner_keeps_the_words_readable_beside_the_art():
    console = tty()
    console.print(_playful.banner(LINES), end="")
    text = console.export_text()
    assert all(line in text for line in LINES)


def test_art_data_is_rectangular_and_uses_only_palette_colours():
    for rows in (BUST, SWARM):
        assert len({len(row) for row in rows}) == 1
        assert {c for row in rows for c in row} <= set(".0123456789abcdef"[: len(PALETTE) + 1])


def test_every_walking_frame_is_equally_wide():
    widths = {_playful.walking(step / 8).cell_len for step in range(16)}
    assert len(widths) == 1


def test_playful_progress_still_shows_its_description():
    console = tty()
    with ConsoleProgress(console, playful=True).task("Installing skills") as advance:
        advance(1)
    assert "Installing skills" in console.export_text()


def test_banner_prints_nothing_without_a_terminal(capsys: pytest.CaptureFixture[str]):
    class Kernel:
        settings = Settings()

    _banner(Kernel())  # type: ignore[arg-type]
    assert not re.search("[▀▄█]", capsys.readouterr().out)
