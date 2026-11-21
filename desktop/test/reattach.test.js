import assert from "node:assert/strict";
import test from "node:test";
import { REATTACH_DELAYS_MS, reattach } from "../src/reattach.js";

test("re-attach tries now, then after 5, 10, 20 and 40 seconds, and stops at the first success", async () => {
  assert.deepEqual([...REATTACH_DELAYS_MS], [5000, 10000, 20000, 40000]);
  const waits = [];
  let attempts = 0;
  assert.equal(await reattach(async () => ++attempts === 3, { wait: async (ms) => { waits.push(ms); } }), true);
  assert.equal(attempts, 3);
  assert.deepEqual(waits, [5000, 10000]);
});

test("re-attach gives up after the last delay; a throwing attempt counts as a failure", async () => {
  const waits = [];
  let attempts = 0;
  const failing = async () => { attempts += 1; if (attempts % 2) throw new Error("refused"); return false; };
  assert.equal(await reattach(failing, { wait: async (ms) => { waits.push(ms); } }), false);
  assert.equal(attempts, 5);
  assert.deepEqual(waits, [5000, 10000, 20000, 40000]);
});

test("re-attach stops when the app quits", async () => {
  let quitting = false;
  let attempts = 0;
  const result = await reattach(async () => { attempts += 1; quitting = true; return false; }, { wait: async () => {}, cancelled: () => quitting });
  assert.equal(result, false);
  assert.equal(attempts, 1);
});
