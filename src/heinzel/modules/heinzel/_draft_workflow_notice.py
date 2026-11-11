"""Private owner notices through the ordinary local notification policy, with no external message."""

from heinzel import (
    ChannelNotice,
    ChannelReceipt,
    CurrentUser,
    HeinzelError,
    NoticeKind,
    Notifications,
    OwnerNoticeChannel,
    OwnerNoticeTarget,
)


class LocalDraftNotice(OwnerNoticeChannel):
    """Use durable local notification keys; queued never asserts native display or OS permission."""

    name = "local-draft-review"
    supported_operations = frozenset({"private-owner-notice"})

    def __init__(self, current: CurrentUser, notifications: Notifications) -> None:
        self.current, self.notifications = current, notifications

    def available(self) -> bool:
        """Keep local private notification policy available to its authenticated owner."""
        return True

    def owner_target(self) -> OwnerNoticeTarget:
        """Bind the exact installation owner's local notification inbox, without changing policy."""
        owner = self.current.owner().id
        return OwnerNoticeTarget(
            account=owner,
            destination="local-owner-inbox",
            scope="local-notification-policy-v1",
            owner_actor=owner,
            private_owner=True,
        )

    def deliver_owner_reviewed(self, notice: ChannelNotice, target: OwnerNoticeTarget) -> ChannelReceipt:
        """Queue once under existing kinds, quiet hours and rate limits; no external transport is contacted.

        Raises:
            HeinzelError: If the current owner differs from the published local inbox.
        """
        if target != self.owner_target() or self.current.context().principal != self.current.owner():
            msg = "The private local notification owner changed"
            raise HeinzelError(msg)
        accepted = self.notifications.emit_once(
            NoticeKind.HEINZEL,
            notice.title,
            notice.body,
            route=notice.route,
            command="hz heinzel sessions",
            key=notice.key,
        )
        return ChannelReceipt(channel=self.name, status="queued" if accepted else "unknown", reference=notice.key)
