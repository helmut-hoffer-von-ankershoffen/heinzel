import pytest
from rich.console import Console

from heinzel import Progress
from heinzel.heads.cli._progress import ConsoleProgress

pytestmark = pytest.mark.unit


def test_kernel_default_progress_is_silent():
    with Progress().task("work", total=2) as advance:
        advance(1)


def test_console_progress_reports_on_its_console():
    console = Console(record=True, force_terminal=True, width=80)
    with ConsoleProgress(console).task("Installing skills", total=2) as advance:
        advance(2)
    with ConsoleProgress(console).task("Spinning") as advance:
        advance(1)
    assert "Installing skills" in console.export_text()


def test_disabled_console_progress_prints_nothing():
    console = Console(record=True, force_terminal=True)
    progress = ConsoleProgress(console)
    progress.enabled = False
    with progress.task("quiet") as advance:
        advance(1)
    assert console.export_text() == ""
