import {
  ArrowLeft,
  ArrowRight,
  Bot,
  CornerDownLeft,
  Ellipsis,
  ExternalLink,
  Plus,
  RefreshCw,
  Settings,
  X,
} from "lucide-react";
import { type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { MENU_EVENT } from "@/about/menu";
import { BookmarkBar } from "@/bookmarks/BookmarkStar";
import { Favicon } from "@/bookmarks/Favicon";
import { HeinzelFace } from "@/components/HeinzelFace";
import { PaneAction } from "@/components/PaneActions";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { useNativeSurface } from "@/lib/nativeSurface";
import { refreshIcon, refreshName } from "@/lib/refreshFeedback";

type BrowserTab = {
  id: string;
  owner: string;
  selected: boolean;
  status: string;
  url: string;
  title: string;
  /** The page's own icon as a small `data:` image, once the desktop read it. */
  favicon?: string | null;
  /** Set on a tab an agent opened (ADR 0162): the name the tab shows. */
  agent?: { name: string; kind: string } | null;
  /** The person took this agent tab over: the agent's calls wait until Hand back. */
  paused?: boolean;
  /** An outside agent's commit that waits for the person: the sentence naming the action and its target. */
  commit?: { summary: string; id: string } | null;
};
/** The person's own tabs per window, as the desktop enforces. */
const MAX_TABS = 8;
type BrowserState = {
  tabs?: BrowserTab[];
  selected?: string | null;
  /** Monotone desktop snapshot revision; older poll replies cannot roll back a newer browser action. */
  revision?: number;
  status: string;
  url?: string;
  canGoBack?: boolean;
  canGoForward?: boolean;
  notice?: "passkey" | "no-device" | "clear-failed" | null;
  /** A link the desktop could not open in a tab: refused (not public HTTPS) or no free tab. */
  link?: { url: string; reason: "refused" | "limit"; external?: boolean } | null;
};
const NOTICES = {
  passkey: "browser.passkeyNote",
  "no-device": "browser.noDeviceNote",
  "clear-failed": "browser.clearFailedNote",
} as const;
type BrowserBridge = { browser: (request: Record<string, unknown>) => Promise<BrowserState> };
function bridge(): BrowserBridge | undefined {
  const value = (window as Window & { heinzelDesktop?: Partial<BrowserBridge> }).heinzelDesktop;
  return typeof value?.browser === "function" ? (value as BrowserBridge) : undefined;
}
/** Whether this window is the desktop app, the only place that owns an isolated browsing surface. */
export function desktopBrowserAvailable(): boolean {
  return bridge() !== undefined;
}
/** A tab's label: its page title, else its host, else "New tab"; an agent's tab starts with the agent's name. */
function tabLabel(tab: BrowserTab): string {
  const page = pageLabel(tab);
  return tab.agent ? `${tab.agent.name}: ${page}` : page;
}
function pageLabel(tab: BrowserTab): string {
  if (tab.title) return tab.title;
  try {
    return new URL(tab.url).host || t("browser.newTabTitle");
  } catch {
    return t("browser.newTabTitle");
  }
}
/** A Heinzel's tab shows the Heinzel's face; an outside agent's tab shows a neutral agent mark. */
function AgentFace({ kind }: Readonly<{ kind: string }>) {
  const Icon = kind === "heinzel" ? HeinzelFace : Bot;
  return (
    <Icon aria-hidden data-agent-kind={kind === "heinzel" ? "heinzel" : "external"} className="size-3.5 shrink-0" />
  );
}
const SCHEME = /^[a-z][a-z\d+.-]*:/i;
const SEARCH = "https://www.google.com/search";
const START_PAGE = "https://heinzel.team";

/**
 * What a typed address means, like a browser's address bar: a scheme stays as typed, a host or path gets https://,
 * and words or a sentence become a search. The desktop then applies its own rules (public HTTPS only).
 */
export function addressUrl(value: string): string | null {
  const input = value.trim();
  if (!input) return null;
  // "localhost:3000" or "example.com:8443/x" is a host with a port, not a scheme.
  const hostWithPort = /^[^\s/:]+:\d+(?:[/?#]|$)/.test(input);
  if (SCHEME.test(input) && !hostWithPort) return input;
  const host = input.split(/[/?#]/, 1)[0] ?? "";
  const looksLikeHost =
    !/\s/.test(input) && (host.includes(".") || hostWithPort || /^localhost$/i.test(host) || host.startsWith("["));
  if (!looksLikeHost) {
    const search = new URL(SEARCH);
    search.searchParams.set("q", input);
    return search.href;
  }
  return `https://${input}`;
}

export function externalBrowserUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

/**
 * The web view offers an external link; only the desktop owns an isolated native browsing surface.
 * Switching panel tabs only hides the surface, so the page and its state stay. Website sign-ins live in the desktop's
 * separate persistent session until the person clears browsing data.
 */
export function BrowserPanel({
  active,
  onClose,
  onSettings,
  bookmarks = false,
  startup: workspaceStartup,
}: Readonly<{
  active: boolean;
  onClose: () => void /** Show the bookmark star; needs the app's providers. */;
  onSettings?: () => void;
  bookmarks?: boolean;
  startup?: RefObject<boolean>;
}>) {
  const localStartup = useRef(false);
  const startup = workspaceStartup ?? localStartup;
  const [address, setAddress] = useState(() => (startup.current ? "" : START_PAGE));
  const [addressEdited, setAddressEdited] = useState(false);
  const addressRevision = useRef(0);
  const navigationRevision = useRef(0);
  const browserRequestRevision = useRef(0);
  const observedBrowserRevision = useRef(-1);
  const [state, setState] = useState<BrowserState>({ status: "empty" });
  const rejectedReloadTab = useRef<string | null | undefined>(undefined);
  const [reloadRejected, setReloadRejected] = useState(false);
  const [refused, setRefused] = useState(false);
  const [externalFailed, setExternalFailed] = useState(false);
  const surface = useRef<HTMLDivElement>(null);
  const addressInput = useRef<HTMLInputElement>(null);
  const selectedChip = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const overflowButton = useRef<HTMLButtonElement>(null);
  const overflowContent = useRef<HTMLElement>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  useEffect(() => {
    if (!active) setToolsOpen(false);
  }, [active]);
  useEffect(() => {
    if (!toolsOpen) return;
    overflowContent.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    const outside = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      if (overflowButton.current?.contains(event.target) || overflowContent.current?.contains(event.target)) return;
      setToolsOpen(false);
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      setToolsOpen(false);
      overflowButton.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [toolsOpen]);
  const native = bridge();
  const tabs = state.tabs ?? [];
  const reloadFailed =
    reloadRejected &&
    (rejectedReloadTab.current === null ? state.selected === undefined : rejectedReloadTab.current === state.selected);
  const clearReloadFailure = useCallback(() => {
    rejectedReloadTab.current = undefined;
    setReloadRejected(false);
  }, []);
  const applyState = useCallback((result: BrowserState, requestRevision?: number) => {
    if (requestRevision !== undefined && requestRevision !== browserRequestRevision.current) return false;
    if (result.revision !== undefined && result.revision < observedBrowserRevision.current) return false;
    if (result.revision !== undefined) observedBrowserRevision.current = result.revision;
    setState(result);
    const failedTab = rejectedReloadTab.current;
    const failedTabClosed =
      failedTab !== undefined &&
      failedTab !== null &&
      result.tabs !== undefined &&
      !result.tabs.some((tab) => tab.id === failedTab);
    const selectionChanged =
      failedTab === null ? result.selected !== undefined : failedTab !== undefined && result.selected !== failedTab;
    if (failedTabClosed || selectionChanged) {
      rejectedReloadTab.current = undefined;
      setReloadRejected(false);
    }
    return true;
  }, []);
  // Agents' tabs have their own limit: only the person's count against the person's.
  const ownTabs = tabs.filter((tab) => !tab.agent).length;
  const request = async (command: Record<string, unknown>) => {
    const revision = ++browserRequestRevision.current;
    if (command.action === "reload" || command.action === "back" || command.action === "forward") clearReloadFailure();
    else if (["navigate", "select-tab", "new-tab", "close-tab"].includes(String(command.action))) clearReloadFailure();
    try {
      const result = await native?.browser(command);
      if (result) applyState(result, revision);
    } catch {
      if (revision !== browserRequestRevision.current) return;
      if (command.action === "reload") {
        rejectedReloadTab.current = state.selected ?? null;
        setReloadRejected(true);
      }
      if (command.action !== "reload") setState((previous) => ({ ...previous, status: "unavailable" }));
    }
  };
  useEffect(() => {
    const started = () => setExternalFailed(false);
    const failed = () => setExternalFailed(true);
    window.addEventListener("heinzel-guest-external-started", started);
    window.addEventListener("heinzel-guest-external-failed", failed);
    return () => {
      window.removeEventListener("heinzel-guest-external-started", started);
      window.removeEventListener("heinzel-guest-external-failed", failed);
    };
  }, []);
  const selectedId = state.selected ?? null;
  const [shownTab, setShownTab] = useState<string | null>(null);
  useEffect(() => {
    if (selectedId) selectedChip.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [selectedId]);
  // Showing another tab shows its own address; typing in the bar is never overwritten by the poll.
  useEffect(() => {
    if (selectedId === shownTab) return;
    setShownTab(selectedId);
    setAddressEdited(false);
    const current = (state.tabs ?? []).find((tab) => tab.id === selectedId);
    // An empty tab owns an empty address too: an older restore may have replaced the optimistic new-tab clear.
    setAddress(current?.url ?? "");
  }, [selectedId, shownTab, state.tabs]);
  const newTab = () => {
    if (!native || ownTabs >= MAX_TABS) return;
    startup.current = true;
    setAddress("");
    setRefused(false);
    void request({ action: "new-tab" }).then(() => addressInput.current?.focus());
  };
  const closeTab = (id: string) => void request({ action: "close-tab", tab: id });
  // The selected tab when an agent opened it: the bar and the commit card are about this tab only.
  const agentTab = tabs.find((tab) => tab.id === selectedId && tab.agent);
  // An answer names the card that is on screen, by its id: the desktop refuses it for any other card on that tab.
  const answer = (action: "commit-approve" | "commit-refuse") => {
    if (agentTab?.commit) void request({ action, tab: agentTab.id, commit: agentTab.commit.id });
  };
  // The listener is bound once per panel state and always calls the latest handler.
  const shortcut = useRef<(event: KeyboardEvent) => void>(() => {});
  shortcut.current = (event) => {
    // On macOS the menu alone owns Cmd (Ctrl-T and Ctrl-W are text editing there); elsewhere Ctrl counts only when
    // the focus is inside this panel, so another panel's terminal or editor keeps its keys.
    if (/mac/i.test(navigator.platform) || !event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    if (!(event.target instanceof Node && panel.current?.contains(event.target))) return;
    const key = event.key.toLowerCase();
    if (key === "t") {
      event.preventDefault();
      newTab();
    } else if (key === "w" && selectedId) {
      event.preventDefault();
      closeTab(selectedId);
    }
  };
  // The Mac menu owns Cmd-T and Cmd-W; it asks the page, and an untaken Close Tab closes the window.
  const menuTab = useRef<(action: string) => boolean>(() => false);
  menuTab.current = (action) => {
    if (action === "new-tab") newTab();
    else if (selectedId) closeTab(selectedId);
    else return false;
    return true;
  };
  useEffect(() => {
    if (!native || !active) return;
    const keys = (event: KeyboardEvent) => shortcut.current(event);
    const menu = (event: Event) => {
      const action = (event as CustomEvent<string>).detail;
      if ((action === "new-tab" || action === "close-tab") && menuTab.current(action)) event.preventDefault();
    };
    document.addEventListener("keydown", keys);
    window.addEventListener(MENU_EVENT, menu);
    return () => {
      document.removeEventListener("keydown", keys);
      window.removeEventListener(MENU_EVENT, menu);
    };
  }, [native, active]);
  const current = tabs.find((tab) => tab.id === selectedId);
  // The bookmark bar draws its star beside the address.
  const [starSlot, setStarSlot] = useState<HTMLElement | null>(null);
  const displayAddress = !addressEdited && state.status === "ready" && state.url ? state.url : address;
  const target = addressUrl(displayAddress);
  const external = target ? externalBrowserUrl(target) : null;
  // The desktop refuses local, private and non-HTTPS targets; say so instead of a silent failure.
  const go = useCallback(
    async (url: string) => {
      startup.current = true;
      const submittedRevision = addressRevision.current;
      const navigation = ++navigationRevision.current;
      const requestRevision = ++browserRequestRevision.current;
      clearReloadFailure();
      try {
        const result = await native?.browser({ action: "navigate", url });
        if (navigation !== navigationRevision.current || requestRevision !== browserRequestRevision.current) return;
        setRefused(false);
        if (result) {
          if (submittedRevision === addressRevision.current) setAddressEdited(false);
          if (result.selected) setShownTab(result.selected);
          applyState(result, requestRevision);
        }
      } catch (error) {
        if (navigation !== navigationRevision.current || requestRevision !== browserRequestRevision.current) return;
        if (String(error).includes("public HTTPS")) {
          if (submittedRevision === addressRevision.current) setRefused(true);
        } else setState((previous) => ({ ...previous, status: "unavailable" }));
      }
    },
    [native, applyState, clearReloadFailure, startup],
  );
  // The workspace keeps first-use state when the user closes and remounts this panel.
  useEffect(() => {
    if (!native) return;
    let disposed = false;
    const requestRevision = browserRequestRevision.current;
    const editedRevision = addressRevision.current;
    void native
      .browser({ action: "tabs" })
      .then((result) => {
        if (disposed || !applyState(result, requestRevision)) return;
        const firstOpen = active && !startup.current;
        if (active) startup.current = true;
        const restored = result.tabs?.find((tab) => tab.selected);
        if (restored) {
          setShownTab(restored.id);
          if (addressRevision.current === editedRevision) {
            setAddressEdited(false);
            setAddress(restored.url);
          }
        } else if (
          firstOpen &&
          result.tabs?.length === 0 &&
          result.status === "empty" &&
          !result.link &&
          !result.notice &&
          addressRevision.current === editedRevision
        ) {
          setAddress(START_PAGE);
          void go(START_PAGE);
        }
      })
      .catch(() => {});
    return () => {
      disposed = true;
    };
  }, [active, native, applyState, go, startup]);
  const surfaceFailed = useNativeSurface(
    surface,
    active,
    native?.browser,
    () => {
      const revision = browserRequestRevision.current;
      void native
        ?.browser({ action: "state" })
        .then((result) => applyState(result, revision))
        .catch(() => {});
    },
    "browser",
  );
  const status =
    state.status === "loading"
      ? t("browser.loading")
      : state.status === "ready"
        ? t("browser.ready")
        : state.status === "empty"
          ? t("browser.empty")
          : t("browser.unavailable");
  const visibleStatus = reloadFailed ? t("browser.unavailable") : status;
  return (
    <section ref={panel} aria-label={t("browser.title")} className="flex h-full min-h-0 flex-col bg-background">
      {native && (
        <div className="flex min-h-11 shrink-0 items-center gap-1 px-2">
          <div
            role="tablist"
            aria-label={t("browser.tabs")}
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
          >
            {tabs.map((tab) => (
              <div
                key={tab.id}
                ref={tab.selected ? selectedChip : undefined}
                className={`flex max-w-40 shrink-0 items-center rounded-md border border-border text-xs ${tab.selected ? "bg-muted" : ""}`}
              >
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  role="tab"
                  aria-selected={tab.selected}
                  title={tab.url || undefined}
                  className="h-9 min-w-0 flex-1 justify-start gap-1 text-left"
                  onClick={() => void request({ action: "select-tab", tab: tab.id })}
                >
                  {tab.agent && <AgentFace kind={tab.agent.kind} />}
                  {tab.favicon && <Favicon image={tab.favicon} className="size-3" />}
                  <span className="min-w-0 flex-1 truncate">{tabLabel(tab)}</span>
                  {tab.commit && (
                    <>
                      <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-destructive" />
                      <span className="sr-only">, {t("browser.commitWaiting")}</span>
                    </>
                  )}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`${t("browser.closeTab")} ${tabLabel(tab)}`}
                  title={t("browser.closeTab")}
                  className="size-8 shrink-0 text-muted-foreground"
                  onClick={() => closeTab(tab.id)}
                >
                  <X aria-hidden className="size-3" />
                </Button>
              </div>
            ))}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-9 shrink-0"
            aria-label={t("browser.newTab")}
            title={t("browser.newTab")}
            disabled={ownTabs >= MAX_TABS}
            onClick={newTab}
          >
            <Plus aria-hidden />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-9 shrink-0"
            aria-label={t("browser.closePanel")}
            title={t("browser.closePanel")}
            onClick={onClose}
          >
            <X aria-hidden />
          </Button>
        </div>
      )}
      <form
        className="flex h-11 shrink-0 items-center gap-0.5 px-2 sm:gap-1"
        onSubmit={(event) => {
          event.preventDefault();
          if (native && target) void go(target);
        }}
      >
        {native && (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0 sm:size-9"
              aria-label={t("browser.back")}
              title={t("browser.back")}
              disabled={!state.canGoBack}
              onClick={() => void request({ action: "back" })}
            >
              <ArrowLeft aria-hidden />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0 sm:size-9"
              aria-label={t("browser.forward")}
              title={t("browser.forward")}
              disabled={!state.canGoForward}
              onClick={() => void request({ action: "forward" })}
            >
              <ArrowRight aria-hidden />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0 sm:size-9"
              aria-label={refreshName(
                t("browser.reload"),
                state.status === "loading",
                reloadFailed || state.status === "unavailable",
              )}
              title={t("browser.reload")}
              disabled={!state.url || state.status === "loading"}
              aria-busy={state.status === "loading"}
              onClick={() => {
                setState((previous) => ({ ...previous, status: "loading" }));
                void request({ action: "reload" });
              }}
            >
              <RefreshCw
                aria-hidden
                className={refreshIcon(state.status === "loading", reloadFailed || state.status === "unavailable")}
              />
            </Button>
          </>
        )}
        <input
          ref={addressInput}
          aria-label={t("browser.address")}
          // Text, not type=url: form validation would refuse "example.com" or a search before it is read.
          type="text"
          inputMode="url"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          value={displayAddress}
          onChange={(event) => {
            startup.current = true;
            addressRevision.current += 1;
            setAddress(event.target.value);
            setAddressEdited(true);
            setRefused(false);
          }}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm"
        />
        {native && bookmarks && <span ref={setStarSlot} className="contents" />}
        {native ? (
          <Button
            type="submit"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 sm:size-9"
            aria-label={t("browser.go")}
            title={t("browser.go")}
            disabled={!target}
          >
            <CornerDownLeft aria-hidden />
          </Button>
        ) : (
          external && (
            <PaneAction ghost icon={ExternalLink} href={external}>
              {t("browser.openExternal")}
            </PaneAction>
          )
        )}
        {native ? (
          <Button
            ref={overflowButton}
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 sm:size-9"
            aria-label={t("browser.moreActions")}
            title={t("browser.moreActions")}
            aria-expanded={toolsOpen}
            onClick={() => setToolsOpen((open) => !open)}
          >
            <Ellipsis aria-hidden />
          </Button>
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t("browser.close")}
            title={t("browser.close")}
            onClick={onClose}
          >
            <X aria-hidden />
          </Button>
        )}
      </form>
      {native && toolsOpen && (
        <section
          ref={overflowContent}
          aria-label={t("browser.moreActions")}
          className="flex shrink-0 flex-wrap items-center gap-1 px-2 py-1"
        >
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-heinzel-browser-external=""
            disabled={!state.url}
            onClick={() => void request({ action: "external" })}
          >
            <ExternalLink aria-hidden />
            {t("browser.openInBrowser")}
          </Button>
          {onSettings && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setToolsOpen(false);
                onSettings();
              }}
            >
              <Settings aria-hidden />
              {t("browser.settings")}
            </Button>
          )}
        </section>
      )}
      {native ? (
        <>
          <output
            className={
              reloadFailed || state.status === "unavailable"
                ? "block px-3 py-1 text-xs text-muted-foreground"
                : "sr-only"
            }
          >
            {visibleStatus}
          </output>
          {bookmarks && (
            <BookmarkBar slot={starSlot} url={current?.url || state.url} title={current?.title ?? ""} tabs={tabs} />
          )}
          {refused && (
            <p role="alert" className="shrink-0 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs">
              {t("browser.refusedNote")}
            </p>
          )}
          {surfaceFailed && active && (
            <p role="alert" className="shrink-0 px-3 py-1.5 text-xs">
              {t("browser.surfaceFailed")}
            </p>
          )}
          {state.link && (
            <div
              role="alert"
              className="flex shrink-0 items-center gap-2 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs"
            >
              <p className="min-w-0 flex-1">
                {t(state.link.reason === "limit" ? "browser.linkLimit" : "browser.linkRefused")}
              </p>
              {state.link.external && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void request({ action: "link-external" })}
                >
                  {t("browser.linkOpenExternal")}
                </Button>
              )}
              <Button type="button" variant="ghost" size="sm" onClick={() => void request({ action: "link-dismiss" })}>
                {t("browser.linkDismiss")}
              </Button>
            </div>
          )}
          {state.notice && (
            <p role="alert" className="shrink-0 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs">
              {t(NOTICES[state.notice])}
            </p>
          )}
          {externalFailed && (
            <p role="alert" className="shrink-0 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs">
              {t("browser.externalFailedNote")}
            </p>
          )}
          {agentTab?.agent && (
            <fieldset
              aria-label={t("browser.agentUsing", { name: agentTab.agent.name })}
              className="m-0 flex min-w-0 shrink-0 flex-wrap items-center gap-2 border-0 border-t-[0.5px] border-foreground/20 bg-muted px-3 py-1.5 text-xs"
            >
              <AgentFace kind={agentTab.agent.kind} />
              <p role="status" className="min-w-0 flex-1">
                {t(agentTab.paused ? "browser.agentPaused" : "browser.agentUsing", { name: agentTab.agent.name })}
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void request({ action: agentTab.paused ? "hand-back" : "take-over", tab: agentTab.id })}
              >
                {t(agentTab.paused ? "browser.handBack" : "browser.takeOver")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                title={t("browser.stopAgentHint", { name: agentTab.agent.name })}
                onClick={() => void request({ action: "stop-agent", tab: agentTab.id })}
              >
                {t("browser.stopAgent")}
              </Button>
            </fieldset>
          )}
          {agentTab?.agent && agentTab.commit && (
            <section
              // A section, not a dialog: a dialog would hide the page the person is asked about.
              aria-label={t("browser.commitAsk", { name: agentTab.agent.name })}
              aria-describedby="browser-commit-summary"
              data-browser-commit
              className="flex shrink-0 flex-wrap items-center gap-2 border-t-[0.5px] border-foreground/20 px-3 py-2 text-sm"
            >
              <div className="min-w-0 flex-1">
                <p className="text-xs text-muted-foreground">{t("browser.commitAsk", { name: agentTab.agent.name })}</p>
                {/* The daemon's own sentence, shown whole and unchanged: the action and its target. */}
                <p id="browser-commit-summary" className="break-words font-medium">
                  {agentTab.commit.summary}
                </p>
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => answer("commit-refuse")}>
                {t("browser.commitDecline")}
              </Button>
              <Button type="button" size="sm" onClick={() => answer("commit-approve")}>
                {t("browser.commitApprove")}
              </Button>
            </section>
          )}
          <div ref={surface} className="min-h-48 flex-1 border-t-[0.5px] border-foreground/20" />
        </>
      ) : (
        <p className="p-4 text-sm text-muted-foreground">{t("browser.webOnly")}</p>
      )}
    </section>
  );
}
