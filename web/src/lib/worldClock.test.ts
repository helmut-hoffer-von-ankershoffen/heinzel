import { describe, expect, it } from "vitest";
import { cameraPoint, initialPhi, nightPath, sunLocation, worldPoint, zoneLocation } from "./worldClock";

describe("World Clock geography", () => {
  it("finds configured IANA places and leaves non-geographic zones unmapped", () => {
    expect(zoneLocation("Europe/Berlin")).toEqual([52.5, 13.3667]);
    expect(zoneLocation("UTC")).toBeNull();
    expect(zoneLocation("not/a-zone")).toBeNull();
  });
  it("centres a selected place in COBE's camera convention", () => {
    const point: [number, number] = [0, 13.4];
    const camera = cameraPoint(worldPoint(point), initialPhi(point), 0);
    expect(camera[0]).toBeCloseTo(0);
    expect(camera[2]).toBeCloseTo(1);
  });
  it("puts solar noon near Greenwich and reverses the longitude at midnight", () => {
    expect(sunLocation(new Date("2026-03-20T12:00:00Z"))[1]).toBe(0);
    expect(sunLocation(new Date("2026-03-20T00:00:00Z"))[1]).toBe(180);
    expect(Math.abs(sunLocation(new Date("2026-06-21T12:00:00Z"))[0])).toBeGreaterThan(23);
  });
  it("changes the night hemisphere when the globe is turned", () => {
    const now = new Date("2026-03-20T12:00:00Z");
    expect(nightPath(now, 0, 0)).not.toBe(nightPath(now, Math.PI, 0));
    expect(nightPath(now, 0, 0)).toContain("M");
  });
});
