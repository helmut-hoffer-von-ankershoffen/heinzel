// Times on demand and day headings: the time is in the page for screen readers and shows above the message on hover,
// keyboard focus or a tap; a conversation is one tab stop; day headings split it at the person's day boundaries. Time
// is frozen and the zone given, so nothing here depends on the runner's clock, zone or ICU data.
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessageTime } from "@/chat/MessageTime";
import { base } from "@/test/chat";
import { config } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const zone = "Europe/Berlin";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-14T15:00:00Z"));
});
afterEach(() => {
  vi.useRealTimers();
  sessionStorage.clear();
  localStorage.clear();
});

const shownTime = (row: Element) => row.querySelector("[data-message-time]");

describe("MessageTime", () => {
  const at = "2026-11-13T08:05:00Z"; // 09:05 in Berlin

  function one() {
    render(
      <MessageTime at={at} own={false} zone={zone}>
        <p>
          Hello <a href="#x">the invoice list</a>
        </p>
      </MessageTime>,
    );
    return document.querySelector("[data-message-time-row]") as HTMLElement;
  }

  it("keeps the time for screen readers and describes the message by it", () => {
    const row = one();
    const time = row.querySelector("time.sr-only") as HTMLElement;
    expect(time).toHaveAttribute("datetime", at);
    expect(time).toHaveTextContent("Friday 13 November 2026, 09:05");
    expect(row).toHaveAccessibleDescription("Friday 13 November 2026, 09:05");
    expect(shownTime(row)).toBeNull();
  });

  it("shows the time on keyboard focus and hides it on Escape", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const row = one();
    await user.tab();
    expect(row).toHaveFocus();
    expect(shownTime(row)).toHaveTextContent("09:05");
    await user.keyboard("{Escape}");
    expect(shownTime(row)).toBeNull();
  });

  it("shows it on mouse hover only", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const row = one();
    await user.hover(screen.getByText(/Hello/));
    expect(shownTime(row)).not.toBeNull();
    await user.unhover(screen.getByText(/Hello/));
    expect(shownTime(row)).toBeNull();
  });

  it("shows it on a tap and hides it on the second tap, but not for a tap on a link", () => {
    const row = one();
    fireEvent.click(row);
    expect(shownTime(row)).toHaveTextContent("09:05");
    fireEvent.click(row);
    expect(shownTime(row)).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "the invoice list" }));
    expect(shownTime(row)).toBeNull();
  });

  it("does not let the focus of a press keep the time shown after the second tap", () => {
    const row = one();
    fireEvent.pointerDown(row);
    row.focus(); // a press focuses the row as well
    fireEvent.click(row);
    expect(shownTime(row)).not.toBeNull();
    fireEvent.pointerDown(row);
    fireEvent.click(row);
    expect(shownTime(row)).toBeNull();
  });

  it("is one tab stop in a list of thirty, and the arrow keys walk the rest", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <div>
        {Array.from({ length: 30 }, (_, index) => `message-${index}`).map((name, index) => (
          <div key={name} data-message-speaker="you">
            <MessageTime at={at} own tabbable={index === 29} zone={zone}>
              <p>{`Message ${index}`}</p>
            </MessageTime>
          </div>
        ))}
      </div>,
    );
    const rows = [...document.querySelectorAll("[data-message-time-row]")] as HTMLElement[];
    expect(rows.filter((row) => row.tabIndex === 0)).toHaveLength(1);
    await user.tab();
    expect(rows[29]).toHaveFocus();
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(rows[27]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(rows[28]).toHaveFocus();
  });
});

const flagless = config;

function mount(messages: Record<string, unknown>[]) {
  mockApi({
    ...base,
    "/api/system/config/show": flagless,
    "/api/heinzel/sessions": {
      sessions: [{ session_id: helmi, name: "Helmi", state: "awake", last_seq: 3, updated_at: "2026-11-14T10:00:00Z" }],
    },
    "/api/heinzel/session": () => ({ session_id: helmi, heinzel_id: helmi, name: "Helmi", messages }),
  });
  renderAt("/doctor");
}

const message = (seq: number, role: string, text: string, at?: string) => ({
  id: `m${seq}`,
  seq,
  role,
  text,
  status: "completed",
  ...(at ? { at } : {}),
});

describe("in the conversation", () => {
  it("draws one heading per day, a time for every message and one tab stop", async () => {
    mount([
      message(1, "user", "Earlier", "2026-11-12T09:00:00Z"),
      message(2, "assistant", "Reply", "2026-11-12T09:01:00Z"),
      message(3, "user", "Later", "2026-11-14T09:00:00Z"),
    ]);
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    await chat.findByText("Later");
    expect([...document.querySelectorAll("[data-day-heading]")].map((h) => h.textContent)).toEqual([
      "Thursday 12 November",
      "Today",
    ]);
    expect(document.querySelectorAll("time.sr-only")).toHaveLength(3);
    expect(document.querySelectorAll("[data-message-time-row][tabindex='0']")).toHaveLength(1);
  });

  it("does not repeat a heading after a message that has no time", async () => {
    mount([
      message(1, "user", "First", "2026-11-14T09:00:00Z"),
      message(2, "assistant", "Undated"),
      message(3, "user", "Third", "2026-11-14T10:00:00Z"),
    ]);
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    await chat.findByText("Third");
    expect(document.querySelectorAll("[data-day-heading]")).toHaveLength(1);
  });
});
