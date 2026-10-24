"""Text from outside the process, made safe to show and to prompt with (ADR 0028)."""

import re

_CONTROLS = re.compile(
    "[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f"  # C0 but tab and newline (so also carriage return), DEL, C1
    "\\u200b\\u200e\\u200f\\u2028\\u2029\\u2060\\ufeff\\u061c"  # zero-width and line separators, direction marks
    "\\u202a-\\u202e\\u2066-\\u2069"  # bidirectional overrides and isolates
    "\\U000e0000-\\U000e007f]"  # tag characters, which smuggle invisible ASCII
)


def clean_text(text: str) -> str:
    """`text` without control, invisible and bidirectional characters, for terminals and prompts.

    Removed: C0 controls but tab and newline (a carriage return goes too: CR LF becomes LF), DEL and C1;
    zero-width space, word joiner and byte order mark; line and paragraph separators; left-to-right, right-to-left
    and Arabic letter marks, bidirectional overrides and isolates; and the Unicode tag characters (U+E0000-E007F),
    which carry invisible ASCII an agent's model would still read.

    Adapters pass every string they return through this: text from a tracker, a channel or a transcript ends up in
    terminals and in agents' prompts, and whoever can write there could otherwise hide or reorder what they show.
    What the text says is still data, never an instruction.
    """
    return _CONTROLS.sub("", text)
