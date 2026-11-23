// Navigation on the left, the screen in the middle, and one panel with the chat and the terminal (ADR 0039, 0045):
// beside the screen when the window leaves it at least 30rem, below it otherwise; never both at once.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Outlet, useLocation, useNavigate, useSearchParams } from "react-router";
import { AboutDialog } from "@/about/AboutDialog";
import { type MenuAction, onMenuAction } from "@/about/menu";
import { useBrowserBookmarksBar, useBrowserStartPage, useConfig } from "@/api/queries";
import { AudioVisualizer } from "@/audio/AudioVisualizer";
import { useAskHeinzel } from "@/chat/askHeinzel";
import { AvatarThemeProvider } from "@/components/AvatarTheme";
import { FOOTER_RELOCATED } from "@/components/FooterLayout";
import { OperationDialog } from "@/components/OperationDialog";
import { t } from "@/i18n";
import { clearChatLink, isDurableSession, OPEN_CHAT, OPEN_NEW_CHAT, requestComposerFocus } from "@/lib/chatIntent";
import { useOperationRun } from "@/lib/operationRun";
import { OverlayTarget } from "@/lib/overlayTarget";
import { anchorPress } from "@/lib/pressAnchor";
import { clampSidebar, readSidebarWidth, sidebarMax, writeSidebarWidth } from "@/lib/sidebarWidth";
import { OPEN_TERMINAL } from "@/lib/terminal";
import { useWidth } from "@/lib/useWidth";
import { cn } from "@/lib/utils";
import { useVisualViewport } from "@/lib/viewport";
import { anchorWorkspace } from "@/lib/workspaceAnchor";
import { useNoticeStream } from "@/notifications/useNoticeStream";
import { Onboarding } from "@/onboarding/OnboardingWizard";
import { InstalledView, useViews, ViewSlot } from "@/plugins/Views";
import { registerSlot } from "@/sound/dock";
import { SoundEngine } from "@/sound/SoundEngine";
import { BrowserPanel, desktopBrowserAvailable } from "./BrowserPanel";
import { useBrowserAgents, useCommitAttention } from "./browserAgents";
import { CalendarPanel } from "./CalendarPanel";
import { ChatPanel } from "./ChatPanel";
import { InfoDialog, type Panel } from "./InfoDialog";
import { NotificationPanel } from "./NotificationCenter";
import { useNativeFooter } from "./nativeFooter";
import { SearchPalette } from "./SearchPalette";
import { SettingsDialog } from "./SettingsDialog";
import { Sidebar, VizStageSlot } from "./Sidebar";
import { SidebarResizer } from "./SidebarResizer";
import { StatusBar } from "./StatusBar";
import { TerminalPanel } from "./TerminalPanel";
import { ThemeTokens } from "./ThemeTokens";
import { TopBar } from "./TopBar";
import { applyTitlebar, nativeMobileWorkspace, useTitlebar } from "./titlebar";
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

/** No element: `useWidth` then follows the window. */
const WINDOW = { current: null };

const GO: Record<string, string> = {
  "go:today": "/",
  "go:heinzel": "/heinzel",
  "go:team": "/team",
  "go:plugins": "/plugins",
};

export function Shell() {
  useVisualViewport();
  return (
    <AvatarThemeProvider>
      <Layout />
    </AvatarThemeProvider>
  );
}

function CloseNavigationOnOperation({ close }: { close: () => void }) {
  const { stage } = useOperationRun();
  useLayoutEffect(() => {
    if (stage !== "idle") close();
  }, [stage, close]);
  return null;
}

function Layout() {
  const nativeFooter = useNativeFooter();
  const config = useConfig();
  const startPage = useBrowserStartPage();
  const bookmarksBar = useBrowserBookmarksBar();
  const ask = useAskHeinzel();
  const audioVisualizer = config.data?.settings.find(
    (setting) => setting.section === "system.ui" && setting.key === "audio_visualizer",
  );
  const showAudioVisualizer = (audioVisualizer?.value ?? audioVisualizer?.default) === true;
  const [mobile] = useState(nativeMobileWorkspace);
  // Narrow windows start with the navigation as a rail, so the screen keeps its room.
  const [navOpen, setNavOpen] = useRemembered("hz.nav.open", () => window.innerWidth >= 1024);
  // The desktop app's integrated title bar (macOS): its insets become CSS variables; a browser gets 0.
  const titlebar = useTitlebar();
  useLayoutEffect(
    () => applyTitlebar(document.documentElement, titlebar.bar, titlebar.fullscreen, navOpen),
    [titlebar.bar, titlebar.fullscreen, navOpen],
  );
  const [chatOpen, setChatOpen] = useRemembered("hz.chat.open", () => true);
  const browserEnabled = desktopBrowserAvailable();
  const [browserOpen, setBrowserOpen] = useState(false);
  const browserAgents = useBrowserAgents();
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [terminalFull, setTerminalFull] = useState(false);
  const [browserFull, setBrowserFull] = useState(false);
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
        if (!mobile) setNavOpen(false);
        if (document.activeElement?.closest("footer")) setActivePanel("page");
      }
    };
    window.addEventListener("resize", fold);
    return () => window.removeEventListener("resize", fold);
  }, [mobile, setNavOpen]);
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
  const mainContent = useRef<HTMLElement>(null);
  const pageContent = useRef<HTMLDivElement>(null);
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
  const areaWidth = useWidth(area);
  const [sidebarWidth, setSidebarWidth] = useState(readSidebarWidth);
  const windowWidth = useWidth(WINDOW);
  // Window width covers phone rotation and iPad multitasking without reacting to the keyboard.
  const compact = mobile && windowWidth < 960;
  const beside = !compact && fitsBeside(areaWidth, rem);
  const navDrawer = useRef<HTMLDialogElement>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const openNavigation = () => {
    navDrawer.current?.showModal();
    setDrawerOpen(true);
  };
  const closeNavigation = useCallback(() => {
    navDrawer.current?.close();
    setDrawerOpen(false);
  }, []);
  const navigationOverlays = useCallback(() => navDrawer.current ?? document.body, []);
  useEffect(() => {
    if (!compact) closeNavigation();
  }, [compact, closeNavigation]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: dismiss on each router navigation
  useEffect(() => {
    closeNavigation();
  }, [location.key, closeNavigation]);
  const audioCompactSlot = useRef<HTMLDivElement>(null);
  const audioRailSlot = useRef<HTMLElement>(null);
  const [audioHost] = useState(() => document.createElement("div"));
  const audioSlot = useCallback(
    (element: HTMLDivElement | null) => {
      audioCompactSlot.current = element;
      if (element && audioHost.parentElement !== element) element.appendChild(audioHost);
    },
    [audioHost],
  );
  useLayoutEffect(() => {
    if (!showAudioVisualizer) return;
    const slot = compact ? audioCompactSlot.current : audioRailSlot.current;
    if (slot && audioHost.parentElement !== slot) slot.appendChild(audioHost);
    audioHost.className = "h-full";
  }, [compact, showAudioVisualizer, audioHost]);
  useLayoutEffect(() => () => audioHost.remove(), [audioHost]);
  const sidebarLimit = sidebarMax(windowWidth, areaWidth);
  const sidebarShown = clampSidebar(sidebarWidth, windowWidth, areaWidth);
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
      setBrowserFull(false);
    }
  }, [durableChat, setChatOpen]);
  const settings = params.get("settings");
  const navigate = useNavigate();
  // About, Updates and Copy are handled where they live. Reveal the footer first if the terminal covers it.
  useEffect(
    () =>
      onMenuAction(["settings", "daemon", "changelog", "doctor", "updates"], (action) => {
        if (action === "doctor") navigate("/doctor");
        else if (action === "updates") {
          setTerminalFull(false);
          setBrowserFull(false);
        } else
          setParams((current) => {
            const next = new URLSearchParams(current);
            if (action === "changelog") next.set("panel", "changelog");
            else next.set("settings", action === "daemon" ? "daemon" : "");
            return next;
          });
      }),
    [navigate, setParams],
  );
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
    (activePanel === "browser" && browserOpen && browserEnabled
      ? "browser"
      : activePanel === "page" && !beside
        ? "page"
        : activePanel === "chat" && chatOpen
          ? "chat"
          : activePanel === "calendar" && calendarOpen
            ? "calendar"
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
  }, [beside, selectedPanel, terminalFull, browserFull]);
  const route = location.pathname;
  const previousRoute = useRef(route);
  useEffect(() => {
    if (previousRoute.current !== route && !beside) setActivePanel("page");
    previousRoute.current = route;
  }, [route, beside]);
  const full =
    (terminalOpen && terminalFull && selectedPanel === "terminal") ||
    (browserEnabled && browserOpen && browserFull && selectedPanel === "browser");
  const flowingWorkspace = !beside && !full;
  const workspaceShown =
    chatOpen ||
    terminalOpen ||
    (browserEnabled && browserOpen) ||
    calendarOpen ||
    viewOpen ||
    retainedViews.some((id) => panelViews.some((view) => view.id === id));
  useLayoutEffect(() => {
    if (!flowingWorkspace || !workspaceShown || !scroller.current || !pageContent.current || !workspace.current) return;
    // With Page selected, protect only the press that opens a workspace tab.
    return anchorWorkspace(scroller.current, pageContent.current, workspace.current, selectedPanel === "page");
  }, [flowingWorkspace, workspaceShown, selectedPanel]);
  // A pressed page control stays under the pointer when the page above it loads late: the page scrolls in the shared
  // scroller below the header, or in its own column beside the workspace.
  useLayoutEffect(() => {
    if (full) return;
    const box = flowingWorkspace ? scroller.current : mainContent.current;
    const region = flowingWorkspace ? pageContent.current : mainContent.current;
    if (!box || !region) return;
    return anchorPress(box, region);
  }, [flowingWorkspace, full]);
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
  // A link to one conversation (?chat=) on a narrow screen opens its Chat tab instead of leaving the person on the page.
  // Declared after the navigation effect above, which drops any scroll request when the address changes.
  const chatParam = params.get("chat");
  useEffect(() => {
    if (!chatParam || window.innerWidth >= 1024) return;
    setActivePanel("chat");
    // The workspace sits below the page there: bring the conversation into view, as choosing its tab does.
    requestScroll("chat");
  }, [chatParam, requestScroll]);
  // The new Heinzel's draft (`?create=1`) is on the page: below the workspace it opens the Page tab, also when only the
  // query changed (the switcher on /heinzel). Declared after the conversation link above, which it overrides.
  const createDraft = params.has("create");
  useEffect(() => {
    if (!createDraft || beside) return;
    setActivePanel("page");
    requestScroll("page");
  }, [createDraft, beside, requestScroll]);

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

  const hasChatLink = params.has("chat") || params.has("chat-context");
  const selectPanel = useCallback(
    (id: string, newChat = false) => {
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
      setBrowserFull(false);
      // A no-op navigation can overwrite a conversation selected before this router render has committed.
      const clearLink = newChat && hasChatLink;
      if (clearLink || requestedId || panelViews.some((view) => view.id === id)) {
        setParams((current) => {
          const next = new URLSearchParams(current);
          if (clearLink) {
            clearChatLink(next);
            next.delete("chat-context");
          }
          if (panelViews.some((view) => view.id === id)) next.set("panel", id);
          else if (panelViews.some((view) => view.id === next.get("panel"))) next.delete("panel");
          ownNavigation.current = { path: location.pathname, search: next.toString() };
          return next;
        });
      }
      if (!beside) requestScroll(id);
    },
    [beside, full, hasChatLink, location.pathname, requestScroll, requestedId, selectedPanel, setParams, panelViews],
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
    const openChat = (event: Event) => {
      setChatOpen(true);
      selectPanel("chat", event.type === OPEN_NEW_CHAT);
    };
    window.addEventListener(OPEN_TERMINAL, open);
    window.addEventListener(OPEN_CHAT, openChat);
    window.addEventListener(OPEN_NEW_CHAT, openChat);
    // The menu's New Chat opens the chat on the default Heinzel (every chat is with a Heinzel, ADR 0157) and puts the
    // cursor in its composer, or in the first start's name field.
    const stopNewChat = onMenuAction(["new-chat"], () => {
      window.dispatchEvent(new Event(OPEN_NEW_CHAT));
      requestComposerFocus();
    });
    return () => {
      stopNewChat();
      window.removeEventListener(OPEN_TERMINAL, open);
      window.removeEventListener(OPEN_CHAT, openChat);
      window.removeEventListener(OPEN_NEW_CHAT, openChat);
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
  const toggleChat = (canClose: boolean) => {
    if (canClose && chatOpen && selectedPanel === "chat") setChatOpen(false);
    else {
      setChatOpen(true);
      selectPanel("chat");
    }
  };
  const toggleBrowser = (canClose: boolean) => {
    if (canClose && browserOpen && selectedPanel === "browser") setBrowserOpen(false);
    else {
      setBrowserOpen(true);
      selectPanel("browser");
    }
  };
  // An agent's commit waits at most two minutes: the card comes to the person, not the other way round.
  useCommitAttention(browserAgents.waiting, browserEnabled && browserOpen && selectedPanel === "browser", () => {
    setBrowserOpen(true);
    selectPanel("browser");
  });
  const toggleTerminal = (canClose: boolean) => {
    if (canClose && terminalOpen && selectedPanel === "terminal") {
      setTerminalOpen(false);
      setTerminalFull(false);
    } else {
      setTerminalOpen(true);
      selectPanel("terminal");
    }
  };
  // View menu toggles can close panels in either layout. The narrow top bar navigates to them.
  const menu = useRef({ toggleChat, toggleBrowser, toggleTerminal, browserEnabled });
  menu.current = { toggleChat, toggleBrowser, toggleTerminal, browserEnabled };
  useEffect(
    () =>
      onMenuAction(
        [
          "search",
          "sidebar",
          "terminal",
          "browser",
          "browser-show",
          "chat",
          "notifications",
          "help",
          ...Object.keys(GO),
        ] as MenuAction[],
        (action) => {
          if (action === "search") setSearchOpen(true);
          else if (action === "sidebar") setNavOpen((open) => !open);
          else if (action === "terminal") menu.current.toggleTerminal(true);
          else if (action === "browser-show") {
            // A link opened a tab: show the panel without toggling it shut.
            if (menu.current.browserEnabled) menu.current.toggleBrowser(false);
          } else if (action === "browser") {
            if (menu.current.browserEnabled) menu.current.toggleBrowser(true);
          } else if (action === "chat") menu.current.toggleChat(true);
          else if (action === "notifications" || action === "help")
            setParams((current) => {
              const next = new URLSearchParams(current);
              next.set("panel", action);
              return next;
            });
          else navigate(GO[action] ?? "/");
        },
      ),
    [navigate, setNavOpen, setParams],
  );
  const sidebar = (
    <Sidebar
      collapsed={!compact && !navOpen}
      drawer={compact}
      titlebar={titlebar.bar !== null}
      onToggle={compact ? closeNavigation : () => setNavOpen((open) => !open)}
      onSearch={() => {
        closeNavigation();
        setSearchOpen(true);
      }}
      onNotifications={() => {
        closeNavigation();
        setParams((current) => {
          const next = new URLSearchParams(current);
          if (next.get("panel") === "notifications") next.delete("panel");
          else next.set("panel", "notifications");
          return next;
        });
      }}
    />
  );
  return (
    <div
      data-testid="app-shell"
      className={cn(
        "relative box-border flex h-[min(100dvh,var(--hz-layout-height,100dvh))] overflow-clip pt-[var(--hz-safe-area-top,env(safe-area-inset-top,0px))] pr-[var(--hz-safe-area-right,env(safe-area-inset-right,0px))] pl-[var(--hz-safe-area-left,env(safe-area-inset-left,0px))]",
        !nativeFooter && "pb-[var(--hz-safe-area-bottom,env(safe-area-inset-bottom,0px))]",
      )}
    >
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-card focus:p-2"
      >
        {t("app.skip")}
      </a>
      {compact ? (
        <dialog
          ref={navDrawer}
          onClose={() => setDrawerOpen(false)}
          aria-label={t("nav.label")}
          className="mobile-navigation m-0 max-h-none max-w-none border-r border-border bg-sidebar p-0 text-foreground backdrop:bg-black/35"
          onPointerDown={(event) => {
            if (event.target !== event.currentTarget) return;
            const box = event.currentTarget.getBoundingClientRect();
            if (
              event.clientX < box.left ||
              event.clientX >= box.right ||
              event.clientY < box.top ||
              event.clientY >= box.bottom
            )
              closeNavigation();
          }}
        >
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">
              {drawerOpen && (
                <OverlayTarget value={navigationOverlays}>
                  <CloseNavigationOnOperation close={closeNavigation} />
                  {sidebar}
                </OverlayTarget>
              )}
            </div>
          </div>
        </dialog>
      ) : (
        <aside
          aria-label={t("nav.label")}
          className={cn(
            "shrink-0 border-r border-border bg-sidebar transition-[width] duration-200",
            navOpen ? "w-64" : "w-14",
          )}
        >
          {sidebar}
        </aside>
      )}
      <div ref={area} className="workspace-area relative flex min-w-0 flex-1 flex-col">
        <div ref={belowHeader} hidden={beside || full} className="shrink-0" />
        <div
          ref={scroller}
          className={cn(
            // Vertical only: a child wider than the column must never let the whole workspace pan sideways on a phone.
            "workspace-scroll relative flex min-h-0 min-w-0 flex-1 overflow-x-clip",
            beside ? "flex-row" : flowingWorkspace ? "flex-col overflow-y-auto" : "flex-col",
          )}
        >
          <div
            ref={pageContent}
            inert={full}
            aria-hidden={full || undefined}
            className={cn("flex min-w-0 flex-col", flowingWorkspace ? "min-h-full shrink-0" : "min-h-0 flex-1")}
          >
            <div ref={besideHeader} hidden={!beside} className="shrink-0" />
            <main
              ref={mainContent}
              id="content"
              inert={!beside && selectedPanel !== "page" && !flowingWorkspace}
              aria-hidden={!beside && selectedPanel !== "page" && !flowingWorkspace ? true : undefined}
              tabIndex={-1}
              // Focused by script (skip link, route change), never a control: no ring, which a click or hover left
              // as a stray outline. Real controls inside keep their focus-visible ring.
              data-focus-ring="parent"
              className={cn(
                "min-w-0 flex-1 overflow-x-clip outline-none bg-[linear-gradient(to_bottom,var(--color-main-tint),transparent_18rem),var(--theme-backdrop)]",
                !flowingWorkspace && "min-h-0 overflow-y-auto",
              )}
            >
              <Outlet />
            </main>
            <div ref={besideFooter} hidden={!beside} className="shrink-0" />
          </div>
          {workspaceShown && (
            // One selected workspace panel; the same mounted contents move between desktop and phone layouts.
            <div
              ref={workspace}
              data-placement={beside ? "beside" : "below"}
              data-active={selectedPanel}
              style={{
                ...(pageHold !== null && selectedPanel === "page" ? { minHeight: pageHold } : undefined),
                ...(beside && !full ? { width: sidebarShown } : undefined),
              }}
              className={cn(
                "workspace-panel flex flex-col bg-background",
                flowingWorkspace && panelViews.some((view) => view.id === selectedPanel) ? "min-h-full" : "min-h-0",
                flowingWorkspace && selectedPanel === "browser" && "h-full",
                full
                  ? "absolute inset-0"
                  : beside
                    ? "relative shrink-0 border-l border-border"
                    : selectedPanel === "page" || flowingWorkspace
                      ? "shrink-0 border-t border-border"
                      : "absolute inset-x-0 top-12 bottom-0 border-t border-border",
              )}
            >
              {beside && !full && (
                <SidebarResizer
                  width={sidebarShown}
                  max={sidebarLimit}
                  onChange={(next, final) => {
                    const clamped = clampSidebar(next, window.innerWidth, areaWidth);
                    setSidebarWidth(clamped);
                    if (final) writeSidebarWidth(clamped);
                  }}
                />
              )}
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
                  ...(browserEnabled && browserOpen
                    ? [
                        {
                          id: "browser",
                          title: t("browser.title"),
                          content: (
                            <BrowserPanel
                              bookmarks
                              startPage={startPage}
                              bookmarksBar={bookmarksBar}
                              full={browserFull}
                              onToggleFull={() => setBrowserFull((value) => !value)}
                              onAsk={ask ? (text) => ask.ask(text) : undefined}
                              askName={ask?.name}
                              active={selectedPanel === "browser"}
                              onClose={() => {
                                setBrowserOpen(false);
                                setBrowserFull(false);
                              }}
                              onSettings={() => setSettings("browser")}
                            />
                          ),
                        },
                      ]
                    : []),
                  ...(calendarOpen
                    ? [
                        {
                          id: "calendar",
                          title: "Calendar",
                          content: (
                            <CalendarPanel
                              onClose={() => {
                                setCalendarOpen(false);
                                setActivePanel(chatOpen ? "chat" : "page");
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
        {compact && <VizStageSlot narrow />}
        {createPortal(
          <TopBar
            navigationMode={!beside}
            onOpenNavigation={compact ? openNavigation : undefined}
            audioSlot={compact && showAudioVisualizer ? audioSlot : undefined}
            calendarOpen={calendarOpen && selectedPanel === "calendar"}
            onOpenCalendar={() => {
              setCalendarOpen(true);
              selectPanel("calendar");
            }}
            chatOpen={chatOpen && selectedPanel === "chat"}
            onToggleChat={() => toggleChat(beside)}
            browserOpen={browserOpen && selectedPanel === "browser"}
            onToggleBrowser={browserEnabled ? () => toggleBrowser(beside) : undefined}
            browserAgents={browserAgents.mark}
            terminalOpen={terminalOpen && selectedPanel === "terminal"}
            onToggleTerminal={() => toggleTerminal(beside)}
          />,
          headerHost,
        )}
        {/* Moving the mounted controls preserves their state when the side panel moves below. */}
        {createPortal(
          <StatusBar
            onOpenCalendar={() => {
              setCalendarOpen(true);
              selectPanel("calendar");
            }}
          />,
          footerHost,
        )}
      </div>
      {showAudioVisualizer && !compact && (
        <section
          ref={audioRailSlot}
          aria-label="Audio visualizer"
          className="min-h-0 w-11 shrink-0 self-stretch border-l border-border"
        />
      )}
      {showAudioVisualizer && createPortal(<AudioVisualizer compact={compact} />, audioHost)}
      {params.get("panel") === "notifications" && (
        <NotificationPanel
          navOpen={!compact && navOpen}
          onClose={() =>
            setParams((current) => {
              const next = new URLSearchParams(current);
              next.delete("panel");
              return next;
            })
          }
        />
      )}
      {/* Keep the current stream mounted when the drawer closes. A docked player lives in its controls. */}
      {compact && <div ref={registerSlot} hidden data-sound-compact="true" />}
      <ThemeTokens />
      <ViewSlot slot="background" />
      <SoundEngine />
      <SearchPalette open={searchOpen} onOpenChange={setSearchOpen} />
      <Onboarding />
      <OperationDialog />
      <AboutDialog />
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
