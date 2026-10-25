// Navigation on the left, the screen in the middle, and one panel with the chat and the terminal (ADR 0039, 0045):
// beside the screen when the window leaves it at least 30rem, below it otherwise; never both at once.
import { type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { Outlet, useLocation, useSearchParams } from "react-router";
import { ChatProvider } from "@/chat/ChatContext";
import { t } from "@/i18n";
import { OPEN_TERMINAL } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import { useNoticeStream } from "@/notifications/useNoticeStream";
import { Onboarding } from "@/onboarding/OnboardingWizard";
import { ChatPanel } from "./ChatPanel";
import { InfoDialog, type Panel } from "./InfoDialog";
import { NotificationPanel } from "./NotificationCenter";
import { SearchPalette } from "./SearchPalette";
import { SettingsDialog } from "./SettingsDialog";
import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import { TerminalPanel } from "./TerminalPanel";
import { ThemeTokens } from "./ThemeTokens";
import { TopBar } from "./TopBar";

/** A pane's open state, remembered in this browser; without storage it starts from `initial` each time. */
function useRemembered(
  key: string,
  initial: () => boolean,
): [boolean, (change: boolean | ((open: boolean) => boolean)) => void] {
  const [open, setOpen] = useState(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved !== null) return saved === "true";
    } catch {
      // No storage: the default below.
    }
    return initial();
  });
  const update = useCallback(
    (change: boolean | ((open: boolean) => boolean)) =>
      setOpen((current) => {
        const next = typeof change === "function" ? change(current) : change;
        try {
          localStorage.setItem(key, String(next));
        } catch {
          // Not remembered.
        }
        return next;
      }),
    [key],
  );
  return [open, update];
}

/** The panel's width beside the screen (`w-96`) and the least the screen keeps next to it, in rem. */
const PANEL_REM = 24;
const MAIN_REM = 30;

/** Whether the panel fits beside the screen in `area`: the area leaves the screen at least 30rem next to it. */
export function fitsBeside(areaPx: number, remPx: number): boolean {
  return areaPx >= (PANEL_REM + MAIN_REM) * remPx;
}

/** The width of `ref`'s element as it changes; the window's until it is measured (and where nothing measures). */
function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const element = ref.current;
    const measure = () => setWidth(element?.getBoundingClientRect().width || window.innerWidth);
    measure();
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" || !element ? null : new ResizeObserver(measure);
    if (element) observer?.observe(element);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [ref]);
  return width;
}

export function Shell() {
  return (
    <ChatProvider>
      <Layout />
    </ChatProvider>
  );
}

function Layout() {
  // Narrow windows start with the navigation as a rail, so the screen keeps its room.
  const [navOpen, setNavOpen] = useRemembered("hz.nav.open", () => window.innerWidth >= 1024);
  const [chatOpen, setChatOpen] = useRemembered("hz.chat.open", () => true);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalFull, setTerminalFull] = useState(false);
  // Getting narrower folds the navigation; getting wider leaves it as the person set it. The chat moves below instead.
  useEffect(() => {
    const fold = () => {
      if (window.innerWidth < 1024) setNavOpen(false);
    };
    window.addEventListener("resize", fold);
    return () => window.removeEventListener("resize", fold);
  }, [setNavOpen]);
  // A command chip's "Run" opens the terminal under the chat.
  useEffect(() => {
    const open = () => {
      setChatOpen(true);
      setTerminalOpen(true);
    };
    window.addEventListener(OPEN_TERMINAL, open);
    return () => window.removeEventListener(OPEN_TERMINAL, open);
  }, [setChatOpen]);
  const [searchOpen, setSearchOpen] = useState(false);
  useNoticeStream();
  // Home is where the chat is (ADR 0045): going there opens it, beside Today or below it.
  const home = useLocation().pathname === "/";
  useEffect(() => {
    if (home) setChatOpen(true);
  }, [home, setChatOpen]);
  const area = useRef<HTMLDivElement>(null);
  const rem = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const beside = fitsBeside(useWidth(area), rem);
  const full = terminalOpen && terminalFull;
  // Settings is a dialog over the screen, open while the URL has `?settings=<section>`, so links can point into it.
  const [params, setParams] = useSearchParams();
  const settings = params.get("settings");
  const setSettings = (section: string | null) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (section === null) {
        next.delete("settings");
        next.delete("q");
      } else next.set("settings", section);
      return next;
    });
  useEffect(() => {
    const open = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
      if ((event.metaKey || event.ctrlKey) && event.key === ",") {
        event.preventDefault();
        setParams((current) => new URLSearchParams([...current, ["settings", ""]]));
      }
    };
    window.addEventListener("keydown", open);
    return () => window.removeEventListener("keydown", open);
  }, [setParams]);
  return (
    <div className="relative flex h-full">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-card focus:p-2"
      >
        {t("app.skip")}
      </a>
      <aside
        aria-label={t("nav.label")}
        className={cn(
          "shrink-0 border-r border-border bg-sidebar transition-[width] duration-200",
          navOpen ? "w-64" : "w-14",
        )}
      >
        <Sidebar
          collapsed={!navOpen}
          onToggle={() => setNavOpen((open) => !open)}
          onSearch={() => setSearchOpen(true)}
          onNotifications={() =>
            setParams((current) => {
              const next = new URLSearchParams(current);
              if (next.get("panel") === "notifications") next.delete("panel");
              else next.set("panel", "notifications");
              return next;
            })
          }
        />
      </aside>
      <div ref={area} className={cn("relative flex min-w-0 flex-1", beside ? "flex-row" : "flex-col")}>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <TopBar
            chatOpen={chatOpen}
            onToggleChat={() => setChatOpen((open) => !open)}
            terminalOpen={terminalOpen}
            onToggleTerminal={() => setTerminalOpen((open) => !open)}
          />
          <main
            id="content"
            tabIndex={-1}
            className="min-h-0 flex-1 overflow-y-auto bg-[linear-gradient(to_bottom,var(--color-main-tint),transparent_18rem),var(--theme-backdrop)]"
          >
            <Outlet />
          </main>
          {beside && <StatusBar />}
        </div>
        {(chatOpen || terminalOpen) && (
          // One panel: the chat on top, the terminal under it, each alone when the other is closed. Beside the screen
          // it is a column; below it, the same stack across the width. In full screen the terminal covers the screen
          // and the panel, and the chat stays mounted with its conversation.
          <div
            data-placement={beside ? "beside" : "below"}
            className={cn(
              "flex flex-col bg-background",
              full
                ? "absolute inset-0 z-30"
                : beside
                  ? "w-96 shrink-0 border-l border-border"
                  : "h-[min(36rem,55%)] shrink-0 border-t border-border",
            )}
          >
            {chatOpen && (
              <aside aria-label={t("chat.title")} className={cn("min-h-0 flex-1", full && "hidden")}>
                <ChatPanel onCollapse={() => setChatOpen(false)} />
              </aside>
            )}
            {terminalOpen && (
              <div className={cn("min-h-0", chatOpen && !full ? "h-1/2 border-t border-border" : "flex-1")}>
                <TerminalPanel
                  full={terminalFull}
                  onToggleFull={() => setTerminalFull((value) => !value)}
                  onClose={() => {
                    setTerminalOpen(false);
                    setTerminalFull(false);
                  }}
                />
              </div>
            )}
          </div>
        )}
        {!beside && <StatusBar />}
      </div>
      {params.get("panel") === "notifications" && (
        <NotificationPanel
          navOpen={navOpen}
          onClose={() =>
            setParams((current) => {
              const next = new URLSearchParams(current);
              next.delete("panel");
              return next;
            })
          }
        />
      )}
      <ThemeTokens />
      <SearchPalette open={searchOpen} onOpenChange={setSearchOpen} />
      <Onboarding />
      {(params.get("panel") === "changelog" || params.get("panel") === "help") && (
        <InfoDialog
          panel={params.get("panel") as Panel}
          onClose={() =>
            setParams((current) => {
              const next = new URLSearchParams(current);
              next.delete("panel");
              return next;
            })
          }
        />
      )}
      {settings !== null && (
        <SettingsDialog
          section={settings || null}
          initialQuery={params.get("q") ?? ""}
          onSection={setSettings}
          onClose={() => setSettings(null)}
        />
      )}
    </div>
  );
}
