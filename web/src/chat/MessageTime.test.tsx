// Times on demand and day headings (flag `one_chat`): the time is in the page for screen readers, shows above the
// message on hover, focus or tap, and day headings split the conversation at the person's day boundaries.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dayHeading, MessageTime, startsDay } from "@/chat/MessageTime";
import { base } from "@/test/chat";
import { config } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const local = (day: number, hour = 9) => new Date(2026, 10, day, hour, 5).toISOString();

describe("day headings", () => {
  const now = new Date(2026, 10, 14, 15, 0);
  it("names today, yesterday, then the weekday and date", () => {
    expect(dayHeading(local(14, 0), now)).toBe("Today");
    expect(dayHeading(local(13, 23), now)).toBe("Yesterday");
    expect(dayHeading(local(12), now)).toBe("Thursday 12 November");
    expect(dayHeading(new Date(2025, 11, 31, 10).toISOString(), now)).toBe("Wednesday 31 December 2025");
  });
  it("starts a day at the first message and at midnight, not within a day", () => {
    expect(startsDay(local(14))).toBe(true);
    expect(startsDay(local(14, 10), local(14, 8))).toBe(false);
    expect(startsDay(local(14, 0), local(13, 23))).toBe(true);
  });
});

describe("MessageTime", () => {
  it("keeps the time for screen readers and shows it on focus, tap and hover only", async () => {
    const user = userEvent.setup();
    render(
      <MessageTime at={local(13)} own={false}>
        <p>Hello</p>
      </MessageTime>,
    );
    const row = document.querySelector("[data-message-time-row]") as HTMLElement;
    expect(row.querySelector("time.sr-only")).toHaveAttribute("datetime", local(13));
    expect(row.querySelector("time.sr-only")).toHaveTextContent(/Friday, November 13, 2026.*09:05/);
    expect(row.querySelector("[data-message-time]")).toBeNull();
    await user.tab();
    expect(row).toHaveFocus();
    expect(row.querySelector("[data-message-time]")).toHaveTextContent("09:05");
    await user.keyboard("{Escape}");
    expect(row.querySelector("[data-message-time]")).toBeNull();
    await user.tab();
    await user.hover(screen.getByText("Hello"));
    expect(row.querySelector("[data-message-time]")).not.toBeNull();
    await user.unhover(screen.getByText("Hello"));
    await user.click(screen.getByText("Hello"));
    expect(row.querySelector("[data-message-time]")).not.toBeNull();
    await user.click(screen.getByText("Hello"));
    await user.unhover(screen.getByText("Hello"));
    await user.tab();
    expect(row.querySelector("[data-message-time]")).toBeNull();
  });
});

const flagged = {
  ...config,
  settings: [
    ...config.settings,
    { section: "hz", key: "features", value: ["one_chat"], description: "", env: "HZ_FEATURES" },
  ],
};

function mount(on: boolean) {
  const messages = [
    { id: "m1", seq: 1, role: "user", text: "Earlier", status: "completed", at: local(10) },
    { id: "m2", seq: 2, role: "assistant", text: "Reply", status: "completed", at: local(10, 9) },
    { id: "m3", seq: 3, role: "user", text: "Later", status: "completed", at: new Date().toISOString() },
  ];
  mockApi({
    ...base,
    "/api/system/config/show": on ? flagged : config,
    "/api/heinzel/sessions": {
      sessions: [{ session_id: helmi, name: "Helmi", state: "awake", last_seq: 3, updated_at: messages[2]?.at }],
    },
    "/api/heinzel/session": () => ({ session_id: helmi, heinzel_id: helmi, name: "Helmi", messages }),
  });
  renderAt(on ? "/doctor" : `/doctor?chat=${helmi}`);
}

describe("in the conversation", () => {
  beforeEach(() => vi.useRealTimers());
  afterEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("flag on: a heading per day and a time on every message", async () => {
    mount(true);
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    await chat.findByText("Later");
    const headings = [...document.querySelectorAll("[data-day-heading]")].map((h) => h.textContent);
    expect(headings).toEqual(["Tuesday 10 November", "Today"]);
    expect(document.querySelectorAll("time.sr-only")).toHaveLength(3);
  });

  it("flag off: no headings, no time rows", async () => {
    mount(false);
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    await chat.findByText("Later", {}, { timeout: 5000 });
    expect(document.querySelector("[data-day-heading]")).toBeNull();
    expect(document.querySelector("[data-message-time-row]")).toBeNull();
  });
});
