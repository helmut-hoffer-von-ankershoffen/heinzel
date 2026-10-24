// Three panes at full height: navigation on the left, the screen in the middle, the chat on the right (ADR 0039).
import { useCallback, useEffect, useState } from "react";
import { Outlet, useLocation, useSearchParams } from "react-router";
import { t } from "@/i18n";
import { OPEN_TERMINAL } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import { useNoticeStream } from "@/notifications/useNoticeStream";
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

export function Shell() {
  // Narrow windows start with the navigation as a rail and the chat closed, so the screen keeps its room.
  const [navOpen, setNavOpen] = useRemembered("hz.nav.open", () => window.innerWidth >= 1024);
  const [chatOpen, setChatOpen] = useRemembered("hz.chat.open", () => window.innerWidth >= 768);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalFull, setTerminalFull] = useState(false);
  // Getting narrower folds the panes; getting wider leaves them as the person set them.
  useEffect(() => {
    const fold = () => {
      if (window.innerWidth < 1024) setNavOpen(false);
      if (window.innerWidth < 768) setChatOpen(false);
    };
    window.addEventListener("resize", fold);
    return () => window.removeEventListener("resize", fold);
  }, [setNavOpen, setChatOpen]);
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
  // Home is the chat itself (ADR 0045); every other screen has it in the right column.
  const home = useLocation().pathname === "/";
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
          "shrink-0 border-r border-border/60 bg-sidebar transition-[width] duration-200",
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
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          chatOpen={chatOpen}
          onToggleChat={() => setChatOpen((open) => !open)}
          terminalOpen={terminalOpen}
          onToggleTerminal={() => {
            // The terminal opens under the chat, each taking half of the right column.
            if (!terminalOpen) setChatOpen(true);
            setTerminalOpen((open) => !open);
          }}
        />
        <main id="content" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
        <StatusBar />
      </div>
      {((chatOpen && !home) || terminalOpen) && (
        // The right column: the chat above, the terminal below, each alone when the other is closed. In full screen
        // the terminal covers the screen and the chat, which stays mounted with its conversation.
        <div
          className={cn(
            "flex flex-col border-l border-border bg-background",
            terminalOpen && terminalFull
              ? cn("absolute inset-y-0 right-0 z-30", navOpen ? "left-64" : "left-14")
              : "w-96 shrink-0 max-md:absolute max-md:inset-y-0 max-md:right-0 max-md:z-30 max-md:w-[min(24rem,100vw)] max-md:shadow-2xl",
          )}
        >
          {chatOpen && !home && (
            <aside
              aria-label={t("chat.title")}
              className={cn("min-h-0 flex-1", terminalOpen && terminalFull && "hidden")}
            >
              <ChatPanel onCollapse={() => setChatOpen(false)} />
            </aside>
          )}
          {terminalOpen && (
            <div
              className={cn("min-h-0", chatOpen && !home && !terminalFull ? "h-1/2 border-t border-border" : "flex-1")}
            >
              <TerminalPanel
                full={terminalFull}
                onToggleFull={() => setTerminalFull((full) => !full)}
                onClose={() => {
                  setTerminalOpen(false);
                  setTerminalFull(false);
                }}
              />
            </div>
          )}
        </div>
      )}
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
