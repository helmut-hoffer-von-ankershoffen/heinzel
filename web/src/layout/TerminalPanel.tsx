import { AnchoredPopover } from "@/components/AnchoredPopover";
// A terminal workspace tab: the user's login shell on this machine, through the web head's
// /api/terminal WebSocket (ADR 0048). Tabs each run their own shell and stay alive while hidden; full screen covers the
// screen and the chat. Closing a tab or the panel hangs its shell up.
import "@xterm/xterm/css/xterm.css";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { Check, ChevronRight, Ellipsis, Maximize2, Minimize2, Plus, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { token } from "@/api/token";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { connectTerminal, followSize } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import { desktopState } from "@/notifications/desktop";

/** The palette of the dark theme, so the terminal reads as part of the app. */
const theme = {
  background: "#181818",
  foreground: "#ececec",
  cursor: "#d97757",
  cursorAccent: "#181818",
  selectionBackground: "#d9775755",
  black: "#272727",
  brightBlack: "#6b6b6b",
};

type CursorStyle = "block" | "bar" | "underline";
type Options = { cursorStyle: CursorStyle; cursorBlink: boolean; copyOnSelect: boolean; scrollback: number };
const DEFAULTS: Options = { cursorStyle: "block", cursorBlink: true, copyOnSelect: false, scrollback: 10_000 };
const OPTIONS = "hz.terminal.options";

function loadOptions(): Options {
  try {
    return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(OPTIONS) ?? "{}") as Partial<Options>) };
  } catch {
    return DEFAULTS;
  }
}

type Tab = { id: number; title: string; agentTitle?: string; notice?: string; status?: string };
type Props = { onClose: () => void; full: boolean; onToggleFull: () => void; onActivate?: () => void };

export function TerminalPanel({ onClose, full, onToggleFull, onActivate }: Props) {
  const [tabs, setTabs] = useState<Tab[]>([{ id: 1, title: t("terminal.title") }]);
  const [active, setActive] = useState(1);
  const [options, setOptions] = useState<Options>(loadOptions);
  const nextId = useRef(2);
  const panelRef = useRef<HTMLElement>(null);
  const change = (patch: Partial<Options>) =>
    setOptions((current) => {
      const next = { ...current, ...patch };
      try {
        localStorage.setItem(OPTIONS, JSON.stringify(next));
      } catch {
        // Not remembered.
      }
      return next;
    });
  const add = () => {
    const id = nextId.current++;
    setTabs((current) => [...current, { id, title: `${t("terminal.title")} ${id}` }]);
    setActive(id);
  };
  const updateTab = (id: number, patch: Partial<Tab>) =>
    setTabs((current) => current.map((tab) => (tab.id === id ? { ...tab, ...patch } : tab)));
  const close = (id: number) => {
    const rest = tabs.filter((tab) => tab.id !== id);
    const last = rest.at(-1);
    if (!last) {
      onClose();
      return;
    }
    setTabs(rest);
    if (active === id) setActive(last.id);
  };
  return (
    <section ref={panelRef} aria-label={t("terminal.title")} className="flex h-full min-h-0 flex-col bg-background">
      <div className="flex h-10 shrink-0 items-center gap-1 px-3">
        <div
          role="tablist"
          aria-label={t("terminal.tabs")}
          className="flex min-w-0 items-center gap-0.5 overflow-x-auto"
        >
          {tabs.map((tab) => (
            <div
              key={tab.id}
              className={cn(
                "flex shrink-0 items-center rounded-lg text-sm",
                tab.id === active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60",
              )}
            >
              <button
                type="button"
                role="tab"
                aria-selected={tab.id === active}
                onClick={() => {
                  setActive(tab.id);
                  updateTab(tab.id, { notice: undefined });
                }}
                className="px-3.5 py-1"
              >
                {tab.agentTitle || tab.title}
                {tab.status && <span className="ml-2 text-xs text-muted-foreground">{tab.status}</span>}
                {tab.notice && (
                  <span
                    role="img"
                    aria-label={t("terminal.attention")}
                    title={tab.notice}
                    className="ml-2 inline-block size-2 rounded-full bg-primary"
                  />
                )}
              </button>
              {tabs.length > 1 && (
                <button
                  type="button"
                  onClick={() => close(tab.id)}
                  aria-label={t("terminal.closeTab", { title: tab.agentTitle || tab.title })}
                  className="mr-1 rounded p-0.5 opacity-60 hover:bg-background/40 hover:opacity-100"
                >
                  <X aria-hidden className="size-3" />
                </button>
              )}
            </div>
          ))}
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={add}
          aria-label={t("terminal.newTab")}
          title={t("terminal.newTab")}
        >
          <Plus aria-hidden />
        </Button>
        <div className="ml-auto flex items-center text-muted-foreground">
          <OptionsMenu options={options} onChange={change} />
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleFull}
            aria-pressed={full}
            aria-label={full ? t("terminal.exitFull") : t("terminal.full")}
            title={full ? t("terminal.exitFull") : t("terminal.full")}
          >
            {full ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label={t("terminal.close")}
            title={t("terminal.close")}
          >
            <X aria-hidden />
          </Button>
        </div>
      </div>
      <div className="relative min-h-0 flex-1">
        {tabs.map((tab) => (
          <TerminalView
            key={tab.id}
            active={tab.id === active}
            full={full}
            options={options}
            onTitle={(title) =>
              updateTab(tab.id, {
                title: title || (tab.id === 1 ? t("terminal.title") : `${t("terminal.title")} ${tab.id}`),
              })
            }
            onAgentTitle={(agentTitle) => updateTab(tab.id, { agentTitle })}
            onNotice={(notice) => {
              updateTab(tab.id, { notice });
              const hidden = tab.id !== active || !document.hasFocus() || !!panelRef.current?.closest("[hidden]");
              if (hidden && desktopState() === "on") {
                try {
                  const notification = new Notification(tab.agentTitle || tab.title, { body: notice });
                  notification.onclick = () => {
                    window.focus();
                    onActivate?.();
                    setActive(tab.id);
                    updateTab(tab.id, { notice: undefined });
                    notification.close();
                  };
                } catch {
                  // The in-app indicator remains available when desktop delivery fails.
                }
              }
            }}
            onStatus={(status) =>
              updateTab(tab.id, {
                status,
                ...(/^(waiting|blocked)$/i.test(status.trim()) ? { notice: status } : {}),
              })
            }
          />
        ))}
      </div>
    </section>
  );
}

type State = "connecting" | "open" | "closed";

/** One shell in one tab; it keeps running while another tab is shown. */
function TerminalView({
  active,
  full,
  options,
  onTitle,
  onAgentTitle,
  onNotice,
  onStatus,
}: {
  active: boolean;
  full: boolean;
  options: Options;
  onTitle: (title: string) => void;
  onAgentTitle: (title: string | undefined) => void;
  onNotice: (notice: string) => void;
  onStatus: (status: string) => void;
}) {
  const callbacks = useRef({ onTitle, onAgentTitle, onNotice, onStatus });
  callbacks.current = { onTitle, onAgentTitle, onNotice, onStatus };
  const host = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const refitRef = useRef<(() => void) | null>(null);
  const senderRef = useRef<((text: string) => void) | null>(null);
  const copyOnSelect = useRef(options.copyOnSelect);
  const initial = useRef(options);
  const [state, setState] = useState<State>("connecting");
  const [reason, setReason] = useState("");
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    // A new shell per generation; "Restart" bumps it.
    void generation;
    const { copyOnSelect: _copy, ...settings } = initial.current;
    const terminal = new Terminal({
      theme,
      fontFamily: 'ui-monospace, "SF Mono", Menlo, Monaco, monospace',
      fontSize: 13,
      lineHeight: 1.2,
      allowProposedApi: false,
      ...settings,
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(element);
    terminalRef.current = terminal;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/api/terminal`);
    socket.binaryType = "arraybuffer";
    const encoder = new TextEncoder();
    const follower = followSize(element, terminal, fit, (cols, rows) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ resize: [cols, rows] }));
    });
    refitRef.current = follower.refit;
    socket.onopen = () => {
      socket.send(JSON.stringify({ token: token() ?? "" }));
      senderRef.current = (text) => socket.send(encoder.encode(text));
      setState("open");
      follower.refit();
    };
    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) terminal.write(new Uint8Array(event.data));
      else if (typeof event.data === "string") {
        try {
          const message: unknown = JSON.parse(event.data);
          if (typeof message === "object" && message !== null && "agent_title" in message) {
            const title: unknown = message.agent_title;
            if (title === null || typeof title === "string") callbacks.current.onAgentTitle(title ?? undefined);
          }
        } catch {
          // Ignore malformed control messages.
        }
      }
    };
    socket.onclose = (event) => {
      senderRef.current = null;
      callbacks.current.onStatus("");
      callbacks.current.onTitle("");
      callbacks.current.onAgentTitle(undefined);
      setState("closed");
      setReason(event.reason);
    };
    const typed = terminal.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(encoder.encode(data));
    });
    const titled = terminal.onTitleChange((title) =>
      callbacks.current.onTitle(
        Array.from(title)
          .filter((char) => {
            const code = char.codePointAt(0) ?? 0;
            return (
              code >= 32 &&
              !(code >= 127 && code <= 159) &&
              ![0x200e, 0x200f].includes(code) &&
              !(code >= 0x202a && code <= 0x202e) &&
              !(code >= 0x2066 && code <= 0x2069)
            );
          })
          .join("")
          .trim()
          .slice(0, 240),
      ),
    );
    const notice9 = terminal.parser.registerOscHandler(9, (message) => {
      if (!message || /^\d+;/.test(message)) return false;
      callbacks.current.onNotice(message.slice(0, 240));
      return true;
    });
    const notice777 = terminal.parser.registerOscHandler(777, (message) => {
      if (!message.startsWith("notify;")) return false;
      callbacks.current.onNotice(message.slice(7, 247).replace(";", ": "));
      return true;
    });
    let programState = "";
    const programRecords = new Map<string, string>();
    const program = terminal.parser.registerOscHandler(7501, (message) => {
      if (message === "?") {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(
            encoder.encode("\x1b]7501;?:states=idle,working,done,blocked,error:kinds=permission,question,auth\x1b\\"),
          );
        }
        return true;
      }
      if (message.length > 4096) return false;
      const fields = Object.fromEntries(
        message.split(":").map((entry) => {
          const split = entry.indexOf("=");
          return split < 0 ? ["", ""] : [entry.slice(0, split), entry.slice(split + 1)];
        }),
      );
      const id = fields.id ?? "";
      if (id.length > 128 || (id && !/^[A-Za-z0-9_.+-]{1,32}(\/[A-Za-z0-9_.+-]{1,32}){0,7}$/.test(id))) return false;
      if (!["idle", "working", "done", "blocked", "error", "clear"].includes(fields.state ?? "")) return false;
      const state = fields.state ?? "";
      if (state === "clear") {
        for (const key of programRecords.keys())
          if (!id || key === id || key.startsWith(`${id}/`)) programRecords.delete(key);
      } else {
        programRecords.delete(id);
        programRecords.set(id, state);
        if (programRecords.size > 256) programRecords.delete(programRecords.keys().next().value ?? "");
      }
      const priority = ["blocked", "error", "working", "done", "idle"];
      const shown = priority.find((candidate) => [...programRecords.values()].includes(candidate)) ?? "";
      if (shown !== programState) {
        programState = shown;
        const labels: Record<string, string> = {
          idle: t("terminal.agent.idle"),
          working: t("terminal.agent.working"),
          done: t("terminal.agent.done"),
          blocked: t("terminal.agent.blocked"),
          error: t("terminal.agent.error"),
          clear: "",
        };
        callbacks.current.onStatus(labels[shown] ?? "");
        if (shown === "blocked") callbacks.current.onNotice(t("terminal.agent.blocked"));
      }
      return true;
    });
    const status = terminal.parser.registerOscHandler(21337, (message) => {
      const field = message.split(";").find((part) => part.startsWith("status="));
      if (field !== undefined) callbacks.current.onStatus(field.slice(7, 107));
      return true;
    });
    const prompt = terminal.parser.registerOscHandler(133, (message) => {
      if (message === "A" || message.startsWith("A;")) {
        for (const [id, value] of programRecords) {
          if (value === "working" || value === "blocked") programRecords.delete(id);
        }
        programState = "";
        callbacks.current.onStatus("");
      }
      return false;
    });
    const selected = terminal.onSelectionChange(() => {
      if (copyOnSelect.current && terminal.hasSelection()) void navigator.clipboard?.writeText(terminal.getSelection());
    });
    return () => {
      follower.dispose();
      refitRef.current = null;
      typed.dispose();
      titled.dispose();
      notice9.dispose();
      notice777.dispose();
      program.dispose();
      status.dispose();
      prompt.dispose();
      selected.dispose();
      socket.onclose = null;
      senderRef.current = null;
      socket.close();
      terminal.dispose();
    };
  }, [generation]);
  // Options apply to the running terminal; copy on select is read when text is selected.
  useEffect(() => {
    copyOnSelect.current = options.copyOnSelect;
    const terminal = terminalRef.current;
    if (!terminal) return;
    terminal.options.cursorStyle = options.cursorStyle;
    terminal.options.cursorBlink = options.cursorBlink;
    terminal.options.scrollback = options.scrollback;
  }, [options]);
  // Full screen changes the container's size: measure again.
  useEffect(() => {
    void full;
    if (active) refitRef.current?.();
  }, [full, active]);
  // The shown tab receives "Run in the terminal" and gets the focus.
  useEffect(() => {
    if (!active || state !== "open") return;
    refitRef.current?.();
    terminalRef.current?.focus();
    connectTerminal((text) => senderRef.current?.(text));
    return () => connectTerminal(null);
  }, [active, state]);
  return (
    <div className={cn("absolute inset-0 flex flex-col", !active && "hidden")}>
      {state === "closed" && (
        <div className="flex shrink-0 items-center gap-2 px-4 py-1 text-xs text-muted-foreground">
          <span className="truncate">{reason || t("terminal.ended")}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6"
            onClick={() => {
              setState("connecting");
              setReason("");
              setGeneration((n) => n + 1);
            }}
          >
            <RotateCcw aria-hidden />
            {t("terminal.restart")}
          </Button>
        </div>
      )}
      <div ref={host} className="min-h-0 flex-1 px-3 pb-1" />
    </div>
  );
}

const SCROLLBACK = [1_000, 10_000, 100_000];
const CURSORS: CursorStyle[] = ["block", "bar", "underline"];

/** Cursor style, blink, copy on select and scrollback, remembered in this browser. */
function OptionsMenu({ options, onChange }: { options: Options; onChange: (patch: Partial<Options>) => void }) {
  const [open, setOpen] = useState(false);
  const [sub, setSub] = useState<"cursor" | "scrollback" | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const row =
    "flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left text-sm text-foreground/90 hover:bg-accent [&_svg]:size-4";
  const tick = <Check aria-hidden className="ml-auto text-switch" />;
  return (
    <div ref={ref} className="relative">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => {
          setOpen((value) => !value);
          setSub(null);
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("terminal.options")}
        title={t("terminal.options")}
      >
        <Ellipsis aria-hidden />
      </Button>
      {open && (
        <AnchoredPopover anchor={ref} role="menu" className="w-60">
          <button
            type="button"
            role="menuitem"
            className={row}
            onClick={() => setSub(sub === "cursor" ? null : "cursor")}
          >
            {t("terminal.cursorStyle")}
            <span className="ml-auto text-muted-foreground capitalize">{options.cursorStyle}</span>
            <ChevronRight aria-hidden className="text-icon" />
          </button>
          {sub === "cursor" &&
            CURSORS.map((style) => (
              <button
                key={style}
                type="button"
                role="menuitemradio"
                aria-checked={options.cursorStyle === style}
                className={cn(row, "pl-6 capitalize")}
                onClick={() => onChange({ cursorStyle: style })}
              >
                {style}
                {options.cursorStyle === style && tick}
              </button>
            ))}
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={options.cursorBlink}
            className={row}
            onClick={() => onChange({ cursorBlink: !options.cursorBlink })}
          >
            {t("terminal.blink")}
            {options.cursorBlink && tick}
          </button>
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={options.copyOnSelect}
            className={row}
            onClick={() => onChange({ copyOnSelect: !options.copyOnSelect })}
          >
            {t("terminal.copyOnSelect")}
            {options.copyOnSelect && tick}
          </button>
          <button
            type="button"
            role="menuitem"
            className={row}
            onClick={() => setSub(sub === "scrollback" ? null : "scrollback")}
          >
            {t("terminal.scrollback")}
            <span className="ml-auto text-muted-foreground">
              {t("terminal.lines", { lines: options.scrollback.toLocaleString() })}
            </span>
            <ChevronRight aria-hidden className="text-icon" />
          </button>
          {sub === "scrollback" &&
            SCROLLBACK.map((lines) => (
              <button
                key={lines}
                type="button"
                role="menuitemradio"
                aria-checked={options.scrollback === lines}
                className={cn(row, "pl-6")}
                onClick={() => onChange({ scrollback: lines })}
              >
                {t("terminal.lines", { lines: lines.toLocaleString() })}
                {options.scrollback === lines && tick}
              </button>
            ))}
        </AnchoredPopover>
      )}
    </div>
  );
}
