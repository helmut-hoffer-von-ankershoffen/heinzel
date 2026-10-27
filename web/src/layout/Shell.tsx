// Navigation on the left, the screen in the middle, and one panel with the chat and the terminal (ADR 0039, 0045):
// beside the screen when the window leaves it at least 30rem, below it otherwise; never both at once.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Outlet, useLocation, useSearchParams } from "react-router";
import { useConfig } from "@/api/queries";
import { AudioVisualizer } from "@/audio/AudioVisualizer";
import { ChatProvider } from "@/chat/ChatContext";
import { AvatarThemeProvider } from "@/components/AvatarTheme";
import { FOOTER_RELOCATED } from "@/components/FooterLayout";
import { t } from "@/i18n";
import { OPEN_CHAT } from "@/lib/chatIntent";
import { OPEN_TERMINAL } from "@/lib/terminal";
import { useWidth } from "@/lib/useWidth";
import { cn } from "@/lib/utils";
import { useVisualViewport } from "@/lib/viewport";
import { useNoticeStream } from "@/notifications/useNoticeStream";
import { Onboarding } from "@/onboarding/OnboardingWizard";
import { SoundEngine } from "@/sound/SoundEngine";
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
import { WorkspacePanels } from "./WorkspacePanels";
import { WorldClock } from "./WorldClock";

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

export function Shell() {
  useVisualViewport();
  return (
    <ChatProvider>
      <AvatarThemeProvider>
        <Layout />
      </AvatarThemeProvider>
    </ChatProvider>
  );
}

function Layout() {
  const config = useConfig();
  const audioVisualizer = config.data?.settings.find(
    (setting) => setting.section === "system.ui" && setting.key === "audio_visualizer",
  );
  const showAudioVisualizer = (audioVisualizer?.value ?? audioVisualizer?.default) === true;
  // Narrow windows start with the navigation as a rail, so the screen keeps its room.
  const [navOpen, setNavOpen] = useRemembered("hz.nav.open", () => window.innerWidth >= 1024);
  const [chatOpen, setChatOpen] = useRemembered("hz.chat.open", () => true);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [terminalFull, setTerminalFull] = useState(false);
  const [activePanel, setActivePanel] = useState(() => (window.innerWidth < 1024 ? "page" : "chat"));
  const [clockRetained, setClockRetained] = useState(false);
  // Getting narrower folds the navigation; getting wider leaves it as the person set it. The chat moves below instead.
  useEffect(() => {
    const fold = () => {
      if (window.innerWidth < 1024) {
        setNavOpen(false);
        if (document.activeElement?.closest("footer")) setActivePanel("page");
      }
    };
    window.addEventListener("resize", fold);
    return () => window.removeEventListener("resize", fold);
  }, [setNavOpen]);
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
  const besideFooter = useRef<HTMLDivElement>(null);
  const belowFooter = useRef<HTMLDivElement>(null);
  const [footerHost] = useState(() => document.createElement("div"));
  useLayoutEffect(() => {
    const target = beside ? besideFooter.current : belowFooter.current;
    if (!target || footerHost.parentElement === target) return;
    const focused = footerHost.contains(document.activeElement) ? (document.activeElement as HTMLElement) : null;
    target.appendChild(footerHost);
    focused?.focus({ preventScroll: true });
  }, [beside, footerHost]);
  useLayoutEffect(() => () => footerHost.remove(), [footerHost]);
  // Settings is a dialog over the screen, open while the URL has `?settings=<section>`, so links can point into it.
  const [params, setParams] = useSearchParams();
  const settings = params.get("settings");
  const clockOpen = params.get("panel") === "world-clock";
  useEffect(() => {
    if (clockOpen) {
      setClockRetained(true);
      setActivePanel("world-clock");
    }
  }, [clockOpen]);
  const selectedPanel = clockOpen
    ? "world-clock"
    : activePanel === "page" && !beside
      ? "page"
      : activePanel === "chat" && chatOpen
        ? "chat"
        : activePanel === "terminal" && terminalOpen
          ? "terminal"
          : activePanel === "world-clock" && clockRetained
            ? "world-clock"
            : chatOpen
              ? "chat"
              : terminalOpen
                ? "terminal"
                : !beside
                  ? "page"
                  : "chat";
  // Page selection can reveal the same footer at a new capacity without a viewport resize.
  useLayoutEffect(() => {
    if (beside || selectedPanel === "page") window.dispatchEvent(new Event(FOOTER_RELOCATED));
  }, [beside, selectedPanel]);
  const route = useLocation().pathname;
  const previousRoute = useRef(route);
  useEffect(() => {
    if (previousRoute.current !== route && !beside) setActivePanel("page");
    previousRoute.current = route;
  }, [route, beside]);
  const full = terminalOpen && terminalFull && selectedPanel === "terminal";
  const flowingClock = !beside && !full && selectedPanel === "world-clock";
  const selectPanel = useCallback(
    (id: string) => {
      setActivePanel(id);
      setTerminalFull(false);
      setParams((current) => {
        const next = new URLSearchParams(current);
        if (id === "world-clock") next.set("panel", "world-clock");
        else if (next.get("panel") === "world-clock") next.delete("panel");
        return next;
      });
    },
    [setParams],
  );
  const closeClock = () => {
    setClockRetained(false);
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("panel");
      return next;
    });
  };
  // Commands select the terminal without discarding the other open workspace panels.
  useEffect(() => {
    const open = () => {
      setChatOpen(true);
      setTerminalOpen(true);
      selectPanel("terminal");
    };
    const openChat = () => {
      setChatOpen(true);
      selectPanel("chat");
    };
    window.addEventListener(OPEN_TERMINAL, open);
    window.addEventListener(OPEN_CHAT, openChat);
    return () => {
      window.removeEventListener(OPEN_TERMINAL, open);
      window.removeEventListener(OPEN_CHAT, openChat);
    };
  }, [setChatOpen, selectPanel]);
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
    <div
      data-testid="app-shell"
      className="relative box-border flex h-[min(100dvh,var(--hz-layout-height,100dvh))] overflow-clip pt-[var(--hz-safe-area-top,env(safe-area-inset-top,0px))] pr-[var(--hz-safe-area-right,env(safe-area-inset-right,0px))] pb-[var(--hz-safe-area-bottom,env(safe-area-inset-bottom,0px))] pl-[var(--hz-safe-area-left,env(safe-area-inset-left,0px))]"
    >
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
      <div
        ref={area}
        className={cn(
          "workspace-area relative flex min-w-0 flex-1",
          beside ? "flex-row" : flowingClock ? "flex-col overflow-y-auto" : "flex-col",
        )}
      >
        <div
          inert={full}
          aria-hidden={full || undefined}
          className={cn("flex min-w-0 flex-col", flowingClock ? "min-h-full shrink-0" : "min-h-0 flex-1")}
        >
          <TopBar
            chatOpen={chatOpen && selectedPanel === "chat"}
            onToggleChat={() => {
              if (chatOpen && selectedPanel === "chat") setChatOpen(false);
              else {
                setChatOpen(true);
                selectPanel("chat");
              }
            }}
            terminalOpen={terminalOpen && selectedPanel === "terminal"}
            onToggleTerminal={() => {
              if (terminalOpen && selectedPanel === "terminal") {
                setTerminalOpen(false);
                setTerminalFull(false);
              } else {
                setTerminalOpen(true);
                selectPanel("terminal");
              }
            }}
          />
          <main
            id="content"
            inert={!beside && selectedPanel !== "page" && !flowingClock}
            aria-hidden={!beside && selectedPanel !== "page" && !flowingClock ? true : undefined}
            tabIndex={-1}
            className={cn(
              "flex-1 bg-[linear-gradient(to_bottom,var(--color-main-tint),transparent_18rem),var(--theme-backdrop)]",
              !flowingClock && "min-h-0 overflow-y-auto",
            )}
          >
            <Outlet />
          </main>
          <div ref={besideFooter} hidden={!beside} className="shrink-0" />
        </div>
        {(chatOpen || terminalOpen || clockOpen || clockRetained) && (
          // One selected workspace panel; the same mounted contents move between desktop and phone layouts.
          <div
            data-placement={beside ? "beside" : "below"}
            data-active={selectedPanel}
            className={cn(
              "workspace-panel flex min-h-0 flex-col bg-background",
              full
                ? "absolute inset-0"
                : beside
                  ? "w-96 shrink-0 border-l border-border"
                  : selectedPanel === "page" || flowingClock
                    ? "shrink-0 border-t border-border"
                    : "absolute inset-x-0 top-12 bottom-0 border-t border-border",
            )}
          >
            <WorkspacePanels
              flowing={flowingClock}
              active={selectedPanel}
              onSelect={selectPanel}
              panels={[
                ...(!beside ? [{ id: "page", title: t("workspace.page"), content: null }] : []),
                ...(chatOpen
                  ? [
                      {
                        id: "chat",
                        title: t("chat.title"),
                        content: (
                          <aside aria-label={t("chat.title")} className="h-full min-h-0">
                            <ChatPanel onCollapse={() => setChatOpen(false)} />
                          </aside>
                        ),
                      },
                    ]
                  : []),
                ...(terminalOpen
                  ? [
                      {
                        id: "terminal",
                        title: t("terminal.title"),
                        content: (
                          <TerminalPanel
                            full={terminalFull}
                            onToggleFull={() => setTerminalFull((value) => !value)}
                            onClose={() => {
                              setTerminalOpen(false);
                              setTerminalFull(false);
                            }}
                          />
                        ),
                      },
                    ]
                  : []),
                ...(clockOpen || clockRetained
                  ? [
                      {
                        id: "world-clock",
                        title: t("worldClock.title"),
                        content: (
                          <aside aria-label={t("worldClock.panel")} className={flowingClock ? "" : "h-full min-h-0"}>
                            <WorldClock onClose={closeClock} flowing={flowingClock} />
                          </aside>
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          </div>
        )}
        <div ref={belowFooter} hidden={beside || (selectedPanel !== "page" && !flowingClock)} className="shrink-0" />
        {/* One mounted footer: moving its host preserves controls when the side panel moves below. */}
        {createPortal(<StatusBar />, footerHost)}
      </div>
      {showAudioVisualizer && (
        <section aria-label="Audio visualizer" className="min-h-0 w-7 shrink-0 self-stretch border-l border-border">
          <AudioVisualizer />
        </section>
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
      <SoundEngine />
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
