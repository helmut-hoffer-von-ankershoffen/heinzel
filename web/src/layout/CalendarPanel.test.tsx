import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import * as browser from "@/lib/browser";
import { shell } from "@/test/fixtures";
import { eventStream, expectAccessible, mockApi, renderAt } from "@/test/render";
import { CalendarPanel } from "./CalendarPanel";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

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
  expect(screen.getByRole("link", { name: "Join Zoom meeting" })).toHaveAttribute("href", "https://acme.zoom.us/j/123");
  await user.click(screen.getByRole("button", { name: /Weekly sync/ }));
  const detail = screen.getByRole("article", { name: "Weekly sync" });
  expect(within(detail).getByRole("link", { name: "Join Zoom meeting" })).toHaveAttribute(
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

it("puts Close in the title row and every action in one bottom row, Today leading with its words", async () => {
  panel(true);
  const close = await screen.findByRole("button", { name: "Close calendar" });
  expect(close.closest("[data-pane-chrome]")).not.toBeNull();
  expect(close).toHaveTextContent("");
  const row = (await screen.findByRole("group", { name: "Actions" })) as HTMLElement;
  const buttons = [...row.querySelectorAll<HTMLElement>("button, a")];
  expect(buttons.map((button) => button.getAttribute("aria-label") ?? button.textContent)).toEqual([
    "Today",
    "Previous week",
    "Next week",
    "Set up reminder worker",
  ]);
  expect(buttons[0]).toHaveTextContent("Today");
  expect(buttons.slice(1).every((button) => button.textContent === "")).toBe(true);
});

// The agenda, with the clock fixed to Sun 2026-11-08 08:18 in Europe/Berlin (07:18Z) and the API zone Europe/Berlin.
const NOW = "2026-11-08T07:18:00Z";
const meeting = (id: string, title: string, start: string, end: string, extra: object = {}) => ({
  id,
  title,
  start,
  end,
  all_day: false,
  ...extra,
});
const agendaEvents = [
  meeting("offsite", "Offsite", "2026-11-06T17:00:00Z", "2026-11-07T09:00:00Z"),
  meeting("sat", "Test2", "2026-11-07T08:00:00Z", "2026-11-07T08:45:00Z"),
  meeting("market", "Woltersdorf market", "2026-11-07T23:00:00Z", "2026-11-08T23:00:00Z", { all_day: true }),
  meeting("standup", "Standup", "2026-11-08T07:00:00Z", "2026-11-08T07:30:00Z"),
  meeting("review", "Review with Anna", "2026-11-08T09:00:00Z", "2026-11-08T09:45:00Z", {
    conference_url: "https://acme.zoom.us/j/9",
  }),
  meeting("dentist", "Dentist", "2026-11-08T13:00:00Z", "2026-11-08T14:00:00Z"),
];
const nextWeek = {
  timezone: "Europe/Berlin",
  since: "2026-11-08T23:00:00Z",
  until: "2026-11-15T23:00:00Z",
  events: [meeting("plan", "Planning", "2026-11-09T09:00:00Z", "2026-11-09T10:00:00Z")],
  problem: null,
  fix: null,
};
const thisWeek = (events: object[], timezone = "Europe/Berlin") => ({
  timezone,
  since: "2026-11-01T23:00:00Z",
  until: "2026-11-08T23:00:00Z",
  events,
  problem: null,
  fix: null,
});

function agenda(events: object[] = agendaEvents, { local = true, service = "not installed", enabled = true } = {}) {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
  vi.setSystemTime(new Date(NOW));
  vi.spyOn(browser, "isLocalServer").mockReturnValue(local);
  const scroll = vi.fn();
  Object.defineProperty(Element.prototype, "scrollIntoView", { value: scroll, configurable: true, writable: true });
  mockApi({
    "/api/org/calendar": (request: Request) =>
      new URL(request.url).searchParams.get("offset") === "1" ? nextWeek : thisWeek(events),
    "/api/org/calendar-reminders-status": { enabled, service },
  });
  const view = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <CalendarPanel onClose={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { scroll, ...view };
}
afterEach(() => Reflect.deleteProperty(Element.prototype, "scrollIntoView"));

const headers = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
const listed = (section: HTMLElement) =>
  within(section)
    .queryAllByRole("button")
    .map((b) => b.getAttribute("aria-label"));

it("names each day in a sticky header, Today first among the live ones, and drops empty days", async () => {
  agenda();
  await screen.findByText("Standup");
  expect(headers()).toEqual(["Fri 6 Nov", "Yesterday \u00b7 Sat 7 Nov", "Today \u00b7 Sun 8 Nov"]);
  for (const heading of screen.getAllByRole("heading", { level: 3 })) expect(heading).toHaveClass("sticky", "top-0");
  expect(screen.getByRole("list", { name: "Agenda" })).toBeInTheDocument();
});

it("keeps Today with a quiet sentence when it has no events, and captions another week instead", async () => {
  agenda([agendaEvents[1] ?? {}]);
  expect(await screen.findByText("No events today")).toBeInTheDocument();
  expect(headers()).toEqual(["Yesterday \u00b7 Sat 7 Nov", "Today \u00b7 Sun 8 Nov"]);
  expect(screen.queryByText(/2\u20138 Nov/)).not.toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Next week" }));
  expect(await screen.findByText("9\u201315 Nov 2026")).toBeInTheDocument();
  expect(headers()).toEqual(["Tomorrow \u00b7 Mon 9 Nov"]);
  expect(screen.queryByText("No events today")).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { current: "date" })).not.toBeInTheDocument();
});

it("shows times only in rows: the date is said once, in the header", async () => {
  const { container } = agenda();
  await screen.findByText("Standup");
  const rows = [...container.querySelectorAll("ul > li")];
  expect(rows).toHaveLength(7);
  for (const row of rows) {
    const text = (row.querySelector("button > span") as HTMLElement).textContent ?? "";
    expect(text).toMatch(/^(\d{2}:\d{2}|All day|\u2192 \d{2}:\d{2})/);
    expect(row.textContent).not.toMatch(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b|20\d\d/);
  }
  const month = /Nov/g;
  const inHeaders = headers().join(" ").match(month)?.length;
  expect(inHeaders).toBe(3);
  expect(container.textContent?.match(month)?.length).toBe(3);
});

it("shows a multi-day meeting under each day with arrows, and All day for whole days", async () => {
  agenda();
  await screen.findByText("Standup");
  const section = (name: RegExp) => screen.getByRole("heading", { name }).closest("section") as HTMLElement;
  expect(within(section(/Fri 6 Nov/)).getByRole("button", { name: /18:00.*Offsite/ })).toHaveTextContent(
    "18:00 \u2192",
  );
  const saturday = section(/Sat 7 Nov/);
  expect(within(saturday).getByRole("button", { name: /10:00.*Offsite/ })).toHaveTextContent("\u2192 10:00");
  expect(within(saturday).getByRole("button", { name: /09:00\s*09:45\s*Test2/ })).toHaveTextContent("09:0009:45");
  expect(within(section(/Today/)).getByRole("button", { name: /All day\s*Woltersdorf market/ })).toHaveTextContent(
    "All day",
  );
});

it("marks the running meeting, the now line and the next meeting, and moves them with the clock", async () => {
  const { container } = agenda();
  const today = (await screen.findByRole("heading", { name: /Today/ })).closest("section") as HTMLElement;
  expect(today).toHaveAttribute("aria-current", "date");
  const standup = within(today).getByRole("button", { name: /08:00\s*08:30\s*Standup\s*Now\s*12 min left/ });
  expect(standup).toHaveTextContent("Now");
  expect(standup).toHaveTextContent("12 min left");
  expect(standup.closest("li")).toHaveClass("border-primary");
  expect(standup.querySelector("[data-progress]")).toHaveStyle({ width: "60%" });
  const separator = within(today).getByRole("separator", { name: "Now, 08:18" });
  expect(standup.compareDocumentPosition(separator) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const review = within(today).getByRole("button", { name: /Review with Anna/ });
  expect(review.compareDocumentPosition(separator) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  expect(review).toHaveTextContent("in 1 h 42 min");
  const dentist = within(today).getByRole("button", { name: /Dentist/ });
  expect(dentist).not.toHaveTextContent(/in \d|Now|left/);
  expect(container.querySelectorAll("[data-progress]")).toHaveLength(1);

  act(() => vi.advanceTimersByTime(15 * 60_000));
  expect(within(today).getByRole("separator", { name: "Now, 08:33" })).toBeInTheDocument();
  expect(container.querySelector("[data-progress]")).toBeNull();
  expect(within(today).getByRole("button", { name: /Standup/ })).not.toHaveTextContent("Now");
  expect(
    within(today)
      .getByRole("button", { name: /Standup/ })
      .closest("li"),
  ).toHaveClass("text-muted-foreground");

  act(() => vi.advanceTimersByTime(105 * 60_000));
  const moved = within(today).getByRole("separator", { name: "Now, 10:18" });
  const reviewAgain = within(today).getByRole("button", { name: /Review with Anna/ });
  expect(reviewAgain.compareDocumentPosition(moved) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(today).getByRole("button", { name: /Dentist/ })).toHaveTextContent("in 3 h 42 min");
});

it("mutes past days and finished rows, and the header says the day so colour is never the only signal", async () => {
  agenda();
  await screen.findByText("Standup");
  const yesterday = screen.getByRole("heading", { name: /Yesterday/ }).closest("section") as HTMLElement;
  expect(yesterday).toHaveClass("text-muted-foreground");
  expect(yesterday).toHaveTextContent("Yesterday");
  const today = screen.getByRole("heading", { name: /Today/ }).closest("section") as HTMLElement;
  expect(today).not.toHaveClass("text-muted-foreground");
  expect(listed(today)).toHaveLength(4);
  act(() => vi.advanceTimersByTime(60 * 60_000));
  expect(
    within(today)
      .getByRole("button", { name: /Standup/ })
      .closest("li"),
  ).toHaveClass("text-muted-foreground");
  expect(
    within(today)
      .getByRole("button", { name: /Review/ })
      .closest("li"),
  ).not.toHaveClass("text-muted-foreground");
});

it("keeps Today dimmed on this week, enables it after Next week, and scrolls Today to the top when pressed", async () => {
  const { scroll } = agenda();
  await screen.findByText("Standup");
  expect(scroll).toHaveBeenCalledTimes(1);
  expect(scroll.mock.contexts[0]).toHaveAttribute("aria-current", "date");
  expect(scroll).toHaveBeenLastCalledWith(expect.objectContaining({ block: "start" }));
  const row = screen.getByRole("group", { name: "Actions" });
  const today = within(row).getByRole("button", { name: /Today/ });
  expect(today).toBeDisabled();
  expect(today).toHaveTextContent("Today");
  for (const name of ["Previous week", "Next week"]) {
    const button = within(row).getByRole("button", { name });
    expect(button).toHaveAttribute("title", name);
    expect(button).toHaveTextContent("");
  }
  const user = userEvent.setup();
  await user.click(within(row).getByRole("button", { name: "Next week" }));
  await screen.findByText("Planning");
  expect(today).toBeEnabled();
  await user.click(today);
  await screen.findByText("Standup");
  expect(today).toBeDisabled();
  expect(scroll).toHaveBeenCalledTimes(2);
  expect(scroll.mock.contexts[1]).toHaveAttribute("aria-current", "date");
});

it("says the reminder state in one line with an icon and nothing to press", async () => {
  const { container } = agenda();
  const band = await screen.findByText("Reminders need a background worker on this computer.");
  expect(band.querySelector("svg")).not.toBeNull();
  expect(band.querySelector("a, button")).toBeNull();
  expect(band.closest("a, button")).toBeNull();
  expect(container.querySelectorAll("a")).toHaveLength(1); // only the Join link of the review meeting
  expect(screen.getByRole("button", { name: "Set up reminder worker" })).toBeInTheDocument();
});

it("hides the band while the worker runs, and shows the remote sentence without a worker button", async () => {
  agenda(agendaEvents, { service: "running" });
  await screen.findByRole("button", { name: "Remove reminder worker" });
  expect(screen.queryByText(/Reminders need|Calendar reminders|worker's state/)).not.toBeInTheDocument();
});

it("tells a remote browser to use the computer itself", async () => {
  agenda(agendaEvents, { local: false });
  expect(await screen.findByText("Open Calendar on this computer to set up its reminder worker.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /reminder worker/ })).not.toBeInTheDocument();
});

it("says reminders are off, with the settings button in the bottom row", async () => {
  agenda(agendaEvents, { enabled: false });
  expect(await screen.findByText("Calendar reminders are off or unavailable.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Notification settings" })).toBeInTheDocument();
});

it("announces Now and Next in one polite status line and nowhere else", async () => {
  const { container } = agenda();
  await screen.findByText("Standup");
  const status = screen.getByRole("status");
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(status).toHaveClass("sr-only");
  expect(status).toHaveTextContent("Now: Standup, 12 min left. Next: Review with Anna in 1 h 42 min.");
  expect(container.querySelectorAll("[aria-live]")).toHaveLength(0);
  act(() => vi.advanceTimersByTime(15 * 60_000));
  expect(status).toHaveTextContent("Now: nothing. Next: Review with Anna in 1 h 27 min.");
});

it("takes every string from the catalogue", () => {
  const sources = import.meta.glob<string>("./CalendarPanel.tsx", { query: "?raw", import: "default", eager: true });
  const source = Object.values(sources)[0] ?? "";
  expect(source.length).toBeGreaterThan(1000);
  const code = source
    .replace(/\bt\((?:"[^"]*"|`[^`]*`)[^)]*\)/g, "t()")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  expect(code.match(/"[A-Z][a-z]+[^"]*"/g) ?? []).toEqual([]);
  expect(code.match(/(?<![=-])>\s*[A-Za-z][^<>{}"]*</g) ?? []).toEqual([]);
});

it("opens the join and calendar links outside the app and draws no button for a link the event lacks", async () => {
  vi.spyOn(browser, "isLocalServer").mockReturnValue(true);
  mockApi({
    "/api/org/calendar": weekOf([
      {
        ...baseEvent,
        title: "Test2",
        conference_url: "https://zoom.us/j/12345678901",
        url: "https://www.google.com/calendar/event?eid=abc",
      },
      { ...baseEvent, id: "two", title: "Plain" },
    ]),
    "/api/org/calendar-reminders-status": { enabled: true, service: "running" },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <CalendarPanel onClose={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const row = (await screen.findByRole("button", { name: /Test2/ })).closest("li") as HTMLElement;
  const join = within(row).getByRole("link", { name: "Join Zoom meeting" });
  expect(join).toHaveAttribute("href", "https://zoom.us/j/12345678901");
  expect(join).toHaveAttribute("target", "_blank");
  expect(join).toHaveAttribute("rel", "noopener noreferrer");
  const plain = (await screen.findByRole("button", { name: /Plain/ })).closest("li") as HTMLElement;
  expect(within(plain).queryByRole("link")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Test2/ }));
  const detail = screen.getByRole("article", { name: "Test2" });
  const source = within(detail).getByRole("link", { name: "Open in calendar" });
  expect(source).toHaveAttribute("href", "https://www.google.com/calendar/event?eid=abc");
  expect(source).toHaveAttribute("target", "_blank");
  await user.click(screen.getByRole("button", { name: "Back to the week" }));
  await user.click(screen.getByRole("button", { name: /Plain/ }));
  const bare = screen.getByRole("article", { name: "Plain" });
  expect(within(bare).queryByRole("link")).not.toBeInTheDocument();
  expect(within(bare).queryByRole("button", { name: /Join|Open in calendar/ })).not.toBeInTheDocument();
});
