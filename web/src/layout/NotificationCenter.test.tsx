import { screen } from "@testing-library/react";
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
