import { ArrowLeft, ArrowRight, CornerDownLeft, ExternalLink, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { viewportBounds } from "@/lib/viewport";

type BrowserState = { status: string; url?: string; canGoBack?: boolean; canGoForward?: boolean };
type BrowserBridge = { browser: (request: Record<string, unknown>) => Promise<BrowserState> };
function bridge(): BrowserBridge | undefined {
  const value = (window as Window & { heinzelDesktop?: Partial<BrowserBridge> }).heinzelDesktop;
  return typeof value?.browser === "function" ? (value as BrowserBridge) : undefined;
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
 * Switching panel tabs only hides the surface, so the page and its state stay; closing the panel discards the session.
 */
export function BrowserPanel({ active, onClose }: Readonly<{ active: boolean; onClose: () => void }>) {
  const [address, setAddress] = useState("https://heinzel.team");
  const [state, setState] = useState<BrowserState>({ status: "empty" });
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
  const external = externalBrowserUrl(address);
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
          if (native && external) void request({ action: "navigate", url: external });
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
          type="url"
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm"
        />
        {native ? (
          <Button
            type="submit"
            variant="ghost"
            size="icon"
            aria-label={t("browser.go")}
            title={t("browser.go")}
            disabled={!external}
          >
            <CornerDownLeft aria-hidden />
          </Button>
        ) : (
          external && (
            <Button
              asChild
              variant="ghost"
              size="icon"
              aria-label={t("browser.openExternal")}
              title={t("browser.openExternal")}
            >
              <a href={external} target="_blank" rel="noopener noreferrer">
                <ExternalLink aria-hidden />
              </a>
            </Button>
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
          <div ref={surface} className="min-h-48 flex-1 border-t-[0.5px] border-foreground/20" />
          <p className="shrink-0 px-3 py-1 text-xs text-muted-foreground">{t("browser.private")}</p>
        </>
      ) : (
        <p className="p-4 text-sm text-muted-foreground">{t("browser.webOnly")}</p>
      )}
    </section>
  );
}
