import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
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
