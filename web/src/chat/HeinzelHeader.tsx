// The chat's header when every chat is with a Heinzel (flag `one_chat`, canvas boards ChatDefault and Switch): the
// face, the name with ▾ that opens your Heinzels and New Heinzel, More (search, details, earlier chats) and Close.
// There is no "Back to chat": switching Heinzels is the only way between chats. Both popovers keep the keyboard path of
// the session switcher: Escape closes and focus returns to the button that opened them; Tab out closes too.
import { ChevronDown, Ellipsis, History, PanelRightClose, Plus, Search, SlidersHorizontal } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useHeinzelSessions } from "@/api/queries";
import { AnchoredPopover } from "@/components/AnchoredPopover";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import type { HeinzelLook } from "@/components/heinzel";
import { Button } from "@/components/ui/button";
import { appearanceLook, stateLabel } from "@/heinzel/look";
import { t } from "@/i18n";
import { clearChatLink, createDraftPath } from "@/lib/chatIntent";
import { cn } from "@/lib/utils";

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
  onEarlier: () => void;
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
  onEarlier,
  onClose,
  locked = false,
}: Props) {
  const [switchOpen, setSwitchOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const switchTrigger = useRef<HTMLButtonElement>(null);
  const moreTrigger = useRef<HTMLButtonElement>(null);
  const sessions = useHeinzelSessions(switchOpen);
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
            aria-label={t("oneChat.switch", { name })}
            className="flex h-10 min-w-0 items-center gap-2 rounded-lg px-1.5 text-left hover:bg-accent/70 disabled:opacity-50"
          >
            <HeinzelAvatar
              view="bust"
              {...look}
              state={busy ? "thinking" : undefined}
              className="size-8 overflow-hidden"
            />
            <span className="truncate text-[15px] font-medium">{name}</span>
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
                <HeinzelAvatar
                  view="bust"
                  {...appearanceLook(item.appearance)}
                  className="size-7 shrink-0 overflow-hidden"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{item.name}</span>
                  <span className="block truncate text-ui-sm text-muted-foreground">{stateLabel(item.state)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          className={cn(rowClass, "mt-1 border-t border-border")}
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
        <button type="button" className={rowClass} disabled={locked} onClick={pick(onEarlier)}>
          <History aria-hidden className="size-4" />
          {t("oneChat.earlier")}
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
