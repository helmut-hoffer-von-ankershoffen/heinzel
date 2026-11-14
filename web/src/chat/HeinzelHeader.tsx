// The chat's header, and every chat is with a Heinzel (ADR 0157, canvas boards ChatDefault and Switch): the face, the
// name with ▾ that opens your Heinzels and New Heinzel, More (search, details) and Close.
// There is no "Back to chat": switching Heinzels is the only way between chats. Both popovers keep the keyboard path of
// the session switcher: Escape closes and focus returns to the button that opened them; Tab out closes too.
import { ChevronDown, Ellipsis, PanelRightClose, Plus, Search, SlidersHorizontal } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useHeinzelSessions } from "@/api/queries";
import { AnchoredPopover } from "@/components/AnchoredPopover";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import type { HeinzelLook } from "@/components/heinzel";
import { Button } from "@/components/ui/button";
import { ActivityFace } from "@/heinzel/ActivityFace";
import { activityPose, activityWord, sessionWord } from "@/heinzel/activity";
import { t } from "@/i18n";
import { clearChatLink, createDraftPath } from "@/lib/chatIntent";
import { cn } from "@/lib/utils";

const ACTIVITY_POLL_MS = 20_000;

type Props = {
  sessionId: string;
  name: string;
  look: HeinzelLook;
  /** A turn is on its way: the face thinks. At rest the face is awake in an open chat (owner default). */
  busy: boolean;
  searchOpen: boolean;
  detailsOpen: boolean;
  onSearch: () => void;
  onDetails: () => void;
  onClose: () => void;
  /** Switching is held while a profile save is in flight, as the old "Back to chat" was. */
  locked?: boolean;
};

/** Close a popover on a press outside it, and on Escape inside it with focus back on its trigger. */
function useDismiss(open: boolean, setOpen: (open: boolean) => void, box: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", outside);
    return () => window.removeEventListener("mousedown", outside);
  }, [open, setOpen, box]);
}

function Menu({
  open,
  setOpen,
  trigger,
  label,
  children,
  button,
}: {
  open: boolean;
  setOpen: (open: boolean) => void;
  trigger: RefObject<HTMLButtonElement | null>;
  label: string;
  children: ReactNode;
  button: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  useDismiss(open, setOpen, box);
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: Escape anywhere inside closes the list, as menus do
    <div
      ref={box}
      className="relative flex min-w-0 items-center"
      // Tab out of the list closes it, as Escape does, so the keyboard never leaves a menu open behind it.
      onBlur={(event) => {
        if (open && event.relatedTarget && !box.current?.contains(event.relatedTarget as Node)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      {button}
      {open && (
        <AnchoredPopover focusFirst anchor={trigger} role="dialog" aria-label={label} className="w-72">
          {children}
        </AnchoredPopover>
      )}
    </div>
  );
}

const rowClass =
  "flex min-h-11 w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-ui hover:bg-accent [&_svg]:text-icon";

export function HeinzelHeader({
  sessionId,
  name,
  look,
  busy,
  searchOpen,
  detailsOpen,
  onSearch,
  onDetails,
  onClose,
  locked = false,
}: Props) {
  const [switchOpen, setSwitchOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const switchTrigger = useRef<HTMLButtonElement>(null);
  const moreTrigger = useRef<HTMLButtonElement>(null);
  // The state word follows the backend while the chat stays open: asked again every 20 s and when a turn starts or ends.
  const sessions = useHeinzelSessions(true, ACTIVITY_POLL_MS);
  const { refetch } = sessions;
  // Opening the list asks again: a Heinzel made elsewhere (the Heinzels page, the CLI) shows at once.
  useEffect(() => {
    if (switchOpen) void refetch();
  }, [switchOpen, refetch]);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const heinzels = (sessions.data?.sessions ?? []).filter(
    (item) => item.state !== "retired" || item.session_id === sessionId,
  );
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (wasBusy.current !== busy) void refetch();
    wasBusy.current = busy;
  }, [busy, refetch]);
  const current = (sessions.data?.sessions ?? []).find((item) => item.session_id === sessionId);
  // A turn on its way is work: face and word say so together, whatever the list last said about a quiet Heinzel.
  const quiet = !current || ["idle", "asleep", "proud"].includes(current.activity);
  const shown = busy && quiet ? "working" : current?.activity;
  const word = busy && quiet ? activityWord("working") : current ? sessionWord(current) : activityWord(undefined);
  const choose = (id: string) => {
    setSwitchOpen(false);
    switchTrigger.current?.focus();
    if (id === sessionId) return;
    setParams((current) => {
      const next = new URLSearchParams(current);
      clearChatLink(next);
      next.set("chat", id);
      return next;
    });
  };
  const pick = (action: () => void) => () => {
    setMoreOpen(false);
    moreTrigger.current?.focus();
    action();
  };
  return (
    <div className="chat-panel-header flex h-12 shrink-0 items-center gap-0.5 border-b border-border px-2">
      <h2 className="sr-only">{name}</h2>
      <Menu
        open={switchOpen}
        setOpen={setSwitchOpen}
        trigger={switchTrigger}
        label={t("oneChat.heinzels")}
        button={
          <button
            ref={switchTrigger}
            type="button"
            data-pane-action
            disabled={locked}
            onClick={() => setSwitchOpen((value) => !value)}
            aria-haspopup="dialog"
            aria-expanded={switchOpen}
            // The name starts with what the button shows, the name and the word (WCAG 2.5.3, label in name).
            aria-label={t("oneChat.switch", { name, word })}
            data-activity={shown ?? "idle"}
            className="flex h-11 min-w-0 items-center gap-2 rounded-lg px-1.5 text-left hover:bg-accent/70 disabled:opacity-50"
          >
            <HeinzelAvatar view="bust" {...look} state={activityPose(shown, true)} className="size-8 overflow-hidden" />
            <span className="min-w-0">
              <span className="block truncate text-[15px] leading-tight font-medium">{name}</span>
              {/* A real space between the name and the word, so the shown text reads "Helmi Idle", not "HelmiIdle". */}{" "}
              <span
                id={`${sessionId}-activity`}
                className="block truncate text-ui-sm leading-tight text-muted-foreground"
              >
                {word}
              </span>
            </span>
            <ChevronDown aria-hidden className="size-3.5 shrink-0 text-icon" />
          </button>
        }
      >
        <h3 className="px-2.5 pt-1 pb-1 text-ui-sm text-muted-foreground">{t("oneChat.heinzels")}</h3>
        {sessions.isPending && <p className="px-2.5 py-1.5 text-ui-sm text-muted-foreground">{t("state.loading")}</p>}
        {sessions.isError && (
          <p role="status" className="px-2.5 py-1.5 text-ui-sm">
            {t("oneChat.loadError")}
          </p>
        )}
        <ul className="max-h-80 overflow-y-auto">
          {heinzels.map((item) => (
            <li key={item.session_id}>
              <button
                type="button"
                onClick={() => choose(item.session_id)}
                aria-current={item.session_id === sessionId || undefined}
                className={cn(rowClass, item.session_id === sessionId && "bg-accent text-accent-foreground")}
              >
                <ActivityFace
                  appearance={item.appearance}
                  activity={item.activity}
                  awake={item.session_id === sessionId}
                  className="size-7 shrink-0 overflow-hidden"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{item.name}</span>
                  <span className="block truncate text-ui-sm text-muted-foreground">{sessionWord(item)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        <hr className="my-1 border-border" />
        <button
          type="button"
          className={rowClass}
          onClick={() => {
            setSwitchOpen(false);
            navigate(createDraftPath(params));
          }}
        >
          <Plus aria-hidden className="size-4" />
          {t("oneChat.new")}
        </button>
      </Menu>
      <span className="flex-1" />
      <Menu
        open={moreOpen}
        setOpen={setMoreOpen}
        trigger={moreTrigger}
        label={t("oneChat.more")}
        button={
          <Button
            ref={moreTrigger}
            type="button"
            variant="ghost"
            size="icon"
            data-pane-action
            aria-haspopup="dialog"
            aria-expanded={moreOpen}
            aria-label={t("oneChat.moreLabel")}
            title={t("oneChat.more")}
            onClick={() => setMoreOpen((value) => !value)}
          >
            <Ellipsis aria-hidden />
          </Button>
        }
      >
        <button type="button" className={rowClass} aria-pressed={searchOpen} onClick={pick(onSearch)}>
          <Search aria-hidden className="size-4" />
          {t("oneChat.search")}
        </button>
        <button type="button" className={rowClass} aria-pressed={detailsOpen} onClick={pick(onDetails)}>
          <SlidersHorizontal aria-hidden className="size-4" />
          {t("oneChat.details")}
        </button>
      </Menu>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        data-pane-action
        onClick={onClose}
        aria-label={t("chat.collapse")}
        title={t("chat.collapse")}
      >
        <PanelRightClose aria-hidden />
      </Button>
    </div>
  );
}
