import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import {
  type BrowserAgents,
  browserAgentsOf,
  useBrowserAgents,
  useCommitAttention,
  type WaitingCommit,
  waitingCommitOf,
} from "./browserAgents";
import { TopBar } from "./TopBar";

const desktop = vi.hoisted(() => ({ state: "on" }));
vi.mock("@/notifications/desktop", () => ({ desktopState: () => desktop.state }));

afterEach(() => {
  Reflect.deleteProperty(window, "heinzelDesktop");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  desktop.state = "on";
});

const agent = { agent: { name: "Ada", kind: "heinzel" } };

it("reads the mark from the tab strip: none, an agent at work, an agent waiting for an answer", () => {
  expect(browserAgentsOf(undefined)).toBe("none");
  expect(browserAgentsOf([{}, { agent: null }])).toBe("none");
  expect(browserAgentsOf([{}, agent])).toBe("active");
  expect(browserAgentsOf([agent, { ...agent, commit: { summary: "Click" } }])).toBe("waiting");
  // A card belongs to an agent's tab: a person's tab never raises the mark.
  expect(browserAgentsOf([{ commit: { summary: "Click" } }])).toBe("none");
});

it("asks the desktop for its tabs while the panel is closed, and follows them", async () => {
  let tabs: unknown[] = [];
  const browser = vi.fn(async (_request: Record<string, unknown>) => ({ status: "empty", tabs }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  const { result, unmount } = renderHook(() => useBrowserAgents(20));
  expect(result.current).toEqual({ mark: "none", waiting: null });
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "tabs" }));
  tabs = [agent];
  await waitFor(() => expect(result.current.mark).toBe("active"));
  tabs = [{ ...agent, id: "t-000000aa", commit: { summary: "Click", id: "a".repeat(16) } }];
  await waitFor(() => expect(result.current.mark).toBe("waiting"));
  const card = result.current.waiting;
  expect(card).toEqual({ tab: "t-000000aa", card: "a".repeat(16), name: "Ada", summary: "Click" });
  // The same card stays the same object over the next polls, so it is acted on once.
  const polls = browser.mock.calls.length;
  await waitFor(() => expect(browser.mock.calls.length).toBeGreaterThan(polls + 1));
  expect(result.current.waiting).toBe(card);
  // The next card on the same tab with the very same sentence is another card.
  tabs = [{ ...agent, id: "t-000000aa", commit: { summary: "Click", id: "b".repeat(16) } }];
  await waitFor(() => expect(result.current.waiting?.card).toBe("b".repeat(16)));
  expect(result.current.waiting).not.toBe(card);
  tabs = [];
  await waitFor(() => expect(result.current).toEqual({ mark: "none", waiting: null }));
  // Only ever a read of the tab strip: nothing here opens, selects or answers.
  expect(new Set(browser.mock.calls.map(([request]) => JSON.stringify(request)))).toEqual(
    new Set(['{"action":"tabs"}']),
  );
  unmount();
  const asked = browser.mock.calls.length;
  await new Promise((resolve) => setTimeout(resolve, 60));
  expect(browser.mock.calls.length).toBe(asked);
});

it("a desktop that fails to answer leaves the mark as it was", async () => {
  const browser = vi.fn(async () => {
    throw new Error("closed");
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  const { result } = renderHook(() => useBrowserAgents(20));
  await waitFor(() => expect(browser).toHaveBeenCalled());
  expect(result.current).toEqual({ mark: "none", waiting: null });
});

it("the PWA asks nothing and shows no mark", async () => {
  const { result } = renderHook(() => useBrowserAgents(20));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(result.current).toEqual({ mark: "none", waiting: null });
});

function bar(browserAgents?: BrowserAgents, browserOpen = false) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <TopBar
          chatOpen={false}
          onToggleChat={() => {}}
          terminalOpen={false}
          onToggleTerminal={() => {}}
          calendarOpen={false}
          onOpenCalendar={() => {}}
          onToggleBrowser={() => {}}
          browserOpen={browserOpen}
          browserAgents={browserAgents}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

it("the Browser button carries a mark while an agent has a tab, also with the panel closed", () => {
  const { rerender } = render(bar());
  expect(screen.getByRole("button", { name: "Open the browser" })).toBeVisible();
  expect(screen.queryByTestId("browser-agents-mark")).toBeNull();
  rerender(bar("active"));
  expect(screen.getByRole("button", { name: "Open the browser: an agent is using a tab" })).toContainElement(
    screen.getByTestId("browser-agents-mark"),
  );
  rerender(bar("waiting"));
  expect(screen.getByRole("button", { name: "Open the browser: an agent is waiting for your answer" })).toBeVisible();
  rerender(bar("active", true));
  expect(screen.getByRole("button", { name: "Close the browser: an agent is using a tab" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  rerender(bar("none"));
  expect(screen.queryByTestId("browser-agents-mark")).toBeNull();
});

it("names the waiting card's tab, agent and sentence; a person's tab never has one", () => {
  expect(waitingCommitOf(undefined)).toBeNull();
  expect(waitingCommitOf([agent, { id: "t-00000001", commit: { summary: "Click", id: "c".repeat(16) } }])).toBeNull();
  // A card without an id cannot be answered, so it is not brought to the person as one.
  expect(waitingCommitOf([{ ...agent, id: "t-000000aa", commit: { summary: "Click" } }])).toBeNull();
  expect(waitingCommitOf([{ ...agent, id: "t-000000aa", commit: { summary: "Click", id: "c".repeat(16) } }])).toEqual({
    tab: "t-000000aa",
    card: "c".repeat(16),
    name: "Ada",
    summary: "Click",
  });
});

const CARD: WaitingCommit = {
  tab: "t-000000aa",
  card: "a".repeat(16),
  name: "ci-bot",
  summary: 'Click button "Buy now" on shop.example.com',
};

function attention() {
  const browser = vi.fn(async () => ({ status: "ready" }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  const shown: { title: string; options?: NotificationOptions; onclick?: () => void; close: () => void }[] = [];
  vi.stubGlobal(
    "Notification",
    class {
      onclick?: () => void;
      close = vi.fn();
      constructor(
        public title: string,
        public options?: NotificationOptions,
      ) {
        shown.push(this);
      }
    },
  );
  const show = vi.fn();
  return { browser, shown, show };
}

it("a commit that waits while the browser panel is closed opens the panel on that tab and notifies once", () => {
  const { browser, shown, show } = attention();
  const { rerender } = renderHook(
    ({ waiting, open }: { waiting: WaitingCommit | null; open: boolean }) => useCommitAttention(waiting, open, show),
    { initialProps: { waiting: null as WaitingCommit | null, open: false } },
  );
  expect(show).not.toHaveBeenCalled();
  expect(shown).toHaveLength(0);
  rerender({ waiting: CARD, open: false });
  expect(show).toHaveBeenCalledTimes(1);
  expect(browser.mock.calls).toEqual([[{ action: "select-tab", tab: CARD.tab }]]);
  expect(shown.map((notification) => notification.title)).toEqual(["ci-bot waits for your OK in the browser"]);
  // Opening never answers the card: only the person's click on Approve or Decline does.
  expect(JSON.stringify(browser.mock.calls)).not.toMatch(/commit-/);
  // The panel is open now, the card is the same: nothing is repeated, also when the person closes the panel again.
  rerender({ waiting: CARD, open: true });
  rerender({ waiting: CARD, open: false });
  expect(show).toHaveBeenCalledTimes(1);
  expect(shown).toHaveLength(1);
  // A click on the notification brings the panel back.
  shown[0]?.onclick?.();
  expect(show).toHaveBeenCalledTimes(2);
  // The next card is a new one.
  rerender({ waiting: null, open: false });
  rerender({ waiting: { ...CARD, card: "b".repeat(16), summary: 'Click button "Pay"' }, open: false });
  expect(show).toHaveBeenCalledTimes(3);
  expect(shown).toHaveLength(2);
});

it("with the panel in front and the window focused, the card is already in view: nothing opens and nothing pops up", () => {
  const { browser, shown, show } = attention();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  renderHook(() => useCommitAttention(CARD, true, show));
  expect(show).not.toHaveBeenCalled();
  expect(browser).not.toHaveBeenCalled();
  expect(shown).toHaveLength(0);
});

it("the panel in front of an unfocused window still notifies, and leaves the person's tab selection alone", () => {
  const { browser, shown, show } = attention();
  vi.spyOn(document, "hasFocus").mockReturnValue(false);
  renderHook(() => useCommitAttention(CARD, true, show));
  expect(show).not.toHaveBeenCalled();
  expect(browser).not.toHaveBeenCalled();
  expect(shown).toHaveLength(1);
});

it("notifications turned off: the panel still opens, nothing pops up", () => {
  const { shown, show } = attention();
  desktop.state = "off";
  renderHook(() => useCommitAttention(CARD, false, show));
  expect(show).toHaveBeenCalledTimes(1);
  expect(shown).toHaveLength(0);
});

it("a refused notification leaves the open panel as the signal", () => {
  const { show } = attention();
  vi.stubGlobal(
    "Notification",
    class {
      constructor() {
        throw new Error("not allowed");
      }
    },
  );
  expect(() => renderHook(() => useCommitAttention(CARD, false, show))).not.toThrow();
  expect(show).toHaveBeenCalledTimes(1);
});

it("the PWA opens nothing and pops up nothing, whatever it is told", () => {
  const show = vi.fn();
  const created = vi.fn();
  vi.stubGlobal(
    "Notification",
    class {
      constructor() {
        created();
      }
    },
  );
  renderHook(() => useCommitAttention(CARD, false, show));
  expect(show).not.toHaveBeenCalled();
  expect(created).not.toHaveBeenCalled();
});

it("a new card with the same sentence on the same tab opens the panel and notifies again", async () => {
  const { shown, show } = attention();
  let tabs = [{ ...agent, id: "t-000000aa", commit: { summary: "Click", id: "a".repeat(16) } }];
  const browser = vi.fn(async (_request: Record<string, unknown>) => ({ status: "ready", tabs }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  renderHook(() => useCommitAttention(useBrowserAgents(20).waiting, false, show));
  await waitFor(() => expect(show).toHaveBeenCalledTimes(1));
  expect(shown).toHaveLength(1);
  // Several polls of the same card change nothing.
  const polls = sent(browser).length;
  await waitFor(() => expect(sent(browser).length).toBeGreaterThan(polls + 1));
  expect(show).toHaveBeenCalledTimes(1);
  // Card A timed out and the agent asked again at once, in the same words: no poll saw the tab without a card.
  tabs = [{ ...agent, id: "t-000000aa", commit: { summary: "Click", id: "b".repeat(16) } }];
  await waitFor(() => expect(show).toHaveBeenCalledTimes(2));
  expect(shown.map((notification) => notification.options?.tag)).toEqual([
    `hz-commit-${"a".repeat(16)}`,
    `hz-commit-${"b".repeat(16)}`,
  ]);
});

function sent(browser: ReturnType<typeof vi.fn>) {
  return browser.mock.calls.filter(([request]) => (request as { action?: string }).action === "tabs");
}
