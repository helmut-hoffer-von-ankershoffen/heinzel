// Test-only attach gate. It does not change navigation or grant desktop authority.
import { existsSync } from "node:fs";
import { join } from "node:path";

export async function waitForController(root, { timeoutMs = 30_000, intervalMs = 10 } = {}) {
  const deadline = performance.now() + timeoutMs;
  const marker = join(root, "controller-ready");
  while (!existsSync(marker)) {
    if (performance.now() >= deadline) throw new Error("Electron test controller did not become ready.");
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export async function preserveCleanupFailure(primary, cleanup) {
  try {
    await cleanup();
  } catch (error) {
    if (primary !== undefined) throw new AggregateError([primary, error], "Electron launch and cleanup both failed.");
    throw error;
  }
}
