// The centre pane's header: the screen's title with a menu on the left, quick actions on the right.
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Link2, RefreshCw, SquareTerminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { matchPath, useLocation } from "react-router";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { Button } from "@/components/ui/button";
import type { MessageKey } from "@/i18n";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

/** Each screen's title and the `hz` command that shows the same thing in a terminal. */
const screens: { path: string; title: MessageKey; command: string }[] = [
  { path: "/", title: "nav.home", command: "hz system info" },
  { path: "/settings", title: "nav.settings", command: "hz system config show" },
  { path: "/heinzel", title: "nav.heinzelmaennchen", command: "hz heinzel list" },
  { path: "/contracts", title: "nav.graph", command: "hz heinzel graph" },
  { path: "/graph", title: "nav.teamGraph", command: "hz graph" },
  { path: "/org", title: "nav.orgChart", command: "hz org show" },
  { path: "/team", title: "nav.team", command: "hz team show" },
  { path: "/inventory/types", title: "nav.assetTypes", command: "hz heinzel playbooks" },
  { path: "/inventory/assets", title: "nav.assets", command: "hz heinzel discover" },
  { path: "/doctor", title: "nav.doctor", command: "hz system doctor" },
  { path: "/profile", title: "nav.profile", command: "hz org show" },
];

function useScreen(): { title: string; command: string } {
  const { pathname } = useLocation();
  const contract = matchPath("/contracts/:name", pathname);
  if (contract?.params.name) {
    const name = decodeURIComponent(contract.params.name);
    return { title: name, command: `hz heinzel show ${name}` };
  }
  const screen = screens.find((s) => matchPath({ path: s.path, end: true }, pathname));
  return screen ? { title: t(screen.title), command: screen.command } : { title: "", command: "hz --help" };
}

type Props = {
  chatOpen: boolean;
  onToggleChat: () => void;
  terminalOpen: boolean;
  onToggleTerminal: () => void;
};

export function TopBar({ chatOpen, onToggleChat, terminalOpen, onToggleTerminal }: Props) {
  const { title, command } = useScreen();
  const queries = useQueryClient();
  const [menu, setMenu] = useState(false);
  const [copied, setCopied] = useState<"command" | "link" | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenu(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setMenu(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);
  const copy = async (what: "command" | "link") => {
    // The link never carries the session token: the fragment stays out of it.
    const text = what === "command" ? command : `${window.location.origin}${window.location.pathname}`;
    await navigator.clipboard.writeText(text);
    setCopied(what);
    setMenu(false);
    window.setTimeout(() => setCopied(null), 1500);
  };
  const refresh = () => {
    void queries.invalidateQueries();
    setMenu(false);
  };
  return (
    // The sidebar's top line has the same height and hairline, so both run across the window as one.
    <header className="flex h-12 shrink-0 items-center gap-1 border-b-[0.5px] border-foreground/20 px-3">
      <div ref={menuRef} className="relative min-w-0">
        <button
          type="button"
          onClick={() => setMenu((open) => !open)}
          aria-haspopup="menu"
          aria-expanded={menu}
          className="flex max-w-full items-center gap-1.5 rounded-lg px-2 py-1 text-ui font-medium transition-colors hover:bg-accent/70"
        >
          <span className="truncate">{title}</span>
          <ChevronDown aria-hidden className="size-4 shrink-0 text-icon" />
        </button>
        {menu && (
          <div
            role="menu"
            className="absolute top-full left-0 z-20 mt-1 w-64 rounded-xl border border-border bg-card p-1 shadow-lg"
          >
            <MenuItem
              onSelect={() => copy("command")}
              icon={copied === "command" ? <Check aria-hidden /> : <SquareTerminal aria-hidden />}
            >
              {t("top.copyCommand")}
              <code className="ml-auto truncate font-mono text-xs text-muted-foreground">{command}</code>
            </MenuItem>
            <MenuItem onSelect={() => copy("link")} icon={<Link2 aria-hidden />}>
              {t("top.copyLink")}
            </MenuItem>
            <MenuItem onSelect={refresh} icon={<RefreshCw aria-hidden />}>
              {t("top.refresh")}
            </MenuItem>
          </div>
        )}
      </div>
      <div className="ml-auto flex items-center gap-0.5 text-icon">
        <Button variant="ghost" size="icon" onClick={refresh} aria-label={t("top.refresh")} title={t("top.refresh")}>
          <RefreshCw aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleTerminal}
          aria-pressed={terminalOpen}
          aria-label={terminalOpen ? t("terminal.close") : t("terminal.open")}
          title={terminalOpen ? t("terminal.close") : t("terminal.open")}
          className={cn(terminalOpen && "bg-accent text-accent-foreground")}
        >
          <SquareTerminal aria-hidden />
        </Button>
        {/* The chat is a conversation with your Heinzelmännchen, so its button is a Heinzelmännchen head (not the sidebar's logo); open shows as a dot under it, never a background. */}
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleChat}
          aria-expanded={chatOpen}
          aria-pressed={chatOpen}
          aria-label={chatOpen ? t("chat.collapse") : t("chat.expand")}
          title={chatOpen ? t("chat.collapse") : t("chat.expand")}
          className="relative hover:bg-transparent hover:opacity-70"
        >
          <HeinzelAvatar view="head" age="young" shirt="teal" className="size-[1.4em] text-lg" />
          <span
            aria-hidden
            data-testid="chat-open-dot"
            className={cn(
              "absolute bottom-0.5 left-1/2 size-1 -translate-x-1/2 rounded-full bg-primary transition-opacity",
              chatOpen ? "opacity-100" : "opacity-0",
            )}
          />
        </Button>
      </div>
      <span className="sr-only" aria-live="polite">
        {copied && t("command.copied")}
      </span>
    </header>
  );
}

function MenuItem({
  onSelect,
  icon,
  children,
}: {
  onSelect: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onSelect}
      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-icon"
    >
      {icon}
      {children}
    </button>
  );
}
