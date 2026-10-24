import pytest
from rich.console import Console

from heinzel.modules.system.changelog import as_markdown

pytestmark = pytest.mark.unit


def test_placeholders_and_brackets_are_shown_verbatim():
    text = "- Name plugins heinzel-<name>, see [link] and `heinzel-<name>` [bold]x[/bold]"
    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        console.print(as_markdown(text))
    assert capture.get().strip() == "• " + text.removeprefix("- ").replace("`", "")
