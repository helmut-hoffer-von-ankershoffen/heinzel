import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";

vi.mock("@/api/queries", () => ({ usePlayful: () => false }));
vi.mock("@/components/Contributions", () => ({ HeaderActions: () => null }));

function bar(chatOpen: boolean) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <TopBar
          chatOpen={chatOpen}
          onToggleChat={() => {}}
          terminalOpen={false}
          onToggleTerminal={() => {}}
          calendarOpen={false}
          onOpenCalendar={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("TopBar chat button", () => {
  it("places calendar and reload after terminal and before the mascot", () => {
    render(bar(false));
    const buttons = screen.getAllByRole("button");
    const terminal = buttons.findIndex((button) => /terminal/i.test(button.getAttribute("aria-label") ?? ""));
    const calendar = buttons.findIndex((button) => button.getAttribute("aria-label") === "Open calendar");
    const reload = buttons.findIndex((button) => /reload|refresh/i.test(button.getAttribute("aria-label") ?? ""));
    const chat = buttons.findIndex((button) => /chat/i.test(button.getAttribute("aria-label") ?? ""));
    expect(terminal).toBeGreaterThanOrEqual(0);
    expect([calendar, reload, chat]).toEqual([terminal + 1, terminal + 2, terminal + 3]);
  });
  it("puts the browser next to the terminal and keeps reload last before the mascot", () => {
    render(
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
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const labels = screen.getAllByRole("button").map((button) => button.getAttribute("aria-label") ?? "");
    const at = (pattern: RegExp) => labels.findIndex((label) => pattern.test(label));
    const terminal = at(/terminal/i);
    expect([at(/browser/i), at(/^Open calendar$/), at(/refresh/i), at(/chat/i)]).toEqual([
      terminal + 1,
      terminal + 2,
      terminal + 3,
      terminal + 4,
    ]);
  });
  it("is a Heinzelmännchen head with no background, open shown by a dot and aria-pressed", () => {
    const { container, rerender } = render(bar(false));
    const button = screen.getByRole("button", { name: /chat/i });
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(button.className).not.toContain("bg-accent");
    expect(container.querySelector('svg[data-view="head"]')).not.toBeNull();
    expect(screen.getByTestId("chat-open-dot")).toHaveClass("opacity-0");
    rerender(bar(true));
    expect(screen.getByRole("button", { name: /chat/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("chat-open-dot")).toHaveClass("opacity-100");
  });
});

function Probe({ fetcher }: { fetcher: () => Promise<string> }) {
  const { data } = useQuery({ queryKey: ["probe"], queryFn: fetcher, staleTime: Number.POSITIVE_INFINITY });
  return <output>{data}</output>;
}

describe("TopBar reload", () => {
  it("refetches even fresh queries on screen and shows the refetch while it runs", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const pending: ((value: string) => void)[] = [];
    const fetcher = vi.fn(() => new Promise<string>((resolve) => pending.push(resolve)));
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Probe fetcher={fetcher} />
          <TopBar
            chatOpen={false}
            onToggleChat={() => {}}
            terminalOpen={false}
            onToggleTerminal={() => {}}
            calendarOpen={false}
            onOpenCalendar={() => {}}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    act(() => pending.shift()?.("first"));
    await screen.findByText("first");
    const reload = screen.getByRole("button", { name: "Refresh data" });
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "false"));
    fireEvent.click(reload);
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "true"));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    act(() => pending.shift()?.("second"));
    await screen.findByText("second");
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "false"));
  });
});

it("distinguishes explicit refreshes from overlapping background work", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let finishManual!: () => void;
  let finishBackground!: (value: string) => void;
  vi.spyOn(client, "invalidateQueries").mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finishManual = resolve;
      }),
  );
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TopBar
          chatOpen={false}
          onToggleChat={() => {}}
          terminalOpen={false}
          onToggleTerminal={() => {}}
          calendarOpen={false}
          onOpenCalendar={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const button = screen.getByRole("button", { name: "Refresh data" });
  fireEvent.focus(button);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Refresh data");
  act(() => {
    void client.fetchQuery({
      queryKey: ["background"],
      queryFn: () =>
        new Promise<string>((resolve) => {
          finishBackground = resolve;
        }),
    });
  });
  await waitFor(() => expect(button).toHaveAttribute("aria-label", "Refreshing data in the background"));
  expect(button).toHaveAttribute("aria-busy", "true");
  expect(screen.getByRole("status")).toHaveTextContent("Refreshing data in the background");
  fireEvent.click(button);
  await waitFor(() => expect(button).toHaveAttribute("aria-label", "Refreshing data…"));
  await waitFor(() => expect(finishManual).toBeDefined());
  await act(async () => {
    finishManual();
  });
  await waitFor(() => expect(button).toHaveAttribute("aria-label", "Refreshing data in the background"));
  await act(async () => {
    finishBackground("done");
  });
  await waitFor(() => expect(button).toHaveAttribute("aria-label", "Refresh data"));
  expect(button).toHaveAttribute("aria-busy", "false");
  expect(button.querySelector("svg")).not.toHaveClass("animate-spin");
});

it("shows a hoverable tooltip and dismisses it on Escape, click and blur", () => {
  render(bar(false));
  const button = screen.getByRole("button", { name: "Refresh data" });
  const wrapper = button.parentElement;
  if (!wrapper) throw new Error("Refresh button has no tooltip wrapper");
  fireEvent.mouseEnter(wrapper);
  const tooltip = screen.getByRole("tooltip");
  expect(tooltip).not.toHaveClass("pointer-events-none");
  fireEvent.mouseOver(tooltip, { relatedTarget: button });
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  fireEvent.mouseLeave(wrapper);
  fireEvent.focus(button);
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  fireEvent.click(button);
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  fireEvent.blur(button);
  fireEvent.focus(button);
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  fireEvent.blur(button);
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});

it("keeps manual busy until repeated refreshes settle, including a rejection", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const pending: { resolve: () => void; reject: (error: Error) => void }[] = [];
  vi.spyOn(client, "invalidateQueries").mockImplementation(
    () => new Promise<void>((resolve, reject) => pending.push({ resolve, reject })),
  );
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TopBar
          chatOpen={false}
          onToggleChat={() => {}}
          terminalOpen={false}
          onToggleTerminal={() => {}}
          calendarOpen={false}
          onOpenCalendar={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const button = screen.getByRole("button", { name: "Refresh data" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(pending).toHaveLength(2));
  const [first, second] = pending;
  if (!first || !second) throw new Error("Expected two pending refreshes");
  await act(async () => first.reject(new Error("refresh failed")));
  expect(button).toHaveAttribute("aria-busy", "true");
  expect(button).toHaveAttribute("aria-label", "Refreshing data…");
  await act(async () => second.resolve());
  await waitFor(() => expect(button).toHaveAttribute("aria-busy", "false"));
  expect(button).toHaveAttribute("aria-label", "Refresh data");
});
