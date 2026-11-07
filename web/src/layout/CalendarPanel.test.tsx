import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import * as browser from "@/lib/browser";
import { shell } from "@/test/fixtures";
import { eventStream, expectAccessible, mockApi, renderAt } from "@/test/render";
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

const baseEvent = { id: "one", start: "2026-11-07T08:00:00Z", end: "2026-11-07T08:45:00Z", all_day: false };
const weekOf = (events: object[]) => ({
  timezone: "Europe/Berlin",
  since: "2026-11-02T00:00:00Z",
  until: "2026-11-09T00:00:00Z",
  events,
  problem: null,
  fix: null,
});

it("Refresh aborts the older calendar read, so a stale response never overwrites the edited meeting", async () => {
  let finishOld = () => {};
  const first = new Promise((resolve) => {
    finishOld = () => resolve(weekOf([{ ...baseEvent, title: "Old title" }]));
  });
  const calendar = vi
    .fn()
    .mockReturnValueOnce(first)
    .mockReturnValue(weekOf([{ ...baseEvent, title: "Edited title" }]));
  const { calls } = mockApi({ ...shell, "/api/org/calendar": calendar });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Open calendar" }));
  await waitFor(() => expect(calendar).toHaveBeenCalledTimes(1));
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("Edited title")).toBeInTheDocument();
  finishOld();
  const reads = calls.filter((request) => new URL(request.url).pathname === "/api/org/calendar");
  expect(reads).toHaveLength(2);
  expect(reads[0]?.signal.aborted).toBe(true);
  expect(reads[1]?.cache).toBe("no-store");
  expect(screen.queryByText("Old title")).not.toBeInTheDocument();
});

it("opens a meeting row into its detail with provider, a Join icon button, organizer and attendees", async () => {
  vi.spyOn(browser, "isLocalServer").mockReturnValue(true);
  mockApi({
    "/api/org/calendar": weekOf([
      {
        ...baseEvent,
        title: "Weekly sync",
        conference_url: "https://acme.zoom.us/j/123",
        organizer: { name: "Ada Lovelace", email: "ada@example.com" },
        attendees: [{ name: "Ada Lovelace" }, { email: "bob@example.com" }, { name: "Ada Lovelace" }],
        response: "tentative",
      },
      { ...baseEvent, id: "two", title: "Plain" },
    ]),
    "/api/org/calendar-reminders-status": { enabled: false, service: "not installed" },
  });
  const user = userEvent.setup();
  const { container } = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <CalendarPanel onClose={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("button", { name: /Plain/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Join meeting" })).toHaveAttribute("href", "https://acme.zoom.us/j/123");
  await user.click(screen.getByRole("button", { name: /Weekly sync/ }));
  const detail = screen.getByRole("article", { name: "Weekly sync" });
  expect(within(detail).getByRole("link", { name: "Join meeting" })).toHaveAttribute(
    "href",
    "https://acme.zoom.us/j/123",
  );
  expect(within(detail).getByText("Zoom")).toBeInTheDocument();
  expect(within(detail).getByText("Organizer").nextSibling).toHaveTextContent("Ada Lovelace");
  expect(
    within(detail)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual(["Ada Lovelace", "bob@example.com"]);
  expect(within(detail).getByText("Tentative")).toBeInTheDocument();
  await expectAccessible(container);
  await user.click(screen.getByRole("button", { name: "Back to the week" }));
  expect(screen.queryByRole("article")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Plain/ })).toBeInTheDocument();
});
