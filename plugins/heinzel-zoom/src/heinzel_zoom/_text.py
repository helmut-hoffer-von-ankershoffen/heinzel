"""Display names from Zoom's mails made safe: cleaned, and never an email address."""

import re

from heinzel import clean_text

_EMAIL = re.compile(r"[^\s@<>()\[\],;:\"]+@[^\s@<>()\[\],;:\"]+\.[^\s@<>()\[\],;:\"]+")


def name(text: object) -> str | None:
    """A display name as shown, cleaned; None if empty or if it is, or holds, an email address.

    Names reach agents' prompts, addresses never do (ADR 0024, ADR 0028).
    """
    if not isinstance(text, str):
        return None
    cleaned = " ".join(clean_text(text).split())
    if not cleaned or _EMAIL.search(cleaned):
        return None
    return cleaned


ADDRESS = "[address]"
"""What stands where an email address was."""


def without_addresses(text: str) -> str:
    """`text` with every email address replaced by `[address]`: a summary may quote one, a prompt never gets it."""
    return _EMAIL.sub(lambda found: ADDRESS + found.group()[len(found.group().rstrip(".!?")) :], text)
