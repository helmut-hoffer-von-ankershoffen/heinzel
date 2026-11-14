import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { type BrowserAgents, browserAgentsOf, useBrowserAgents } from "./browserAgents";
import { TopBar } from "./TopBar";

afterEach(() => {
  Reflect.deleteProperty(window, "heinzelDesktop");
  vi.useRealTimers();
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
  const browser = vi.fn(async () => ({ status: "empty", tabs }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  const { result, unmount } = renderHook(() => useBrowserAgents(20));
  expect(result.current).toBe("none");
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "tabs" }));
  tabs = [agent];
  await waitFor(() => expect(result.current).toBe("active"));
  tabs = [{ ...agent, commit: { summary: "Click" } }];
  await waitFor(() => expect(result.current).toBe("waiting"));
  tabs = [];
  await waitFor(() => expect(result.current).toBe("none"));
  // Only ever a read of the tab strip: nothing here opens, selects or answers.
  expect(new Set(browser.mock.calls.map(([request]) => JSON.stringify(request)))).toEqual(new Set(['{"action":"tabs"}']));
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
  expect(result.current).toBe("none");
});

it("the PWA asks nothing and shows no mark", async () => {
  const { result } = renderHook(() => useBrowserAgents(20));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(result.current).toBe("none");
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
