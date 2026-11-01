import assert from "node:assert/strict";
import test from "node:test";
import { compareCodeUnits, sortedKeys } from "../src/order.js";

test("orders by code unit, never by locale", () => {
  assert.deepEqual(["b", "B", "a", "é", "z", "A"].sort(compareCodeUnits), ["A", "B", "a", "b", "z", "é"]);
  assert.equal(compareCodeUnits("a", "a"), 0);
  assert.ok(compareCodeUnits("a", "b") < 0);
  assert.ok(compareCodeUnits("b", "a") > 0);
  assert.deepEqual(sortedKeys({ b: 1, B: 2, a: 3 }), ["B", "a", "b"]);
});
