import { useEffect, useState } from "react";

type AgentTab = { agent?: { name: string; kind: string } | null; commit?: { summary: string } | null };
type Desktop = { browser?: (request: Record<string, unknown>) => Promise<{ tabs?: AgentTab[] }> };

/** What the toolbar's Browser button says about agents: one is using a tab, or one waits for the person's answer. */
export type BrowserAgents = "none" | "active" | "waiting";

/** Read from the tab strip the desktop reports. */
export function browserAgentsOf(tabs: readonly AgentTab[] | undefined): BrowserAgents {
  const theirs = (tabs ?? []).filter((tab) => tab.agent);
  if (theirs.some((tab) => tab.commit)) return "waiting";
  return theirs.length ? "active" : "none";
}

/**
 * Whether an agent has a tab in the desktop app's browser, also while the browser panel is closed (ADR 0162).
 * Only the desktop app has such tabs: anywhere else this asks nothing and stays "none".
 */
export function useBrowserAgents(intervalMs = 2000): BrowserAgents {
  const [agents, setAgents] = useState<BrowserAgents>("none");
  useEffect(() => {
    const desktop = (window as Window & { heinzelDesktop?: Desktop }).heinzelDesktop;
    const browser = desktop?.browser;
    if (typeof browser !== "function") return;
    let live = true;
    const ask = () => {
      if (document.hidden) return;
      void browser({ action: "tabs" })
        .then((state) => live && setAgents(browserAgentsOf(state?.tabs)))
        .catch(() => {});
    };
    ask();
    const timer = window.setInterval(ask, intervalMs);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [intervalMs]);
  return agents;
}
