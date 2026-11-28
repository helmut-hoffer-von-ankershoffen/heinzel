"""Compatibility path for the kernel's text helpers (ADR 0028)."""

import re

from heinzel.spec._text import (
    CONTROL_CHARACTER_PATTERN,
    EMAIL_PATTERN,
    NAME_ACRONYMS,
    REDACTED_EMAIL,
    clean_text,
    display_name,
    redact_emails,
)

__all__ = ["NAME_ACRONYMS", "REDACTED_EMAIL", "clean_text", "display_name", "re", "redact_emails"]

_CONTROLS = CONTROL_CHARACTER_PATTERN
_EMAIL = EMAIL_PATTERN
