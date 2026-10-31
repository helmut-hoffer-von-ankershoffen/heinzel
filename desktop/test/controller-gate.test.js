import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { preserveCleanupFailure, waitForController } from "../e2e/controller-gate.mjs";

test("the test-only startup gate waits for the owning controller", async () => {
  const root = await mkdtemp(join(tmpdir(), "hz-controller-gate-"));
  try {
    let released = false;
    const pending = waitForController(root, { timeoutMs: 1000, intervalMs: 5 }).then(() => { released = true; });
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(released, false);
    await writeFile(join(root, "controller-ready"), "ready", { flag: "wx" });
    await pending;
    assert.equal(released, true);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("the test-only gate fails closed when no controller appears", async () => {
  const root = await mkdtemp(join(tmpdir(), "hz-controller-gate-"));
  try { await assert.rejects(waitForController(root, { timeoutMs: 25, intervalMs: 5 }), /did not become ready/); }
  finally { await rm(root, { recursive: true, force: true }); }
});


test("cleanup preserves both the original launch failure and cleanup failure", async () => {
  const primary = new Error("launch failed");
  const cleanup = new Error("cleanup failed");
  await assert.rejects(preserveCleanupFailure(primary, () => Promise.reject(cleanup)), (error) => {
    assert.ok(error instanceof AggregateError);
    assert.deepEqual(error.errors, [primary, cleanup]);
    return true;
  });
  await assert.rejects(preserveCleanupFailure(undefined, () => Promise.reject(cleanup)), (error) => error === cleanup);
  await preserveCleanupFailure(primary, () => Promise.resolve());
});
