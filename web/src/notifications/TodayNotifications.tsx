// Today's tease of the notification center (ADR 0043): how many notices you haven't seen, the latest few, and the way
// to all of them. Nothing unread, nothing shown: the center itself is a click away on the bell.
import { Bell, ListChecks } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { useLiveNotices, useSeen, when } from "@/layout/NotificationCenter";

const TEASED = 3;

export function TodayNotifications() {
  const navigate = useNavigate();
  const notices = useLiveNotices();
  const [seen] = useSeen();
  const unread = (notices.data?.notices ?? []).filter((notice) => notice.id > seen);
  if (unread.length === 0) return null;
  // The same notice repeated (a test sent three times) is one line; the count above still counts them all.
  const teased = [...new Map(unread.map((notice) => [`${notice.title}\n${notice.route}`, notice])).values()].slice(
    0,
    TEASED,
  );
  const heading = t("today.notifications");
  return (
    <li
      data-widget="notifications"
      className="flex min-w-0 flex-col gap-2 rounded-lg border-l-4 border-l-primary bg-card p-3 text-sm shadow-sm @[31rem]:col-span-2"
    >
      <div className="flex items-center gap-2">
        <h3 className="flex min-w-0 flex-1 items-center gap-2 font-semibold">
          <Bell aria-hidden className="size-4 shrink-0" />
          <span className="truncate">{heading}</span>
          <span className="ml-auto shrink-0 whitespace-nowrap rounded-full bg-primary px-2 text-xs font-medium text-primary-foreground">
            {t("today.notifications.unread", { count: unread.length })}
          </span>
        </h3>
        <Button
          size="icon"
          variant="ghost"
          className="-my-1 -mr-1 size-8"
          onClick={() => navigate("/?panel=notifications")}
          aria-label={t("today.notifications.all")}
          title={t("today.notifications.all")}
        >
          <ListChecks aria-hidden />
        </Button>
      </div>
      <ul className="flex flex-col gap-1 text-xs">
        {teased.map((notice) => (
          <li key={notice.id}>
            <Link to={notice.route} className="flex items-baseline gap-2 hover:underline">
              <span className="truncate" title={notice.title}>
                {notice.title}
              </span>
              <span className="ml-auto shrink-0 text-muted-foreground">{when(notice.at)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </li>
  );
}
