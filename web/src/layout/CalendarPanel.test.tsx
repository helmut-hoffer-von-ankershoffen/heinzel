import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import * as browser from "@/lib/browser";
import { eventStream, expectAccessible, mockApi } from "@/test/render";
import { CalendarPanel } from "./CalendarPanel";

afterEach(() => vi.restoreAllMocks());

function panel(local: boolean) {
  vi.spyOn(browser, "isLocalServer").mockReturnValue(local);
  const requests = mockApi({
    "/api/org/calendar": {
      timezone: "Europe/Berlin",
      since: "2026-11-02T00:00:00Z",
      until: "2026-11-09T00:00:00Z",
      events: [],
      problem: null,
      fix: null,
    },
    "/api/org/calendar-reminders-status": { enabled: true, service: "not installed" },
    "/api/stream/org/calendar-reminders-install": () => eventStream([], { message: "installed" }),
  });
  const rendered = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <CalendarPanel onClose={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...requests, ...rendered };
}

it("sets up the profile worker only after explicit confirmation without a terminal command", async () => {
  const { calls, container } = panel(true);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Set up reminder worker" }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent("at every login");
  expect(screen.getByRole("alertdialog")).toHaveTextContent("separate consent");
  expect(container.textContent).not.toContain("hz org");
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  await expectAccessible(container);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Set up reminder worker" }));
  await user.click(screen.getByRole("button", { name: "Run it" }));
  await waitFor(() => expect(calls.filter((call) => call.method === "POST")).toHaveLength(1));
  const request = calls.find((call) => call.method === "POST");
  expect(new URL(request?.url ?? "").pathname).toBe("/api/stream/org/calendar-reminders-install");
  expect(new URL(request?.url ?? "").searchParams.get("confirm")).toBe("true");
});

it("does not offer server persistence setup from a remote browser", async () => {
  const { calls } = panel(false);
  expect(await screen.findByText(/Open Calendar on this computer/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Set up reminder worker" })).not.toBeInTheDocument();
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
});
