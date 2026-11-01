import { expect, it } from "vitest";
import { compareCodeUnits, sortedKeys } from "./sort";

it("orders by code unit, not by locale", () => {
  // localeCompare puts "a" before "B" and "é" before "z"; code-unit order does not, and never varies by machine.
  expect(["b", "B", "a", "é", "z", "A"].sort(compareCodeUnits)).toEqual(["A", "B", "a", "b", "z", "é"]);
  expect(compareCodeUnits("a", "a")).toBe(0);
  expect(compareCodeUnits("a", "b")).toBeLessThan(0);
  expect(compareCodeUnits("b", "a")).toBeGreaterThan(0);
});

it("returns an object's keys sorted without touching the object", () => {
  const value = { b: 1, B: 2, a: 3 };
  expect(sortedKeys(value)).toEqual(["B", "a", "b"]);
  expect(Object.keys(value)).toEqual(["b", "B", "a"]);
});
