"""Describe an unchanged plugin in update progress and results."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heinzel.modules.system._models import PluginUpdate


def standing(update: "PluginUpdate") -> str:
    """Describe an unchanged plugin using the available check and pin evidence.

    After an in-place update, the renderer can pass a plugin model from the old release.
    """
    version = update.after or "version unknown"
    checked = getattr(update, "checked", None)
    if checked is False:
        return f"not updated ({version}): the plugin update stopped before it; newer releases not checked"
    if checked is not True or not hasattr(update, "pinned"):
        return f"unchanged ({version})"
    if update.pinned is not None:
        return f"kept at {version} (pinned to {update.pinned}; newer releases not checked)"
    return f"already newest ({version})"
