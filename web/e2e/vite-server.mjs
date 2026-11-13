// One Vite dev server per journey, each with its own dependency cache.
// CI starts several journeys at once. On one shared web/node_modules/.vite cache, a server that re-optimizes the
// dependencies outdates the others, whose pages then get "504 Outdated Optimize Dep".
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "vite";

export async function createJourneyServer(name, config = {}, create = createServer) {
  const cacheDir = mkdtempSync(join(tmpdir(), `vite-${name}-`));
  let server;
  try {
    server = await create({ ...config, cacheDir });
  } catch (error) {
    rmSync(cacheDir, { recursive: true, force: true });
    throw error;
  }
  const close = server.close.bind(server);
  server.close = async () => {
    try {
      await close();
    } finally {
      rmSync(cacheDir, { recursive: true, force: true });
    }
  };
  return server;
}
