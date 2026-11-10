import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("native audio preflight refuses without disposable CI authorization before loading Electron", () => {
  const script = fileURLToPath(new URL("../e2e/system-audio-preflight.mjs", import.meta.url));
  for (const env of [{ CI: "true" }, { HZ_DISPOSABLE_NATIVE_CI: "1" }, {}]) {
    const result = spawnSync(process.execPath, [script], { env, encoding: "utf8", timeout: 5000 });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /only runs on an explicitly disposable macOS ARM CI runner/);
  }
});
