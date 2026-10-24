// The notification center: every notice hz recorded (ADR 0043), newest first, beside the sidebar. Read state is this
// browser's: notices after the last one seen count as unread, on the bell and in the list.
import { AlertTriangle, Bell, BellRing, CalendarClock, FileText, Gift, Mail, Stethoscope, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import type { SchemaNotice } from "@/api/client";
import { useNotices } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { NotificationTest } from "@/notifications/NotificationTest";

const SEEN = "hz.notify.seen";
const CHANGED = "hz:notify-seen";

function seenId(): string {
  try {
    return localStorage.getItem(SEEN) ?? "";
  } catch {
    return "";
  }
}

/** The id of the newest notice seen here; notice ids sort by time. */
function useSeen(): [string, (id: string) => void] {
  const [seen, setSeen] = useState(seenId);
  useEffect(() => {
    const update = () => setSeen(seenId());
    window.addEventListener(CHANGED, update);
    return () => window.removeEventListener(CHANGED, update);
  }, []);
  const mark = (id: string) => {
    try {
      localStorage.setItem(SEEN, id);
    } catch {
      // Not remembered: unread again next time.
    }
    window.dispatchEvent(new Event(CHANGED));
  };
  return [seen, mark];
}

/** New notices arrive over the app's stream as `hz:notice` events; the list is read again at once. */
function useLiveNotices() {
  const notices = useNotices();
  const { refetch } = notices;
  useEffect(() => {
    const again = () => void refetch();
    window.addEventListener("hz:notice", again);
    return () => window.removeEventListener("hz:notice", again);
  }, [refetch]);
  return notices;
}

export function useUnread(): number {
  const notices = useLiveNotices();
  const [seen] = useSeen();
  return (notices.data?.notices ?? []).filter((notice) => notice.id > seen).length;
}

/** The bell on the sidebar's top line; `label` shows the word beside it. */
export function BellButton({ className, onOpen }: { className: string; onOpen: () => void }) {
  const unread = useUnread();
  const label = unread ? t("notify.openUnread", { count: unread }) : t("notify.open");
  return (
    <button type="button" onClick={onOpen} aria-label={label} title={label} className={cn("relative", className)}>
      <Bell aria-hidden />
      {unread > 0 && (
        <span
          aria-hidden
          className="absolute top-0.5 right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground"
        >
          {unread > 99 ? "99+" : unread}
        </span>
      )}
    </button>
  );
}

const icons = {
  draft: FileText,
  failed: AlertTriangle,
  doctor: Stethoscope,
  release: Gift,
  meeting: CalendarClock,
  mail: Mail,
  test: BellRing,
} as const;

function when(at: string): string {
  const date = new Date(at);
  const today = new Date().toDateString() === date.toDateString();
  return today
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

export function NotificationPanel({ onClose, navOpen }: { onClose: () => void; navOpen: boolean }) {
  const notices = useLiveNotices();
  const [seen, markSeen] = useSeen();
  const [kind, setKind] = useState<SchemaNotice["kind"] | "all">("all");
  const navigate = useNavigate();
  const list = (notices.data?.notices ?? []).filter((notice) => kind === "all" || notice.kind === kind);
  // Ids are strings that sort by time; the last one sorted is the newest.
  const newest =
    (notices.data?.notices ?? [])
      .map((notice) => notice.id)
      .sort()
      .at(-1) ?? "";
  const today = new Date().toDateString();
  const groups = [
    { label: t("notify.today"), items: list.filter((notice) => new Date(notice.at).toDateString() === today) },
    { label: t("notify.earlier"), items: list.filter((notice) => new Date(notice.at).toDateString() !== today) },
  ].filter((group) => group.items.length > 0);
  const kinds: (SchemaNotice["kind"] | "all")[] = [
    "all",
    "draft",
    "failed",
    "doctor",
    "release",
    "meeting",
    "mail",
    "test",
  ];
  return (
    <section
      aria-label={t("notify.title")}
      onKeyDown={(event) => event.key === "Escape" && onClose()}
      className={cn(
        "absolute inset-y-0 z-40 flex w-[min(26rem,100vw)] flex-col border-r border-border bg-popover shadow-2xl",
        navOpen ? "left-64" : "left-14",
      )}
    >
      <header className="flex h-12 shrink-0 items-center gap-2 pr-2 pl-4">
        <h2 className="text-ui font-medium">{t("notify.title")}</h2>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto text-muted-foreground"
          disabled={!newest || newest <= seen}
          onClick={() => markSeen(newest)}
        >
          {t("notify.markRead")}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label={t("notify.close")}
          title={t("notify.close")}
          className="text-icon"
        >
          <X aria-hidden />
        </Button>
      </header>
      <div role="tablist" aria-label={t("notify.filter")} className="flex shrink-0 gap-1 overflow-x-auto px-3 pb-2">
        {kinds.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={kind === item}
            onClick={() => setKind(item)}
            className={cn(
              "shrink-0 rounded-lg px-2.5 py-1 text-xs transition-colors hover:bg-accent/70",
              kind === item ? "bg-accent text-foreground" : "text-muted-foreground",
            )}
          >
            {t(`notify.kind.${item}`)}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {groups.length === 0 && (
          <p className="px-3 py-8 text-center text-sm text-muted-foreground">
            {notices.isLoading ? t("notify.loading") : t("notify.empty")}
          </p>
        )}
        {groups.map((group) => (
          <section key={group.label} aria-label={group.label}>
            <h3 className="px-2 pt-3 pb-1 text-ui-sm text-muted-foreground">{group.label}</h3>
            <ul className="flex flex-col gap-0.5">
              {group.items.map((notice) => {
                const Icon = icons[notice.kind] ?? Bell;
                const unread = notice.id > seen;
                return (
                  <li key={notice.id}>
                    <button
                      type="button"
                      onClick={() => {
                        onClose();
                        navigate(notice.route);
                      }}
                      className="flex w-full items-start gap-3 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent/70"
                    >
                      <span
                        className={cn(
                          "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full",
                          notice.kind === "failed" ? "bg-bad/15 text-bad" : "bg-accent text-foreground/80",
                        )}
                      >
                        <Icon aria-hidden className="size-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline gap-2">
                          <span className={cn("truncate text-ui", unread && "font-medium")}>{notice.title}</span>
                          <span className="ml-auto shrink-0 text-xs text-muted-foreground">{when(notice.at)}</span>
                        </span>
                        {notice.body && (
                          <span className="mt-0.5 line-clamp-2 block text-sm text-muted-foreground">{notice.body}</span>
                        )}
                        <span className="mt-1 block text-xs text-muted-foreground/80">
                          {notice.delivered_by
                            ? t("notify.shownBy", { by: t(`notify.by.${notice.delivered_by}`) })
                            : notice.held
                              ? notice.held
                              : t("notify.waiting")}
                        </span>
                      </span>
                      {unread && (
                        <>
                          <span aria-hidden className="mt-2 size-2 shrink-0 rounded-full bg-primary" />
                          <span className="sr-only">{t("notify.unread")}</span>
                        </>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
      <footer className="flex max-h-[50%] shrink-0 flex-col gap-1.5 overflow-y-auto border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
        <span className="truncate">{notices.data?.native}</span>
        <NotificationTest />
      </footer>
    </section>
  );
}
