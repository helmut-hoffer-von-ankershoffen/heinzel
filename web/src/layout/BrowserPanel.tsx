import { ArrowLeft, ArrowRight, Bot, CornerDownLeft, ExternalLink, Plus, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { MENU_EVENT } from "@/about/menu";
import { BookmarkBar } from "@/bookmarks/BookmarkStar";
import { Favicon } from "@/bookmarks/Favicon";
import { HeinzelFace } from "@/components/HeinzelFace";
import { PaneAction } from "@/components/PaneActions";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { useNativeSurface } from "@/lib/nativeSurface";

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
  commit?: { summary: string } | null;
};
/** The person's own tabs per window, as the desktop enforces. */
const MAX_TABS = 8;
type BrowserState = {
  tabs?: BrowserTab[];
  selected?: string | null;
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
  bookmarks = false,
}: Readonly<{
  active: boolean;
  onClose: () => void /** Show the bookmark star; needs the app's providers. */;
  bookmarks?: boolean;
}>) {
  const [address, setAddress] = useState("https://heinzel.team");
  const [state, setState] = useState<BrowserState>({ status: "empty" });
  const [refused, setRefused] = useState(false);
  const surface = useRef<HTMLDivElement>(null);
  const addressInput = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLElement>(null);
  const native = bridge();
  const tabs = state.tabs ?? [];
  // Agents' tabs have their own limit: only the person's count against the person's.
  const ownTabs = tabs.filter((tab) => !tab.agent).length;
  const request = async (command: Record<string, unknown>) => {
    try {
      const result = await native?.browser(command);
      if (result) setState(result);
    } catch {
      setState((previous) => ({ ...previous, status: "unavailable" }));
    }
  };
  // A failed wipe must stay visible, also when no page is open yet.
  const clearData = async () => {
    try {
      const result = await native?.browser({ action: "clear" });
      if (result) setState(result);
    } catch {
      setState((previous) => ({ ...previous, notice: "clear-failed" }));
    }
  };
  // Tabs live in the desktop, not in this panel: closing the panel only hides them, and reopening shows them again.
  useEffect(() => {
    void native
      ?.browser({ action: "tabs" })
      .then((result) => {
        setState(result);
        const current = result.tabs?.find((tab) => tab.selected);
        if (current?.url) setAddress(current.url);
      })
      .catch(() => {});
  }, [native]);
  useNativeSurface(surface, active, native?.browser, () => {
    void native
      ?.browser({ action: "state" })
      .then(setState)
      .catch(() => {});
  });
  const selectedId = state.selected ?? null;
  const [shownTab, setShownTab] = useState<string | null>(null);
  // Showing another tab shows its own address; typing in the bar is never overwritten by the poll.
  useEffect(() => {
    if (selectedId === shownTab) return;
    setShownTab(selectedId);
    const current = (state.tabs ?? []).find((tab) => tab.id === selectedId);
    if (current?.url) setAddress(current.url);
  }, [selectedId, shownTab, state.tabs]);
  const newTab = () => {
    if (!native || ownTabs >= MAX_TABS) return;
    setAddress("");
    setRefused(false);
    void request({ action: "new-tab" }).then(() => addressInput.current?.focus());
  };
  const closeTab = (id: string) => void request({ action: "close-tab", tab: id });
  // The selected tab when an agent opened it: the bar and the commit card are about this tab only.
  const agentTab = tabs.find((tab) => tab.id === selectedId && tab.agent);
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
  // The star's place inside the address form; the bookmark bar draws into it from below the tab strip.
  const [starSlot, setStarSlot] = useState<HTMLElement | null>(null);
  const target = addressUrl(address);
  const external = target ? externalBrowserUrl(target) : null;
  // The desktop refuses local, private and non-HTTPS targets; say so instead of a silent failure.
  const go = async (url: string) => {
    try {
      const result = await native?.browser({ action: "navigate", url });
      setRefused(false);
      if (result) setState(result);
    } catch (error) {
      if (String(error).includes("public HTTPS")) setRefused(true);
      else setState((previous) => ({ ...previous, status: "unavailable" }));
    }
  };
  const status =
    state.status === "loading"
      ? t("browser.loading")
      : state.status === "ready"
        ? t("browser.ready")
        : state.status === "empty"
          ? t("browser.empty")
          : t("browser.unavailable");
  return (
    <section ref={panel} aria-label={t("browser.title")} className="flex h-full min-h-80 flex-col bg-background">
      <form
        className="flex h-10 shrink-0 items-center gap-1 px-2"
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
              aria-label={t("browser.reload")}
              title={t("browser.reload")}
              disabled={!state.url || state.status === "loading"}
              onClick={() => void request({ action: "reload" })}
            >
              <RefreshCw aria-hidden className={state.status === "loading" ? "animate-spin" : undefined} />
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
          value={address}
          onChange={(event) => {
            setAddress(event.target.value);
            setRefused(false);
          }}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm"
        />
        {native && bookmarks && <span ref={setStarSlot} className="contents" />}
        {native && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t("browser.openInBrowser")}
            title={t("browser.openInBrowser")}
            disabled={!state.url}
            onClick={() => void request({ action: "external" })}
          >
            <ExternalLink aria-hidden />
          </Button>
        )}
        {native ? (
          <Button
            type="submit"
            variant="ghost"
            size="icon"
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
      </form>
      {native ? (
        <>
          <div
            role="tablist"
            aria-label={t("browser.tabs")}
            className="flex shrink-0 items-center gap-1 overflow-x-auto px-2"
          >
            {tabs.map((tab) => (
              <div
                key={tab.id}
                className={`flex max-w-40 shrink-0 items-center rounded-md border border-border text-xs ${tab.selected ? "bg-muted" : ""}`}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab.selected}
                  title={tab.url || undefined}
                  className="flex min-w-0 flex-1 items-center gap-1 px-2 py-1 text-left"
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
                </button>
                <button
                  type="button"
                  aria-label={`${t("browser.closeTab")} ${tabLabel(tab)}`}
                  title={t("browser.closeTab")}
                  className="px-1 py-1 text-muted-foreground"
                  onClick={() => closeTab(tab.id)}
                >
                  <X aria-hidden className="size-3" />
                </button>
              </div>
            ))}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("browser.newTab")}
              title={t("browser.newTab")}
              disabled={ownTabs >= MAX_TABS}
              onClick={newTab}
            >
              <Plus aria-hidden />
            </Button>
          </div>
          <p role="status" className="truncate px-3 pb-1 text-xs text-muted-foreground" title={state.url || undefined}>
            {state.url && (state.status === "ready" || state.status === "loading") ? (
              <>
                <span className="sr-only">{t("browser.current")} </span>
                {state.url}
              </>
            ) : (
              status
            )}
          </p>
          {bookmarks && (
            <BookmarkBar slot={starSlot} url={current?.url || state.url} title={current?.title ?? ""} tabs={tabs} />
          )}
          {refused && (
            <p role="alert" className="shrink-0 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs">
              {t("browser.refusedNote")}
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
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void request({ action: "commit-refuse", tab: agentTab.id })}
              >
                {t("browser.commitDecline")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => void request({ action: "commit-approve", tab: agentTab.id })}
              >
                {t("browser.commitApprove")}
              </Button>
            </section>
          )}
          <div ref={surface} className="min-h-48 flex-1 border-t-[0.5px] border-foreground/20" />
          <div className="flex shrink-0 items-center gap-2 px-3 py-1 text-xs text-muted-foreground">
            <p className="min-w-0 flex-1">
              {t("browser.private")} {t("browser.automationNote")}
            </p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              title={t("browser.clearDataHint")}
              onClick={() => void clearData()}
            >
              {t("browser.clearData")}
            </Button>
          </div>
        </>
      ) : (
        <p className="p-4 text-sm text-muted-foreground">{t("browser.webOnly")}</p>
      )}
    </section>
  );
}
