import { useEffect, useRef, useState } from "react";
import { t } from "@/i18n";
import { desktopState } from "@/notifications/desktop";

type AgentTab = { id?: string; agent?: { name: string; kind: string } | null; commit?: { summary: string } | null };
type Browser = (request: Record<string, unknown>) => Promise<{ tabs?: AgentTab[] }>;

/** What the toolbar's Browser button says about agents: one is using a tab, or one waits for the person's answer. */
export type BrowserAgents = "none" | "active" | "waiting";
/** The agent tab whose commit card waits for the person. */
export type WaitingCommit = { tab: string; name: string; summary: string };

function desktopBrowser(): Browser | undefined {
  const browser = (window as Window & { heinzelDesktop?: { browser?: Browser } }).heinzelDesktop?.browser;
  return typeof browser === "function" ? browser : undefined;
}

/** Read from the tab strip the desktop reports. */
export function browserAgentsOf(tabs: readonly AgentTab[] | undefined): BrowserAgents {
  const theirs = (tabs ?? []).filter((tab) => tab.agent);
  if (theirs.some((tab) => tab.commit)) return "waiting";
  return theirs.length ? "active" : "none";
}

/** The first agent tab with a commit card, or null. A person's tab never has one. */
export function waitingCommitOf(tabs: readonly AgentTab[] | undefined): WaitingCommit | null {
  const tab = (tabs ?? []).find((entry) => entry.agent && entry.commit && typeof entry.id === "string");
  return tab?.agent && tab.commit && tab.id ? { tab: tab.id, name: tab.agent.name, summary: tab.commit.summary } : null;
}

/**
 * Whether an agent has a tab in the desktop app's browser, also while the browser panel is closed (ADR 0162), and
 * which tab waits for the person's answer. Only the desktop app has such tabs: anywhere else this asks nothing.
 */
export function useBrowserAgents(intervalMs = 2000): { mark: BrowserAgents; waiting: WaitingCommit | null } {
  const [mark, setMark] = useState<BrowserAgents>("none");
  const [waiting, setWaiting] = useState<WaitingCommit | null>(null);
  useEffect(() => {
    const browser = desktopBrowser();
    if (!browser) return;
    let live = true;
    const ask = () => {
      void browser({ action: "tabs" })
        .then((state) => {
          if (!live) return;
          setMark(browserAgentsOf(state?.tabs));
          const next = waitingCommitOf(state?.tabs);
          // The same card keeps the same object, so it is acted on once.
          setWaiting((previous) =>
            previous && next && previous.tab === next.tab && previous.summary === next.summary ? previous : next,
          );
        })
        .catch(() => {});
    };
    ask();
    const timer = window.setInterval(ask, intervalMs);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [intervalMs]);
  return { mark, waiting };
}

/**
 * A commit card must not time out unseen: when one starts to wait while the browser panel is not shown, show the
 * panel on that agent's tab, and tell the person once with a notification. `shown` is whether the panel is in front;
 * `show` brings it there. Once per card: a panel the person closes again stays closed.
 */
export function useCommitAttention(waiting: WaitingCommit | null, shown: boolean, show: () => void): void {
  const latest = useRef({ shown, show });
  latest.current = { shown, show };
  useEffect(() => {
    const browser = desktopBrowser();
    if (!waiting || !browser) return;
    const away = !latest.current.shown || !document.hasFocus();
    if (!latest.current.shown) {
      latest.current.show();
      void browser({ action: "select-tab", tab: waiting.tab }).catch(() => {});
    }
    if (!away || desktopState() !== "on") return;
    try {
      const notification = new Notification(t("browser.commitNotice", { name: waiting.name }), {
        tag: `hz-commit-${waiting.tab}`,
      });
      notification.onclick = () => {
        window.focus();
        latest.current.show();
        notification.close();
      };
    } catch {
      // The open panel and the mark on the toolbar remain when the system shows no notification.
    }
  }, [waiting]);
}
