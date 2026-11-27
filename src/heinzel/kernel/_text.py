"""Compatibility path for the kernel's text helpers (ADR 0028)."""

import re

from heinzel.spec._text import _CONTROLS as _PUBLISHED_CONTROLS
from heinzel.spec._text import _EMAIL as _PUBLISHED_EMAIL
from heinzel.spec._text import NAME_ACRONYMS, REDACTED_EMAIL, clean_text, display_name, redact_emails

__all__ = ["NAME_ACRONYMS", "REDACTED_EMAIL", "clean_text", "display_name", "re", "redact_emails"]

_CONTROLS = _PUBLISHED_CONTROLS
_EMAIL = _PUBLISHED_EMAIL
