import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { requireOwnedExit } from "../e2e/system-audio-receipt.mjs";

test("native audio preflight refuses without disposable CI authorization before loading Electron", () => {
  const script = fileURLToPath(new URL("../e2e/system-audio-preflight.mjs", import.meta.url));
  for (const env of [{ CI: "true" }, { NATIVE_AUDIO_DISPOSABLE_CI: "1" }, {}]) {
    const result = spawnSync(process.execPath, [script], { env, encoding: "utf8", timeout: 5000 });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /only runs on an explicitly disposable macOS ARM CI runner/);
  }
});

test("missing or invalid service exit evidence cannot pass cleanup", async () => {
  const root = await mkdtemp(join(tmpdir(), "audio-exit-receipt-"));
  try {
    await assert.rejects(requireOwnedExit(root));
    await writeFile(join(root, "owned-exit"), JSON.stringify({ code: null, signal: null }));
    await assert.rejects(requireOwnedExit(root), /exit receipt is invalid/);
    await writeFile(join(root, "owned-exit"), JSON.stringify({ code: 0, signal: null }));
    await requireOwnedExit(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
