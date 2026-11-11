"""Optional personal presentation must not prevent an already authorized local notice."""

from heinzel import NoticeHelper
from heinzel.modules.heinzel._session_crud import SessionCrud


def notice_helper(crud: SessionCrud, session_id: str, reason: str) -> NoticeHelper | None:
    """Read a bounded personal identity when available; omit it on failure without exposing private diagnostics."""
    try:
        profile = crud.snapshot(session_id, limit=1)
        return NoticeHelper(name=profile.name, hat=profile.appearance.hat, reason=reason)
    except Exception:  # noqa: BLE001 - optional presentation cannot suppress an authorized notice
        return None
