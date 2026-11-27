import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
    expect(reload).toHaveAttribute("title", "Refresh");
    fireEvent.click(reload);
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "true"));
    // While it spins the tooltip says why; the accessible name stays the action.
    expect(reload).toHaveAttribute("title", "Getting the latest data…");
    expect(reload).toHaveAccessibleName("Refresh");
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    act(() => pending.shift()?.("second"));
    await screen.findByText("second");
    await waitFor(() => expect(reload).toHaveAttribute("aria-busy", "false"));
  });
});

function compactBar() {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Number.POSITIVE_INFINITY, retry: false } },
  });
  client.setQueryData(
    ["ui", "actions"],
    [
      {
        id: "clock:team",
        slots: { header: true },
        label: "Team clock",
        icon: "clock",
        refresh: 300,
        badge: 2,
        effect: { kind: "command", command: "hz clock", runs: true },
      },
      {
        id: "clock:disabled",
        slots: { header: true },
        label: "Unavailable clock",
        icon: "clock",
        refresh: 300,
        effect: null,
        problem: "Clock service offline",
      },
      { id: "clock:hidden", slots: { header: false }, label: "Hidden clock", icon: "clock", refresh: 300 },
    ],
  );
  const terminal = vi.fn();
  const calendar = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/doctor"]}>
        <button type="button">Outside</button>
        <TopBar
          chatOpen={false}
          onToggleChat={() => {}}
          terminalOpen={false}
          onToggleTerminal={terminal}
          calendarOpen={false}
          onOpenCalendar={calendar}
          onOpenNavigation={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, terminal, calendar };
}

describe("compact TopBar menus", () => {
  it("uses one labeled shortcut presentation for built-ins and dynamic plugin actions", async () => {
    const user = userEvent.setup();
    const { client, terminal, calendar } = compactBar();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const trigger = screen.getByRole("button", { name: "More workspace actions" });
    await user.click(trigger);
    const tools = screen.getByRole("region", { name: "More workspace actions" });
    const tool = within(tools);
    const shortcuts = ["Open a terminal", "Open calendar", "Refresh", "Team clock (2)", "Unavailable clock"].map(
      (name) => tool.getByRole("button", { name }),
    );
    expect(new Set(shortcuts.map((button) => button.className)).size).toBe(1);
    expect(shortcuts[0]).toHaveFocus();
    expect(shortcuts[3]).toHaveTextContent("Team clock");
    expect(shortcuts[4]).toBeDisabled();
    expect(shortcuts[4]).toHaveAttribute("title", "Clock service offline");
    expect(tool.queryByRole("button", { name: "Hidden clock" })).not.toBeInTheDocument();
    expect(tool.queryByText(/Copy/)).not.toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(terminal).toHaveBeenCalledOnce();
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    await user.click(
      within(screen.getByRole("region", { name: "More workspace actions" })).getByRole("button", {
        name: "Open calendar",
      }),
    );
    expect(calendar).toHaveBeenCalledOnce();
    await user.click(trigger);
    await user.click(
      within(screen.getByRole("region", { name: "More workspace actions" })).getByRole("button", { name: "Refresh" }),
    );
    await waitFor(() => expect(invalidate).toHaveBeenCalledOnce());

    await user.click(trigger);
    await user.click(
      within(screen.getByRole("region", { name: "More workspace actions" })).getByRole("button", {
        name: "Team clock (2)",
      }),
    );
    expect(tools).not.toBeInTheDocument();
    // The command stays behind its existing confirmation even after the shortcuts pane closes.
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("hz clock");
  });

  it("keeps the two copies under the screen title and preserves keyboard and outside dismissal", async () => {
    const user = userEvent.setup();
    compactBar();
    const copyText = vi.spyOn(navigator.clipboard, "writeText");
    const title = screen.getByRole("button", { name: "Doctor" });
    await user.click(title);
    const menu = screen.getByRole("menu", { name: "Doctor" });
    const command = within(menu).getByRole("menuitem", { name: /Copy the hz command/ });
    const link = within(menu).getByRole("menuitem", { name: "Copy link to this screen" });
    expect(within(menu).getAllByRole("menuitem")).toEqual([command, link]);
    expect(command).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(link).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(title).toHaveFocus();
    expect(menu).not.toBeInTheDocument();

    await user.click(title);
    await user.keyboard("{Enter}");
    expect(copyText).toHaveBeenLastCalledWith("hz system doctor");
    await user.click(title);
    await user.keyboard("{End}{Enter}");
    expect(copyText).toHaveBeenLastCalledWith(window.location.origin + window.location.pathname);
    await user.click(title);
    await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(title).toHaveAttribute("aria-expanded", "false");
  });
});
