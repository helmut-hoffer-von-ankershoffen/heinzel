import { describe, expect, it } from "vitest";
import { workingHours } from "./workingHours";

const berlin = { zone: "Europe/Berlin", hours: [9, 18] as [number, number] };
const at = (iso: string) => workingHours(berlin, new Date(iso));

describe("shared working-hour status", () => {
  it("shows elapsed and remaining time during the configured working day", () => {
    const status = at("2026-10-19T10:00:00Z");
    expect(status.working).toBe(true);
    expect(status.state).toBe("working");
    expect(status.summary).toBe("Working · 3h since start · 6h left");
    expect(status.detail).toBe("Mon–Fri 09:00–18:00 · Ends Mon 18:00");
    expect(status.start?.toISOString()).toBe("2026-10-19T07:00:00.000Z");
    expect(status.end?.toISOString()).toBe("2026-10-19T16:00:00.000Z");
  });
  it("handles the opening instant, final minute and exact closing boundary", () => {
    expect(at("2026-10-19T07:00:00Z").summary).toBe("Working · 0m since start · 9h left");
    expect(at("2026-10-19T15:59:40Z").summary).toBe("Working · 8h 59m since start · 1m left");
    expect(at("2026-10-19T16:00:00Z").summary).toBe("Starts in 15h · 0m since end");
  });
  it("counts down before work and after work using the previous and next real weekday", () => {
    expect(at("2026-10-19T06:00:00Z").summary).toBe("Starts in 1h · 2d 14h since end");
    const after = at("2026-10-19T18:00:00Z");
    expect(after.state).toBe("off-duty");
    expect(after.summary).toBe("Starts in 13h · 2h since end");
    expect(after.detail).toBe("Mon–Fri 09:00–18:00 · Starts Tue 09:00");
    expect(after.nextStart?.toISOString()).toBe("2026-10-20T07:00:00.000Z");
  });
  it("skips the weekend and uses the new UTC offset after autumn DST", () => {
    const status = at("2026-10-25T11:00:00Z");
    expect(status.summary).toBe("Starts in 21h · 1d 19h since end");
    expect(status.nextStart?.toISOString()).toBe("2026-10-26T08:00:00.000Z");
    expect(status.previousEnd?.toISOString()).toBe("2026-10-23T16:00:00.000Z");
    expect(status.working).toBe(false);
    expect(status.state).toBe("off-duty");
  });
  it("uses the shorter spring weekend instead of adding fixed 24-hour days", () => {
    const status = at("2026-03-27T17:00:00Z");
    expect(status.nextStart?.toISOString()).toBe("2026-03-30T07:00:00.000Z");
    expect(status.summary).toBe("Starts in 2d 14h · 0m since end");
  });
  it("marks late local night as likely sleeping", () => {
    expect(at("2026-10-19T21:00:00Z").state).toBe("sleeping");
  });
  it("uses each zone's configured hours and fractional offset", () => {
    const status = workingHours({ zone: "Asia/Kolkata", hours: [10, 19] }, new Date("2026-10-19T05:00:00Z"));
    expect(status.summary).toBe("Working · 30m since start · 8h 30m left");
    expect(status.start?.toISOString()).toBe("2026-10-19T04:30:00.000Z");
  });
  it("closes a 24:00 working day at the next local midnight", () => {
    const status = workingHours({ zone: "UTC", hours: [0, 24] }, new Date("2026-10-23T23:30:00Z"));
    expect(status.summary).toBe("Working · 23h 30m since start · 30m left");
    expect(status.end?.toISOString()).toBe("2026-10-24T00:00:00.000Z");
    expect(workingHours({ zone: "UTC", hours: [0, 24] }, new Date("2026-10-24T00:00:00Z")).working).toBe(false);
  });
  it("keeps unknown browser zones and malformed hours explicit without throwing", () => {
    for (const zone of [
      { zone: "Unknown/Zone", hours: [9, 18] as [number, number] },
      { zone: "UTC", hours: [18, 9] as [number, number] },
    ]) {
      expect(workingHours(zone, new Date("2026-10-19T12:00:00Z"))).toEqual({
        working: false,
        state: "unavailable",
        summary: "Working hours unavailable",
        detail: "Working hours unavailable",
      });
    }
  });
});
