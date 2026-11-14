import { ArrowLeft, ArrowRight, CornerDownLeft, ExternalLink, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PaneAction } from "@/components/PaneActions";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { viewportBounds } from "@/lib/viewport";

type BrowserState = {
  status: string;
  url?: string;
  canGoBack?: boolean;
  canGoForward?: boolean;
  notice?: "passkey" | "no-device" | "clear-failed" | null;
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
  const host = input.split(/[/?#]/, 1)[0];
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
export function BrowserPanel({ active, onClose }: Readonly<{ active: boolean; onClose: () => void }>) {
  const [address, setAddress] = useState("https://heinzel.team");
  const [state, setState] = useState<BrowserState>({ status: "empty" });
  const [refused, setRefused] = useState(false);
  const surface = useRef<HTMLDivElement>(null);
  const native = bridge();
  const request = async (command: Record<string, unknown>) => {
    try {
      const result = await native?.browser(command);
      if (result) setState(result);
    } catch {
      setState({ status: "unavailable" });
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
  useEffect(() => {
    if (!native) return;
    const update = () => {
      const element = surface.current;
      const overlay = Array.from(
        document.querySelectorAll(
          '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [role="tooltip"], dialog[open], .viewport-popover, [popover]',
        ),
      ).some((node) => node.getClientRects().length > 0 && !node.closest("[hidden], [inert]"));
      if (!active || !element || element.closest("[hidden], [inert]") || overlay || document.hidden) {
        void native.browser({ action: "hide" }).catch(() => {});
        return;
      }
      const box = element.getBoundingClientRect();
      // The native surface must stay inside the visible viewport; anything else hides it rather than overflowing.
      const view = viewportBounds();
      if (
        box.width < 1 ||
        box.height < 1 ||
        box.x < view.left ||
        box.y < view.top ||
        box.right > view.left + view.width ||
        box.bottom > view.top + view.height
      ) {
        void native.browser({ action: "hide" }).catch(() => {});
        return;
      }
      void native
        .browser({
          action: "layout",
          bounds: {
            x: Math.round(box.x),
            y: Math.round(box.y),
            width: Math.floor(box.width),
            height: Math.floor(box.height),
          },
        })
        .catch(() => {});
    };
    const observer = new ResizeObserver(update);
    if (surface.current) observer.observe(surface.current);
    const changes = new MutationObserver(update);
    changes.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["hidden", "inert", "open", "role", "class", "style"],
    });
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    document.addEventListener("visibilitychange", update);
    document.addEventListener("toggle", update, true);
    const timer = window.setInterval(() => {
      void native
        .browser({ action: "state" })
        .then(setState)
        .catch(() => {});
      update();
    }, 500);
    update();
    return () => {
      observer.disconnect();
      changes.disconnect();
      window.clearInterval(timer);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      document.removeEventListener("visibilitychange", update);
      document.removeEventListener("toggle", update, true);
      void native.browser({ action: "hide" }).catch(() => {});
    };
  }, [active, native]);
  useEffect(
    () => () => {
      void native?.browser({ action: "close" }).catch(() => {});
    },
    [native],
  );
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
      else setState({ status: "unavailable" });
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
    <section aria-label={t("browser.title")} className="flex h-full min-h-80 flex-col bg-background">
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
          {refused && (
            <p role="alert" className="shrink-0 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs">
              {t("browser.refusedNote")}
            </p>
          )}
          {state.notice && (
            <p role="alert" className="shrink-0 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs">
              {t(NOTICES[state.notice])}
            </p>
          )}
          <div ref={surface} className="min-h-48 flex-1 border-t-[0.5px] border-foreground/20" />
          <div className="flex shrink-0 items-center gap-2 px-3 py-1 text-xs text-muted-foreground">
            <p className="min-w-0 flex-1">{t("browser.private")}</p>
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
