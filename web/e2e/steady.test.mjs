import assert from "node:assert/strict";
import { test } from "vitest";
import { steady } from "./steady.mjs";
import { until } from "./until.mjs";

// Playwright's words when the page reloads while a probe runs, as the live-restart driver met them on CI (run 41433444134).
const reloaded = () =>
  Promise.reject(new Error("page.evaluate: Execution context was destroyed, most likely because of a navigation"));

test("a probe the app's own reload interrupts is asked again, not fatal", async () => {
  let calls = 0;
  const answer = await until(() => steady(() => (++calls === 1 ? reloaded() : Promise.resolve("instance-2"))), {
    timeout: 1000,
    interval: 1,
    message: "never answered",
  });
  assert.equal(answer, "instance-2");
  assert.equal(calls, 2);
});

test("any other failure of a probe still fails the run", async () => {
  await assert.rejects(
    steady(() => Promise.reject(new Error("browserContext.close: Target page, context or browser has been closed"))),
    /has been closed/,
  );
});
