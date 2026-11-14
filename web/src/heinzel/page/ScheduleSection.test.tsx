// The schedule in words and the short times of the Heinzel's page.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { schedule } from "@/test/heinzelPage";
import { ScheduleSection } from "./ScheduleSection";
import { when } from "./when";

describe("schedule", () => {
  it("reads in words, with the next and the last run, and never the cron text or the hash", () => {
    const { container } = render(<ScheduleSection schedule={{ ...schedule, last_run: "2026-11-13T07:00:00Z" }} />);
    expect(screen.getByText("On · every weekday at 08:00")).toBeInTheDocument();
    expect(screen.getByText(/^Next run /)).toBeInTheDocument();
    expect(screen.getByText(/^Last run /)).toBeInTheDocument();
    expect(container).not.toHaveTextContent("0 8 * * 1-5");
    expect(container).not.toHaveTextContent("c".repeat(16));
  });

  it("has no next run while paused or proposed", () => {
    const { rerender } = render(<ScheduleSection schedule={{ ...schedule, status: "paused" }} />);
    expect(screen.getByText("Paused · every weekday at 08:00")).toBeInTheDocument();
    expect(screen.queryByText(/^Next run /)).not.toBeInTheDocument();
    rerender(<ScheduleSection schedule={{ ...schedule, status: "proposed" }} />);
    expect(screen.getByText("Proposed, not on yet · every weekday at 08:00")).toBeInTheDocument();
  });

  it("says it wakes only when you write without a schedule", () => {
    render(<ScheduleSection schedule={null} />);
    expect(screen.getByText("No schedule. It wakes only when you write.")).toBeInTheDocument();
  });
});

describe("when", () => {
  const now = new Date("2026-11-14T12:00:00Z");

  it("names the weekday within a week and the date further away", () => {
    expect(when("2026-11-16T07:00:00Z", now)).toMatch(/\d{2}:\d{2}/);
    expect(when("2026-11-16T07:00:00Z", now)).not.toMatch(/Nov/);
    expect(when("2026-12-24T07:00:00Z", now)).toMatch(/Dec/);
  });

  it("has nothing to say without a time, or for one it cannot read", () => {
    expect(when(null, now)).toBeNull();
    expect(when(undefined, now)).toBeNull();
    expect(when("not a time", now)).toBeNull();
  });
});
