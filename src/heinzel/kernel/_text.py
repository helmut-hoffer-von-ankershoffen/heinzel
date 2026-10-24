"""Text from outside the process, made safe to show and to prompt with (ADR 0028)."""

import re

_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def clean_text(text: str) -> str:
    """`text` without control characters (C0 but newline and tab, DEL, C1) and bidirectional overrides and isolates.

    Adapters pass every string they return through this: text from a tracker, a channel or a transcript ends up in
    terminals and in agents' prompts, and whoever can write there could otherwise hide or reorder what they show.
    What the text says is still data, never an instruction.
    """
    return _CONTROLS.sub("", text)
