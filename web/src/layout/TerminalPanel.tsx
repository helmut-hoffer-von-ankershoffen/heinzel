// A terminal under the chat, as in Claude: the user's login shell on this machine, through the web head's
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

type Tab = { id: number; title: string };
type Props = { onClose: () => void; full: boolean; onToggleFull: () => void };

export function TerminalPanel({ onClose, full, onToggleFull }: Props) {
  const [tabs, setTabs] = useState<Tab[]>([{ id: 1, title: t("terminal.title") }]);
  const [active, setActive] = useState(1);
  const [options, setOptions] = useState<Options>(loadOptions);
  const nextId = useRef(2);
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
    <section aria-label={t("terminal.title")} className="flex h-full min-h-0 flex-col bg-background">
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
                onClick={() => setActive(tab.id)}
                className="px-3.5 py-1"
              >
                {tab.title}
              </button>
              {tabs.length > 1 && (
                <button
                  type="button"
                  onClick={() => close(tab.id)}
                  aria-label={t("terminal.closeTab", { title: tab.title })}
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
          <TerminalView key={tab.id} active={tab.id === active} full={full} options={options} />
        ))}
      </div>
    </section>
  );
}

type State = "connecting" | "open" | "closed";

/** One shell in one tab; it keeps running while another tab is shown. */
function TerminalView({ active, full, options }: { active: boolean; full: boolean; options: Options }) {
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
    };
    socket.onclose = (event) => {
      senderRef.current = null;
      setState("closed");
      setReason(event.reason);
    };
    const typed = terminal.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(encoder.encode(data));
    });
    const selected = terminal.onSelectionChange(() => {
      if (copyOnSelect.current && terminal.hasSelection()) void navigator.clipboard?.writeText(terminal.getSelection());
    });
    return () => {
      follower.dispose();
      refitRef.current = null;
      typed.dispose();
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
        <div
          role="menu"
          className="absolute top-full right-0 z-(--layer-popover) mt-1 w-60 rounded-xl border border-border bg-popover p-1.5 shadow-xl"
        >
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
        </div>
      )}
    </div>
  );
}
