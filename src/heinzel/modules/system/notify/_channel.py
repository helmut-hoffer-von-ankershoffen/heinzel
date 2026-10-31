"""The native notification channel: Heinzel Notifier on macOS, notify-send on Linux (ADR 0112).

It is a `NotificationChannel`. A channel carries words and never decides: the policy (kinds, quiet mode, working
hours, repeats, the rate limit) has decided before `deliver` is called. The receipt says only what was observed, so
nobody reads more into it:

- `delivered`: macOS holds the notification in Notification Center (Heinzel Notifier lists what it holds, by id). It
  does not say a banner was seen: screen sharing, a Focus or an alert style of None keep the banner back.
- `queued`: the notifier accepted it and nothing more can be seen (notify-send, osascript).
- `refused`: not shown, safe to retry: macOS doesn't allow it, hasn't asked yet, or the app isn't there.
- `unknown`: the notifier didn't answer in time; it may be shown, so it is never retried.
"""

import hashlib
import re
from typing import TYPE_CHECKING, Literal

from heinzel import ChannelNotice, ChannelReceipt, NoticeHelper, NotificationChannel
from heinzel.modules.system.notify._native import HEINZEL, sanitise

if TYPE_CHECKING:
    from heinzel.modules.system.notify._service import NotifyService

NAME = "native"
IDENTITY = re.compile(r"[A-Za-z0-9._:-]{1,120}")
"""What Heinzel Notifier accepts as a notification's identity; any other key is hashed into one."""
DETAIL_LIMIT = 200
SENDER = "Heinzelmännchen"
"""The sender of a notice no helper wrote (doctor, a release): it shows without a face."""
_UNANSWERED = ("waiting", "timed out", "no answer")
"""What a notifier says when it didn't answer: the notification may still be shown, so it isn't retried."""


def identity(key: str) -> str:
    """What macOS knows the notification by: the key itself when it is plain, else a digest of it."""
    return key if IDENTITY.fullmatch(key) else hashlib.sha256(key.encode()).hexdigest()[:40]


class NativeChannel(NotificationChannel):
    """Shows a notice on this machine's desktop; the same one twice is one notification, never two."""

    name = NAME

    def __init__(self, service: "NotifyService") -> None:
        """Deliver through `service`'s notifier, settings and web app address."""
        self._service = service

    def available(self) -> bool:
        """Whether a notifier exists, the setting allows it and, with Heinzel Notifier, macOS has allowed it."""
        service = self._service
        notifier = service.notifier
        if not service.settings.native or notifier.name is None:
            return False
        return (
            notifier.app is None or notifier.name != HEINZEL or notifier.app.status() in {"authorized", "provisional"}
        )

    def deliver(self, notice: ChannelNotice) -> ChannelReceipt:
        """Show one notice; never raises for a failure to show it."""
        service = self._service
        notifier = service.notifier
        ident = identity(notice.key)
        helper = None
        if notice.reason is not None or notice.helper != SENDER:  # a helper spoke, so it has a name and a face
            helper = NoticeHelper(name=notice.helper, hat=notice.hat, reason=notice.reason or "")
        url = service.ui_url(notice.route) if notifier.clicks else None
        problem = notifier.post(
            notice.title,
            notice.body,
            url=url,
            helper=helper,
            sound=service.settings.sound,
            ident=ident,
            thread=notice.route.split("&event=")[0].split("?event=")[0],
        )
        if problem is not None:
            unanswered = any(phrase in problem for phrase in _UNANSWERED)
            return self._receipt("unknown" if unanswered else "refused", ident, problem)
        held = notifier.app.delivered() if notifier.name == HEINZEL and notifier.app is not None else None
        if held is not None and ident in held:
            return self._receipt("delivered", ident, "held in Notification Center; a banner may be held back")
        return self._receipt("queued", ident, "accepted by the notifier; not confirmed shown")

    def _receipt(
        self, status: Literal["delivered", "queued", "refused", "unknown"], reference: str, detail: str
    ) -> ChannelReceipt:
        """The receipt, with a detail that is one clean bounded line."""
        return ChannelReceipt(
            channel=self.name, status=status, reference=reference, detail=sanitise(detail, DETAIL_LIMIT)
        )
