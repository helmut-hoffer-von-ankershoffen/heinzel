import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";

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
    const reload = screen.getByRole("button", { name: "Refresh" });
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "false"));
    fireEvent.click(reload);
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "true"));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    act(() => pending.shift()?.("second"));
    await screen.findByText("second");
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "false"));
  });
});
