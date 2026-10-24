"""The module declaration, apart from `__init__.py` so the adapter and service can import it."""

from heinzel import Module

module = Module(
    "zoom",
    "Read meetings from Zoom's meeting-summary emails: the meetings capability.",
    # The summaries arrive by mail: whichever adapter serves `mail` reads them, so no Zoom credentials are needed.
    requires=["mail:read"],
)
