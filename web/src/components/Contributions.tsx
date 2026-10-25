// What modules contribute to the app (ADR 0056): status items (footer, sidebar), header actions and Today widgets.
// A module sends data only, in closed shapes this file renders; it never ships code. Each list is fetched once, then
// each entry at its own refresh, so a slow or failing one never holds up the rest. What a click does comes from the
// server: a write runs only after the person confirms the exact `hz …` call the server built.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlarmClock,
  Bell,
  Calendar,
  CalendarClock,
  ChartLine,
  CircleAlert,
  CircleCheck,
  Clock,
  Cloud,
  Code,
  FileText,
  GitPullRequest,
  Globe,
  Heart,
  Inbox,
  Info,
  Link as LinkIcon,
  ListChecks,
  type LucideIcon,
  Mail,
  MessageSquare,
  Play,
  Rocket,
  Star,
  Terminal,
  Timer,
  Users,
  Video,
  X,
  Zap,
} from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router";
import {
  api,
  problem,
  type SchemaActionEntry,
  type SchemaOperationCall,
  type SchemaStatusEntry,
  type SchemaStatusItem,
  type SchemaWidgetEntry,
  unwrap,
} from "@/api/client";
import { token } from "@/api/token";
import { FooterItem, stateOf } from "@/components/FooterItem";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { HeinzelFace } from "@/components/HeinzelFace";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { runInTerminal, runsAsIs, typeInTerminal } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import { zoneClock, zoneTime } from "@/lib/zoneTime";

export type Icon = NonNullable<SchemaStatusItem["icon"]>;
export type Effect = NonNullable<SchemaStatusItem["effect"]>;
type Tone = SchemaStatusItem["tone"];
type Live = NonNullable<SchemaStatusItem["live"]>;
type Widget = NonNullable<SchemaWidgetEntry["widget"]>;
type Slot = "footer" | "sidebar" | "header" | "today";

/** Every icon a contribution may name; the type makes the map complete, so a new server icon fails the build. */
export const ICONS: Record<Icon, LucideIcon> = {
  activity: Activity,
  "alarm-clock": AlarmClock,
  bell: Bell,
  bot: HeinzelFace,
  calendar: Calendar,
  "calendar-clock": CalendarClock,
  "chart-line": ChartLine,
  "circle-alert": CircleAlert,
  "circle-check": CircleCheck,
  clock: Clock,
  cloud: Cloud,
  code: Code,
  "file-text": FileText,
  "git-pull-request": GitPullRequest,
  globe: Globe,
  heart: Heart,
  inbox: Inbox,
  info: Info,
  link: LinkIcon,
  "list-checks": ListChecks,
  mail: Mail,
  "message-square": MessageSquare,
  play: Play,
  rocket: Rocket,
  star: Star,
  terminal: Terminal,
  timer: Timer,
  users: Users,
  video: Video,
  zap: Zap,
};

const TONE_TEXT: Record<Tone, string> = {
  neutral: "",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
};
const TONE_BORDER: Record<Tone, string> = {
  neutral: "border-l-border",
  ok: "border-l-ok",
  warn: "border-l-warn",
  bad: "border-l-bad",
};
const LIST = 300_000;
/** The lists themselves are asked again every five minutes: a new plugin's contribution shows without a reload. */

const fetchers = {
  status: (item?: string) =>
    unwrap(api.GET("/system/ui/status", { params: { query: item ? { item } : {} } })) as Promise<SchemaStatusEntry[]>,
  actions: (item?: string) =>
    unwrap(api.GET("/system/ui/actions", { params: { query: item ? { item } : {} } })) as Promise<SchemaActionEntry[]>,
  widgets: (item?: string) =>
    unwrap(api.GET("/system/ui/widgets", { params: { query: item ? { item } : {} } })) as Promise<SchemaWidgetEntry[]>,
};
type Kind = keyof typeof fetchers;
type EntryOf<K extends Kind> = Awaited<ReturnType<(typeof fetchers)[K]>>[number];

/** One kind's entries in `slot`, each kept current at its own refresh. */
function useEntries<K extends Kind>(kind: K, slot: Slot): { entries: EntryOf<K>[]; updatedAt: number } {
  const list = useQuery({
    queryKey: ["ui", kind],
    queryFn: () => fetchers[kind](),
    refetchInterval: LIST,
    retry: false,
  });
  const entries = ((list.data ?? []) as EntryOf<K>[]).filter((entry) => entry.slots[slot]);
  return { entries, updatedAt: list.dataUpdatedAt };
}

/** One entry, asked again by id every `refresh` seconds; a failing request keeps what it showed. */
function useEntry<K extends Kind>(kind: K, entry: EntryOf<K>, updatedAt: number): EntryOf<K> {
  const query = useQuery({
    queryKey: ["ui", kind, entry.id],
    queryFn: async () => ((await fetchers[kind](entry.id))[0] ?? entry) as EntryOf<K>,
    initialData: entry,
    initialDataUpdatedAt: updatedAt,
    staleTime: entry.refresh * 1000,
    refetchInterval: entry.refresh * 1000,
    retry: false,
  });
  return query.data ?? entry;
}

export function useNow(every = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(new Date()), every);
    return () => window.clearInterval(tick);
  }, [every]);
  return now;
}

const hhmm = (date: Date) => zoneTime(date);

/** A live value in the person's locale: the time in a zone, a moment's time, or how soon it is. */
export function liveText(live: Live, now: Date): string {
  if (live.kind === "clock") {
    return zoneClock(now, live.zone);
  }
  const at = new Date(live.at);
  if (live.kind === "time") return hhmm(at);
  const minutes = Math.round((at.getTime() - now.getTime()) / 60_000);
  if (minutes < 1) return t("status.now");
  if (at.toDateString() !== now.toDateString()) return hhmm(at);
  if (minutes < 60) return t("status.inMinutes", { minutes });
  return t("status.inHours", { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

/** Runs what a click does; a write first asks, showing the call the server built and its summary, never a label. */
export function useEffectRunner(): { run: (effect: Effect) => void; dialog: ReactNode } {
  const navigate = useNavigate();
  const queries = useQueryClient();
  const [pending, setPending] = useState<SchemaOperationCall | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const call = async (operation: SchemaOperationCall) => {
    const url = new URL(`/api${operation.path}`, window.location.origin);
    for (const [key, value] of Object.entries(operation.query)) url.searchParams.set(key, String(value));
    const response = await fetch(
      new Request(url, { method: operation.method, headers: { Authorization: `Bearer ${token() ?? ""}` } }),
    );
    setOutcome(
      response.ok
        ? t("contrib.done", { command: operation.command })
        : problem(response.status, await response.json().catch(() => null)).message,
    );
    void queries.invalidateQueries({ queryKey: ["ui"] });
  };
  const run = (effect: Effect) => {
    setOutcome(null);
    if (effect.kind === "navigate") void navigate(effect.path);
    else if (effect.kind === "open") window.open(effect.url, "_blank", "noopener,noreferrer");
    else if (effect.kind === "command") {
      if (effect.runs && runsAsIs(effect.command)) runInTerminal(effect.command);
      else typeInTerminal(effect.command);
    } else if (effect.call) {
      if (effect.call.read_only) void call(effect.call);
      else setPending(effect.call);
    }
  };
  const dialog = (
    <>
      {pending && (
        <div
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="contrib-confirm"
          onKeyDown={(event) => event.key === "Escape" && setPending(null)}
          className="fixed inset-x-0 bottom-20 z-50 mx-auto w-[min(28rem,calc(100%-2rem))] rounded-xl border border-border bg-popover p-4 text-sm shadow-xl"
        >
          <p id="contrib-confirm" className="font-medium">
            {pending.summary}
          </p>
          <p className="mt-1 text-muted-foreground">{t("contrib.confirm")}</p>
          <code className="mt-2 block rounded bg-muted px-2 py-1 font-mono text-xs break-all">{pending.command}</code>
          <div className="mt-3 flex justify-end gap-2">
            <Button size="sm" variant="outline" autoFocus onClick={() => setPending(null)}>
              {t("contrib.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                const confirmed = pending;
                setPending(null);
                void call(confirmed);
              }}
            >
              {t("contrib.run")}
            </Button>
          </div>
        </div>
      )}
      <span className="sr-only" aria-live="polite">
        {outcome}
      </span>
    </>
  );
  return { run, dialog };
}

function IconOf({ icon, className }: { icon?: Icon | null; className?: string }) {
  if (!icon) return null;
  const Component = ICONS[icon];
  return <Component aria-hidden className={cn("size-3.5 shrink-0", className)} />;
}

/** The effect's button in a pane: it opens or runs what the item's click used to. */
function effectLabel(effect: Effect): string {
  return effect.kind === "open" || effect.kind === "navigate" ? t("status.open") : t("status.runIt");
}

/** A footer status item: only its icon, in the colour and shape of its declared tone; text, detail and buttons in a pane. */
function FooterStatus({
  entry,
  updatedAt,
  run,
}: {
  entry: SchemaStatusEntry;
  updatedAt: number;
  run: (effect: Effect) => void;
}) {
  const current = useEntry("status", entry, updatedAt);
  const now = useNow(current.item?.live?.kind === "clock" ? 1000 : 30_000);
  const item = current.item;
  if (!item) {
    return current.problem ? <FooterItem label={current.problem} state="bad" icon={CircleAlert} /> : null;
  }
  const label = [item.text, item.live ? liveText(item.live, now) : ""].filter(Boolean).join(" ");
  const effect = item.effect;
  return (
    <FooterItem label={label} state={stateOf(item.tone)} icon={item.icon ? ICONS[item.icon] : Info}>
      <ul>
        {(item.detail ?? []).map((line) => (
          <li key={line.text} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5">
            <span aria-hidden className={cn("size-2 shrink-0 rounded-full", DOT[line.tone])} />
            <span className="min-w-0">{line.text}</span>
          </li>
        ))}
      </ul>
      {((item.actions ?? []).length > 0 || effect) && (
        <div className="mt-1 flex flex-wrap gap-1.5 border-t border-border px-1.5 pt-2 pb-1">
          {effect && (
            <Button size="sm" variant="outline" onClick={() => run(effect)}>
              {effectLabel(effect)}
            </Button>
          )}
          {(item.actions ?? []).map((action) => (
            <Button key={action.label} size="sm" variant="outline" onClick={() => run(action.effect)}>
              <IconOf icon={action.icon} />
              {action.label}
            </Button>
          ))}
        </div>
      )}
    </FooterItem>
  );
}

const DOT: Record<Tone, string> = {
  neutral: "bg-muted-foreground/40",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
};

/** One sidebar status item: icon, text and live value; its detail and buttons on hover or focus; a click runs its effect. */
function SidebarStatusItem({
  entry,
  updatedAt,
  run,
  compact,
}: {
  entry: SchemaStatusEntry;
  updatedAt: number;
  run: (effect: Effect) => void;
  compact: boolean;
}) {
  const current = useEntry("status", entry, updatedAt);
  const now = useNow(current.item?.live?.kind === "clock" ? 1000 : 30_000);
  const [open, setOpen] = useState(false);
  const closing = useRef<number | undefined>(undefined);
  const detailId = useId();
  // Closed a moment after the pointer leaves, so it can cross the gap up to the detail and its buttons.
  const show = () => {
    window.clearTimeout(closing.current);
    setOpen(true);
  };
  const hide = () => {
    window.clearTimeout(closing.current);
    closing.current = window.setTimeout(() => setOpen(false), 200);
  };
  useEffect(() => () => window.clearTimeout(closing.current), []);
  const item = current.item;
  if (!item) {
    return current.problem ? (
      <span title={current.problem} className="shrink-0 text-muted-foreground/60">
        <CircleAlert aria-label={current.problem} className="size-3.5" />
      </span>
    ) : null;
  }
  const label = [item.text, item.live ? liveText(item.live, now) : ""].filter(Boolean).join(" ");
  const detail = item.detail ?? [];
  const actions = item.actions ?? [];
  const more = detail.length > 0 || actions.length > 0;
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: hover only previews what the focusable button shows
    <span
      className={cn("relative min-w-0", compact ? "block" : "shrink")}
      onMouseEnter={show}
      onMouseLeave={hide}
      onKeyDown={(event) => event.key === "Escape" && setOpen(false)}
    >
      <button
        type="button"
        title={!more ? label : undefined}
        onClick={() => (item.effect ? run(item.effect) : setOpen((was) => !was))}
        onFocus={show}
        aria-expanded={more ? open : undefined}
        aria-describedby={more ? detailId : undefined}
        className={cn(
          "flex min-w-0 items-center gap-1.5 rounded px-1 hover:bg-accent/60",
          compact && "w-full py-1 text-left",
          TONE_TEXT[item.tone],
          !item.effect && !more && "cursor-default",
        )}
      >
        <IconOf icon={item.icon} />
        <span className={cn("truncate", item.live?.kind === "clock" && "font-mono tabular-nums")}>{label}</span>
      </button>
      {more && open && (
        <div
          id={detailId}
          role="tooltip"
          className={cn(
            "absolute z-40 w-72 rounded-xl border border-border bg-popover p-1.5 text-sm text-foreground shadow-xl",
            compact ? "bottom-full left-0 mb-2" : "right-0 bottom-full mb-2",
          )}
        >
          <ul>
            {detail.map((line) => (
              <li key={line.text} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5">
                <span
                  aria-hidden
                  className={cn(
                    "size-2 shrink-0 rounded-full",
                    line.tone === "ok"
                      ? "bg-ok"
                      : line.tone === "warn"
                        ? "bg-warn"
                        : line.tone === "bad"
                          ? "bg-bad"
                          : "bg-muted-foreground/40",
                  )}
                />
                <span className="min-w-0 truncate">{line.text}</span>
              </li>
            ))}
          </ul>
          {actions.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1.5 border-t border-border px-1.5 pt-2 pb-1">
              {actions.map((action) => (
                <Button key={action.label} size="sm" variant="outline" onClick={() => run(action.effect)}>
                  <IconOf icon={action.icon} />
                  {action.label}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}
    </span>
  );
}

/** Contributions the status bar draws itself, with more than the shapes allow (the clock's pinned zones panel,
 * the agenda's translated text); they still follow the person's per-slot choice through `useShown`. */
export const NATIVE_FOOTER = ["clock:primary-zone", "org:meeting"];

/** Whether a contribution shows in a slot: the person's choice, its default, or `fallback` until the list loads. */
export function useShown(id: string, slot: Slot, fallback = true): boolean {
  const list = useQuery({
    queryKey: ["ui", "status"],
    queryFn: () => fetchers.status(),
    refetchInterval: LIST,
    retry: false,
  });
  const entry = list.data?.find((candidate) => candidate.id === id);
  return entry ? Boolean(entry.slots[slot]) : fallback;
}

/** The status items of a slot: in a row for the footer, stacked for the sidebar. */
export function StatusItems({
  slot,
  className,
  except = [],
  stacked = false,
}: {
  slot: "footer" | "sidebar";
  className?: string;
  except?: string[];
  /** One under the other, as in the sidebar, for the footer's overflow. */
  stacked?: boolean;
}) {
  const all = useEntries("status", slot);
  const updatedAt = all.updatedAt;
  const entries = all.entries.filter((entry) => !except.includes(entry.id));
  const { run, dialog } = useEffectRunner();
  if (!entries.length) return null;
  const compact = slot === "sidebar" || stacked;
  return (
    <div
      className={cn(compact ? "flex flex-col gap-0.5 px-2 text-ui-sm" : "flex min-w-0 items-center gap-3", className)}
      data-slot={slot}
    >
      {entries.map((entry) =>
        compact ? (
          <SidebarStatusItem key={entry.id} entry={entry} updatedAt={updatedAt} run={run} compact />
        ) : (
          <FooterStatus key={entry.id} entry={entry} updatedAt={updatedAt} run={run} />
        ),
      )}
      {dialog}
    </div>
  );
}

function ActionButton({
  entry,
  updatedAt,
  run,
}: {
  entry: SchemaActionEntry;
  updatedAt: number;
  run: (effect: Effect) => void;
}) {
  const current = useEntry("actions", entry, updatedAt);
  const Component = ICONS[current.icon];
  const badge = current.badge === 0 || current.badge == null ? null : String(current.badge);
  const effect = current.effect;
  return (
    <Button
      variant="ghost"
      size="icon"
      disabled={!effect}
      onClick={() => effect && run(effect)}
      aria-label={badge ? `${current.label} (${badge})` : current.label}
      title={current.problem ?? current.label}
      className="relative"
    >
      <Component aria-hidden />
      {badge && (
        <span
          aria-hidden
          className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-primary px-1 text-[10px] leading-4 text-primary-foreground"
        >
          {badge}
        </span>
      )}
    </Button>
  );
}

/** The header buttons modules add, before the built-in ones. */
export function HeaderActions() {
  const { entries, updatedAt } = useEntries("actions", "header");
  const { run, dialog } = useEffectRunner();
  if (!entries.length) return null;
  return (
    <>
      {entries.map((entry) => (
        <ActionButton key={entry.id} entry={entry} updatedAt={updatedAt} run={run} />
      ))}
      {dialog}
    </>
  );
}

/** The text of a widget, for "Ask about it". */
export function widgetText(widget: Widget): string {
  switch (widget.kind) {
    case "card":
      return widget.body ? `${widget.title}: ${widget.body}` : widget.title;
    case "list":
      return `${widget.title}: ${widget.items.map((item) => item.text).join("; ")}`;
    case "metric":
      return `${widget.label}: ${widget.value}${widget.delta ? ` (${widget.delta})` : ""}`;
    case "progress":
      return `${widget.label}: ${widget.done} of ${widget.total}`;
    case "graphic":
      return `${widget.label}: ${widget.value}${widget.caption ? ` (${widget.caption})` : ""}`;
  }
}

function WidgetBody({ widget, run }: { widget: Widget; run: (effect: Effect) => void }) {
  switch (widget.kind) {
    case "card":
      return (
        <>
          <h3 className="font-semibold">{widget.title}</h3>
          {widget.body && (
            <p className="line-clamp-3 text-xs whitespace-pre-line text-muted-foreground">{widget.body}</p>
          )}
          {(widget.actions ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {(widget.actions ?? []).map((action) => (
                <Button key={action.label} size="sm" variant="outline" onClick={() => run(action.effect)}>
                  <IconOf icon={action.icon} />
                  {action.label}
                </Button>
              ))}
            </div>
          )}
        </>
      );
    case "list":
      return (
        <>
          <h3 className="font-semibold">{widget.title}</h3>
          <ul className="flex flex-col gap-1 text-xs">
            {widget.items.map((item) => {
              const effect = item.effect;
              const text = (
                <>
                  <span className={cn("block truncate", TONE_TEXT[item.tone])}>{item.text}</span>
                  {item.detail && <span className="block truncate text-muted-foreground">{item.detail}</span>}
                </>
              );
              return (
                <li key={item.text}>
                  {effect ? (
                    <button type="button" onClick={() => run(effect)} className="w-full text-left hover:underline">
                      {text}
                    </button>
                  ) : (
                    text
                  )}
                </li>
              );
            })}
          </ul>
        </>
      );
    case "metric":
      return (
        <>
          <h3 className="text-xs text-muted-foreground">{widget.label}</h3>
          <p className={cn("text-2xl font-semibold tabular-nums", TONE_TEXT[widget.tone])}>{widget.value}</p>
          {widget.delta && <p className="text-xs text-muted-foreground">{widget.delta}</p>}
        </>
      );
    case "progress":
      return (
        <>
          <h3 className="text-xs text-muted-foreground">{widget.label}</h3>
          <progress
            className="h-2 w-full accent-primary"
            max={widget.total}
            value={widget.done}
            aria-label={widget.label}
          />
          <p className="text-xs tabular-nums">{t("contrib.progress", { done: widget.done, total: widget.total })}</p>
        </>
      );
    case "graphic":
      // Data only: a big value beside an icon or a Heinzelmännchen dressed by the closed list of accessories.
      return (
        <div className="flex items-center gap-4">
          {widget.avatar ? (
            <HeinzelAvatar
              view="full"
              label={t("contrib.graphic.heinzel")}
              accessories={widget.avatar.accessories ?? []}
              className="h-28 w-auto max-w-32"
            />
          ) : (
            widget.icon && <IconOf icon={widget.icon} className="size-10 text-muted-foreground" />
          )}
          <div className="min-w-0">
            <h3 className="text-xs text-muted-foreground">{widget.label}</h3>
            <p className={cn("text-3xl font-semibold tabular-nums", TONE_TEXT[widget.tone])}>{widget.value}</p>
            {widget.caption && <p className="text-sm text-muted-foreground">{widget.caption}</p>}
          </div>
        </div>
      );
  }
}

/** How much of the Today grid a widget takes: what it asks for, else what its kind needs. The spans widen with the
 *  grid's own width (container queries), so beside the chat a wide widget never claims a column that isn't there. */
const SIZES = {
  small: "",
  medium: "@[31rem]:col-span-2",
  wide: "@[31rem]:col-span-2 @4xl:col-span-3",
} as const;
const DEFAULT_SIZE: Record<Widget["kind"], keyof typeof SIZES> = {
  metric: "small",
  progress: "small",
  card: "small",
  list: "small",
  graphic: "medium",
};
export const sizeOf = (widget: Widget): keyof typeof SIZES => widget.size ?? DEFAULT_SIZE[widget.kind];

const DISMISSED = "hz.today.widgets.dismissed";

function loadDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(DISMISSED) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

/** A widget's key while what it shows is the same, so a dismissed one comes back when it changes. */
const widgetKey = (entry: SchemaWidgetEntry) => `${entry.id}:${JSON.stringify(entry.widget)}`;

function WidgetCard({
  entry,
  updatedAt,
  run,
  onAsk,
  dismissed,
  onDismiss,
}: {
  entry: SchemaWidgetEntry;
  updatedAt: number;
  run: (effect: Effect) => void;
  onAsk?: (context: string, text: string) => void;
  dismissed: Set<string>;
  onDismiss: (key: string) => void;
}) {
  const current = useEntry("widgets", entry, updatedAt);
  const widget = current.widget;
  if (!widget || dismissed.has(widgetKey(current))) return null;
  const tone = "tone" in widget ? widget.tone : "neutral";
  const title = widget.kind === "card" || widget.kind === "list" ? widget.title : widget.label;
  return (
    <li
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-lg border-l-4 bg-card p-3 text-sm shadow-sm",
        TONE_BORDER[tone],
        SIZES[sizeOf(widget)],
      )}
      data-widget={current.id}
      data-size={sizeOf(widget)}
    >
      <WidgetBody widget={widget} run={run} />
      <div className="mt-auto flex items-center gap-2 pt-1">
        {onAsk && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              onAsk(
                `text:what module ${current.module} shows`,
                t("contrib.says", {
                  module: current.module,
                  text: widgetText(widget),
                }),
              )
            }
          >
            <MessageSquare aria-hidden />
            {t("today.ask")}
          </Button>
        )}
        <Button
          size="icon"
          variant="ghost"
          className="ml-auto"
          onClick={() => onDismiss(widgetKey(current))}
          aria-label={t("today.dismiss", { title })}
          title={t("today.dismiss", { title })}
        >
          <X aria-hidden />
        </Button>
      </div>
    </li>
  );
}

/** The Today widgets modules add, as cards in the Today grid; "Ask about it" frames the text as the module's. */
export function TodayWidgets({ onAsk }: { onAsk?: (context: string, text: string) => void }) {
  const { entries, updatedAt } = useEntries("widgets", "today");
  const { run, dialog } = useEffectRunner();
  const [dismissed, setDismissed] = useState(loadDismissed);
  const dismiss = (key: string) => {
    const next = new Set(dismissed).add(key);
    setDismissed(next);
    try {
      localStorage.setItem(DISMISSED, JSON.stringify([...next].slice(-100)));
    } catch {
      // No storage: dismissed for this visit only.
    }
  };
  // One card per contribution id, whatever the list repeats.
  const unique = [...new Map(entries.map((entry) => [entry.id, entry])).values()];
  if (!unique.length) return null;
  return (
    <>
      {unique.map((entry) => (
        <WidgetCard
          key={entry.id}
          entry={entry}
          updatedAt={updatedAt}
          run={run}
          onAsk={onAsk}
          dismissed={dismissed}
          onDismiss={dismiss}
        />
      ))}
      {/* The confirmation and its live region are not list items: they go beside the grid, not in it. */}
      {createPortal(dialog, document.body)}
    </>
  );
}

const SLOT_LABEL = {
  footer: "contrib.slot.footer",
  sidebar: "contrib.slot.sidebar",
  header: "contrib.slot.header",
  today: "contrib.slot.today",
} as const;

/** "Show in footer", "Show in sidebar", … for every contribution of `module`, written by `hz system ui show|hide`. */
export function ContributionToggles({ module }: { module: string }) {
  const queries = useQueryClient();
  const list = useQuery({
    queryKey: ["ui", "list"],
    queryFn: () => unwrap(api.GET("/system/ui/list")),
    retry: false,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const mine = (list.data ?? []).filter((info) => info.module === module);
  if (!mine.length) return null;
  const toggle = async (id: string, slot: string, shown: boolean) => {
    setBusy(`${id}:${slot}`);
    try {
      const path = shown ? "/system/ui/show" : "/system/ui/hide";
      await unwrap(api.POST(path, { params: { query: { item: id, slot } } }));
      await queries.invalidateQueries({ queryKey: ["ui"] });
    } finally {
      setBusy(null);
    }
  };
  return (
    <section aria-label={t("contrib.title")} className="mt-6">
      <h4 className="mb-1 text-ui font-medium">{t("contrib.title")}</h4>
      <ul>
        {mine.flatMap((info) =>
          Object.entries(info.slots).map(([slot, on]) => {
            const label = `${t(SLOT_LABEL[slot as Slot])}: ${info.title}`;
            return (
              <li key={`${info.id}:${slot}`} className="flex items-center gap-3 py-1.5 text-ui">
                <span className="min-w-0 flex-1 truncate">{label}</span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={on}
                  aria-label={label}
                  disabled={busy === `${info.id}:${slot}`}
                  onClick={() => void toggle(info.id, slot, !on)}
                  className={cn(
                    "relative h-4 w-7 shrink-0 rounded-full transition-colors disabled:opacity-50",
                    on ? "bg-switch" : "bg-accent",
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-0.5 left-0.5 size-3 rounded-full bg-white transition-transform",
                      on && "translate-x-3",
                    )}
                  />
                </button>
              </li>
            );
          }),
        )}
      </ul>
    </section>
  );
}
