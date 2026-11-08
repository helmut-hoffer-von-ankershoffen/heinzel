"""Describe an unchanged plugin in update progress and results."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heinzel.modules.system._models import PluginUpdate


def standing(update: "PluginUpdate") -> str:
    """Where an unchanged plugin stands, saying newest only when nothing pins it.

    After an in-place update, the renderer can pass a plugin model from the old release.
    """
    version = update.after or "version unknown"
    if not getattr(update, "checked", True):
        return f"not updated ({version}): the plugin update stopped before it; newer releases not checked"
    pinned = getattr(update, "pinned", None)
    if pinned is not None:
        return f"kept at {version} (pinned to {pinned}; newer releases not checked)"
    return f"already newest ({version})"
