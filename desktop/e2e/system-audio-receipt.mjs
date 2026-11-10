import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

// The bootstrap observes the exact spawned service, not an inferred PID alone.
export async function requireOwnedExit(root) {
  const exit = JSON.parse(await readFile(join(root, "owned-exit"), "utf8"));
  assert.ok(Number.isInteger(exit.code) || (typeof exit.signal === "string" && exit.signal.length > 0), "Owned service exit receipt is invalid");
}
