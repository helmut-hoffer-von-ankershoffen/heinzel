"""The directory module: the core's fallback for the `directory` capability (ADR 0024, ADR 0028).

Its `one-on-ones` adapter drafts reporting lines from recurring 1:1s in the bound calendar and marks each as a guess
with its evidence, for the user to confirm or correct in setup; it is the default binding until a plugin that reads a
directory of record (an HR system, a chat tool's profile fields, an IdP) is bound instead. It provides an adapter and
nothing else, so its own `calendar:read` is needed only while that adapter serves someone.
"""

from heinzel import Module
from heinzel.modules.system.directory._guess import OneOnOnes
from heinzel.modules.system.directory._models import DirectorySettings

module = Module(
    "directory",
    "Guess reporting lines from recurring 1:1s in your calendar, for setup to confirm.",
    requires=["calendar:read"],
)
module.settings(DirectorySettings)
module.provides("directory", modes={"read"}, name="one-on-ones")(OneOnOnes)

__all__ = ["DirectorySettings", "OneOnOnes", "module"]
