import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SchemaNotice } from "@/api/client";
import { doctor, shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const checkIn: SchemaNotice = {
  id: "20261027T0900-ab12",
  kind: "heinzel",
  title: "My helper checked in",
  body: "Your scheduled five-minute check-in is due.",
  route: "/heinzel",
  command: "hz heinzel sessions",
  key: "heinzel:hz-01:check-in:2026-10-27T09:00:00+00:00",
  urgent: false,
  at: "2026-10-27T09:00:00Z",
  due: "2026-10-27T09:00:00Z",
  popup: true,
};

describe("The notification center", () => {
  it("labels and filters calendar reminders", async () => {
    mockApi({
      ...shell,
      "/api/system/doctor": doctor,
      "/api/system/daemon/jobs": [],
      "/api/system/notify/list": {
        notices: [{ ...checkIn, kind: "calendar", title: "Meeting starts soon" }],
        waiting: 0,
        native: "through Heinzel Notifier",
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor?panel=notifications");
    await user.click(await screen.findByRole("tab", { name: "Calendar" }));
    expect(await screen.findByText("Meeting starts soon")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Drafts" }));
    expect(screen.queryByText("Meeting starts soon")).not.toBeInTheDocument();
  });

  it("labels and filters scheduled Heinzel check-ins", async () => {
    mockApi({
      ...shell,
      "/api/system/doctor": doctor,
      "/api/system/daemon/jobs": [],
      "/api/system/notify/list": { notices: [checkIn], waiting: 0, native: "through Heinzel Notifier" },
    });
    const user = userEvent.setup();
    renderAt("/doctor?panel=notifications");

    const tab = await screen.findByRole("tab", { name: "Heinzel check-ins" });
    expect(await screen.findByText("My helper checked in")).toBeInTheDocument();
    await user.click(tab);
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("My helper checked in")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Drafts" }));
    expect(screen.queryByText("My helper checked in")).not.toBeInTheDocument();
  });

  it("shows the personal Heinzel behind a notice with its full, current look", async () => {
    const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const helper = { name: "Mira", hat: "green", reason: "scheduled check-in" } as const;
    mockApi({
      ...shell,
      "/api/system/doctor": doctor,
      "/api/system/daemon/jobs": [],
      "/api/system/notify/list": {
        notices: [
          { ...checkIn, route: `/heinzel?chat=${session}`, helper },
          // Its Heinzel is not in this person's list: the hat recorded with the notice is what remains.
          { ...checkIn, id: "20261027T0901-cd34", title: "Another check-in", route: "/heinzel?chat=hz-gone", helper },
          { ...checkIn, id: "20261027T0902-ef56", kind: "doctor", title: "Something is down", route: "/doctor" },
        ],
        waiting: 0,
        native: "through Heinzel Notifier",
      },
      "/api/heinzel/sessions": {
        sessions: [
          {
            session_id: session,
            name: "Mira",
            state: "awake",
            last_seq: 3,
            updated_at: "2026-10-27T09:00:00Z",
            // The look was changed after the notice was recorded: blue hat, rose shirt, a scarf.
            appearance: { age: "old", build: "round", hat: "blue", shirt: "rose", accessories: ["scarf"] },
          },
        ],
      },
    });
    renderAt("/doctor?panel=notifications");
    const row = (await screen.findByText("My helper checked in")).closest("button") as HTMLElement;
    expect(row).toHaveTextContent("Mira · scheduled check-in");
    await waitFor(() => expect(row.querySelector('svg[data-view="bust"] [fill="#1E88E5"]')).not.toBeNull());
    expect(row.querySelector('svg[data-view="bust"] [fill="#D9567C"]')).not.toBeNull();
    expect(row.querySelector('svg[data-view="bust"] [fill="#43A047"]')).toBeNull();
    const other = screen.getByText("Another check-in").closest("button") as HTMLElement;
    expect(other.querySelector('svg[data-view="bust"] [fill="#43A047"]')).not.toBeNull();
    // A notice without a Heinzel keeps its kind's icon.
    const plain = screen.getByText("Something is down").closest("button") as HTMLElement;
    expect(plain.querySelector('svg[data-view="bust"]')).toBeNull();
  });

  it("keeps the delivery line at full muted contrast (axe: no opacity modifier)", async () => {
    mockApi({
      ...shell,
      "/api/system/doctor": doctor,
      "/api/system/daemon/jobs": [],
      "/api/system/notify/list": { notices: [checkIn], waiting: 0, native: "through Heinzel Notifier" },
    });
    renderAt("/doctor?panel=notifications");
    const row = (await screen.findByText("My helper checked in")).closest("button");
    for (const node of row?.querySelectorAll("[class*='text-muted-foreground']") ?? []) {
      expect(node.className).not.toMatch(/text-muted-foreground\/\d+/);
    }
  });
});

describe("The notification center's actions", () => {
  it("closes from the title row and keeps Mark all read in the bottom row after the test", async () => {
    mockApi({
      ...shell,
      "/api/system/doctor": doctor,
      "/api/system/daemon/jobs": [],
      "/api/system/notify/list": { notices: [checkIn], waiting: 0, native: "" },
    });
    renderAt("/doctor?panel=notifications");
    const close = await screen.findByRole("button", { name: "Close notifications" });
    expect(close.closest("[data-pane-chrome]")).not.toBeNull();
    const mark = screen.getByRole("button", { name: "Mark all read" });
    const row = mark.closest("[data-pane-actions]") as HTMLElement;
    expect(row).not.toBeNull();
    const test = screen.getByRole("button", { name: "Send a test notification" });
    expect(test).toHaveTextContent("Send a test notification");
    expect(mark).toHaveTextContent("");
    expect(test.compareDocumentPosition(mark) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(row.contains(test)).toBe(true);
  });
});
