import { describe, expect, it } from "vitest";
import {
  cameraPoint,
  initialPhi,
  markerPosition,
  nightPath,
  sunLocation,
  worldPoint,
  zoneLocation,
} from "./worldClock";

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
  it("projects the actual marker centre and refuses an occluded city on the back of the globe", () => {
    const place: [number, number] = [52.5, 13.3667];
    const phi = initialPhi(place);
    expect(markerPosition(place, phi, 0.15)).toMatchObject({ x: expect.closeTo(50), visible: true });
    expect(markerPosition(place, phi + Math.PI, 0.15).visible).toBe(false);
  });
});
