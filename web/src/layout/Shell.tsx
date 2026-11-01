// Navigation on the left, the screen in the middle, and one panel with the chat and the terminal (ADR 0039, 0045):
// beside the screen when the window leaves it at least 30rem, below it otherwise; never both at once.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Outlet, useLocation, useSearchParams } from "react-router";
import { useConfig } from "@/api/queries";
import { AudioVisualizer } from "@/audio/AudioVisualizer";
import { ChatProvider } from "@/chat/ChatContext";
import { isDurableSession } from "@/chat/useChat";
import { AvatarThemeProvider } from "@/components/AvatarTheme";
import { FOOTER_RELOCATED } from "@/components/FooterLayout";
import { OperationDialog } from "@/components/OperationDialog";
import { t } from "@/i18n";
import { OPEN_CHAT } from "@/lib/chatIntent";
import { OPEN_TERMINAL } from "@/lib/terminal";
import { useWidth } from "@/lib/useWidth";
import { cn } from "@/lib/utils";
import { useVisualViewport } from "@/lib/viewport";
import { useNoticeStream } from "@/notifications/useNoticeStream";
import { Onboarding } from "@/onboarding/OnboardingWizard";
import { InstalledView, useViews, ViewSlot } from "@/plugins/Views";
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
  const views = useViews();
  const panelViews = useMemo(() => (views.data ?? []).filter((view) => view.slot === "panel"), [views.data]);
  const [retainedViews, setRetainedViews] = useState<string[]>([]);
  // Getting narrower folds the navigation; getting wider leaves it as the person set it. The chat moves below instead.
  // A phone also fires resize for the browser's toolbars and keyboard, at the same width; those change nothing here.
  useEffect(() => {
    let width = window.innerWidth;
    const fold = () => {
      const narrower = window.innerWidth < width;
      width = window.innerWidth;
      if (narrower && width < 1024) {
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
  const location = useLocation();
  const home = location.pathname === "/";
  useEffect(() => {
    if (home) setChatOpen(true);
  }, [home, setChatOpen]);
  const area = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const workspace = useRef<HTMLDivElement>(null);
  const pageScroll = useRef<(() => void) | null>(null);
  const [scrollTarget, setScrollTarget] = useState<{ id: string } | null>(null);
  const [pageHold, setPageHold] = useState<number | null>(null);
  const requestScroll = useCallback((id: string) => setScrollTarget({ id }), []);
  useEffect(() => () => pageScroll.current?.(), []);
  // Keep a focused composer visible when a phone's on-screen keyboard resizes the visual viewport.
  useEffect(() => {
    let frame: number | null = null;
    const keepEditorVisible = () => {
      const active = document.activeElement;
      if (
        !(active instanceof HTMLElement) ||
        !active.matches('input, textarea, [contenteditable="true"]') ||
        !active.closest('[data-placement="below"]')
      )
        return;
      if (frame !== null) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        frame = null;
        if (document.activeElement === active) active.scrollIntoView({ block: "nearest", inline: "nearest" });
      });
    };
    window.addEventListener("resize", keepEditorVisible);
    window.visualViewport?.addEventListener("resize", keepEditorVisible);
    return () => {
      window.removeEventListener("resize", keepEditorVisible);
      window.visualViewport?.removeEventListener("resize", keepEditorVisible);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, []);
  const rem = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const beside = fitsBeside(useWidth(area), rem);
  const besideHeader = useRef<HTMLDivElement>(null);
  const belowHeader = useRef<HTMLDivElement>(null);
  const [headerHost] = useState(() => document.createElement("div"));
  useLayoutEffect(() => {
    const target = beside ? besideHeader.current : belowHeader.current;
    if (!target || headerHost.parentElement === target) return;
    const focused = headerHost.contains(document.activeElement) ? (document.activeElement as HTMLElement) : null;
    target.appendChild(headerHost);
    focused?.focus({ preventScroll: true });
  }, [beside, headerHost]);
  useLayoutEffect(() => () => headerHost.remove(), [headerHost]);
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
  const durableChat = params.get("chat");
  useEffect(() => {
    if (durableChat && isDurableSession(durableChat)) {
      setChatOpen(true);
      setTerminalFull(false);
    }
  }, [durableChat, setChatOpen]);
  const settings = params.get("settings");
  const requestedView = panelViews.find((view) => view.id === params.get("panel"));
  const viewOpen = Boolean(requestedView);
  const requestedId = requestedView?.id;
  useEffect(() => {
    if (requestedId) {
      setRetainedViews((current) => (current.includes(requestedId) ? current : [...current, requestedId]));
      setActivePanel(requestedId);
    }
  }, [requestedId]);
  const selectedPanel =
    requestedId ??
    (activePanel === "page" && !beside
      ? "page"
      : activePanel === "chat" && chatOpen
        ? "chat"
        : activePanel === "terminal" && terminalOpen
          ? "terminal"
          : retainedViews.includes(activePanel) && panelViews.some((view) => view.id === activePanel)
            ? activePanel
            : chatOpen
              ? "chat"
              : terminalOpen
                ? "terminal"
                : !beside
                  ? "page"
                  : "chat");
  // Panel and full-screen changes can reveal the same footer at a new capacity without a viewport resize.
  // biome-ignore lint/correctness/useExhaustiveDependencies: each layout change requires a fresh footer measurement
  useLayoutEffect(() => {
    window.dispatchEvent(new Event(FOOTER_RELOCATED));
  }, [beside, selectedPanel, terminalFull]);
  const route = location.pathname;
  const previousRoute = useRef(route);
  useEffect(() => {
    if (previousRoute.current !== route && !beside) setActivePanel("page");
    previousRoute.current = route;
  }, [route, beside]);
  const full = terminalOpen && terminalFull && selectedPanel === "terminal";
  const flowingWorkspace = !beside && !full;
  // An explicit navigation to the view can repeat the same URL, and should still bring it into view.
  const previousLocation = useRef(location);
  const ownNavigation = useRef<{ path: string; search: string } | null>(null);
  useEffect(() => {
    const previous = previousLocation.current;
    previousLocation.current = location;
    const changed = previous.key !== location.key;
    const own =
      changed &&
      ownNavigation.current?.path === location.pathname &&
      ownNavigation.current.search === location.search.replace(/^\?/, "");
    if (changed) ownNavigation.current = null;
    if (beside || (changed && !own)) {
      pageScroll.current?.();
      setScrollTarget(null);
      setPageHold(null);
    }
    if (
      !beside &&
      viewOpen &&
      changed &&
      !own &&
      (previous.pathname !== location.pathname ||
        previous.search === location.search ||
        new URLSearchParams(previous.search).get("panel") !== requestedId)
    ) {
      pageScroll.current?.();
      requestScroll(requestedId ?? "page");
    }
  }, [beside, viewOpen, location, requestScroll, requestedId]);

  useLayoutEffect(() => {
    if (!scrollTarget) return;
    if (beside || full) {
      setScrollTarget(null);
      setPageHold(null);
      return;
    }
    if (scrollTarget.id !== "page" && selectedPanel !== scrollTarget.id) return;
    const box = scroller.current;
    const panel = workspace.current;
    if (!box || (scrollTarget.id !== "page" && !panel)) return;
    const frame = requestAnimationFrame(() => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (scrollTarget.id === "page" && pageHold !== null && !reduced && box.scrollTop > 1 && box.scrollTo) {
        let timer = 0;
        const cleanup = () => {
          box.removeEventListener("scrollend", onScrollEnd);
          window.clearTimeout(timer);
          pageScroll.current = null;
        };
        const finish = () => {
          cleanup();
          setPageHold(null);
        };
        const onScrollEnd = () => {
          if (box.scrollTop <= 1) finish();
        };
        pageScroll.current = () => {
          cleanup();
          setPageHold(null);
          box.scrollTo({ top: box.scrollTop, behavior: "instant" });
        };
        box.addEventListener("scrollend", onScrollEnd);
        timer = window.setTimeout(finish, 1200);
      } else if (pageHold !== null) setPageHold(null);
      box.scrollTo?.({
        top: scrollTarget.id === "page" ? 0 : (panel?.offsetTop ?? 0),
        behavior: reduced ? "auto" : "smooth",
      });
      setScrollTarget(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [beside, full, pageHold, scrollTarget, selectedPanel]);

  const selectPanel = useCallback(
    (id: string) => {
      pageScroll.current?.();
      setScrollTarget(null);
      // Preserve the old panel height while Page scrolls up so selecting its tab cannot clamp the scroll instantly.
      const hold =
        id === "page" &&
        !beside &&
        !full &&
        selectedPanel !== "page" &&
        (scroller.current?.scrollTop ?? 0) > 1 &&
        !window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? (workspace.current?.getBoundingClientRect().height ?? null)
          : null;
      setPageHold(hold);
      setActivePanel(id);
      setTerminalFull(false);
      // Native panel selection needs no URL write unless it closes a contributed panel. A no-op
      // navigation can overwrite a conversation selected before this router render has committed.
      if (requestedId || panelViews.some((view) => view.id === id)) {
        setParams((current) => {
          const next = new URLSearchParams(current);
          if (panelViews.some((view) => view.id === id)) next.set("panel", id);
          else if (panelViews.some((view) => view.id === next.get("panel"))) next.delete("panel");
          ownNavigation.current = { path: location.pathname, search: next.toString() };
          return next;
        });
      }
      if (!beside) requestScroll(id);
    },
    [beside, full, location.pathname, requestScroll, requestedId, selectedPanel, setParams, panelViews],
  );
  const closeView = (id: string) => {
    pageScroll.current?.();
    setRetainedViews((current) => current.filter((entry) => entry !== id));
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
      <div ref={area} className="workspace-area relative flex min-w-0 flex-1 flex-col">
        <div ref={belowHeader} hidden={beside || full} className="shrink-0" />
        <div
          ref={scroller}
          className={cn(
            "workspace-scroll relative flex min-h-0 min-w-0 flex-1",
            beside ? "flex-row" : flowingWorkspace ? "flex-col overflow-y-auto" : "flex-col",
          )}
        >
          <div
            inert={full}
            aria-hidden={full || undefined}
            className={cn("flex min-w-0 flex-col", flowingWorkspace ? "min-h-full shrink-0" : "min-h-0 flex-1")}
          >
            <div ref={besideHeader} hidden={!beside} className="shrink-0" />
            <main
              id="content"
              inert={!beside && selectedPanel !== "page" && !flowingWorkspace}
              aria-hidden={!beside && selectedPanel !== "page" && !flowingWorkspace ? true : undefined}
              tabIndex={-1}
              className={cn(
                "flex-1 bg-[linear-gradient(to_bottom,var(--color-main-tint),transparent_18rem),var(--theme-backdrop)]",
                !flowingWorkspace && "min-h-0 overflow-y-auto",
              )}
            >
              <Outlet />
            </main>
            <div ref={besideFooter} hidden={!beside} className="shrink-0" />
          </div>
          {(chatOpen ||
            terminalOpen ||
            viewOpen ||
            retainedViews.some((id) => panelViews.some((view) => view.id === id))) && (
            // One selected workspace panel; the same mounted contents move between desktop and phone layouts.
            <div
              ref={workspace}
              style={pageHold !== null && selectedPanel === "page" ? { minHeight: pageHold } : undefined}
              data-placement={beside ? "beside" : "below"}
              data-active={selectedPanel}
              className={cn(
                "workspace-panel flex flex-col bg-background",
                flowingWorkspace && panelViews.some((view) => view.id === selectedPanel) ? "min-h-full" : "min-h-0",
                full
                  ? "absolute inset-0"
                  : beside
                    ? "w-96 shrink-0 border-l border-border"
                    : selectedPanel === "page" || flowingWorkspace
                      ? "shrink-0 border-t border-border"
                      : "absolute inset-x-0 top-12 bottom-0 border-t border-border",
              )}
            >
              <WorkspacePanels
                flowing={flowingWorkspace}
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
                              onActivate={() => selectPanel("terminal")}
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
                  ...panelViews
                    .filter((view) => view.id === requestedId || retainedViews.includes(view.id))
                    .map((view) => ({
                      id: view.id,
                      title: view.title,
                      scrollable: true,
                      content: (
                        <aside aria-label={view.title} className={flowingWorkspace ? "" : "h-full min-h-0"}>
                          <InstalledView
                            entry={view}
                            onClose={() => closeView(view.id)}
                            flowing={flowingWorkspace}
                            visible={selectedPanel === view.id || flowingWorkspace}
                          />
                        </aside>
                      ),
                    })),
                ]}
              />
            </div>
          )}
        </div>
        <div
          ref={belowFooter}
          hidden={beside || (selectedPanel !== "page" && !flowingWorkspace)}
          className="shrink-0"
        />
        {createPortal(
          <TopBar
            navigationMode={!beside}
            chatOpen={chatOpen && selectedPanel === "chat"}
            onToggleChat={() => {
              if (beside && chatOpen && selectedPanel === "chat") setChatOpen(false);
              else {
                setChatOpen(true);
                selectPanel("chat");
              }
            }}
            terminalOpen={terminalOpen && selectedPanel === "terminal"}
            onToggleTerminal={() => {
              if (beside && terminalOpen && selectedPanel === "terminal") {
                setTerminalOpen(false);
                setTerminalFull(false);
              } else {
                setTerminalOpen(true);
                selectPanel("terminal");
              }
            }}
          />,
          headerHost,
        )}
        {/* Moving the mounted controls preserves their state when the side panel moves below. */}
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
      <ViewSlot slot="background" />
      <SoundEngine />
      <SearchPalette open={searchOpen} onOpenChange={setSearchOpen} />
      <Onboarding />
      <OperationDialog />
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
