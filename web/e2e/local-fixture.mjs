// Isolated browser fixtures share the same loopback server and request boundary.
import assert from "node:assert/strict";
import { createServer } from "vite";

// This is only a base for parsing origin-form request targets; it is never contacted.
export const requestUrl = (target) => new URL(target, "https://fixture.invalid");

export async function startFixture(handler) {
  const server = await createServer({
    server: { host: "127.0.0.1", port: 0 },
    plugins: [
      {
        name: "fixture-api",
        configureServer: (vite) => {
          vite.middlewares.use(handler);
        },
      },
    ],
  });
  await server.listen();
  const address = server.httpServer.address();
  assert(address && typeof address !== "string");
  return { server, origin: `http://127.0.0.1:${address.port}` };
}

export async function guardFixturePage(page, origin) {
  const blocked = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.route("**/*", (route) => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    blocked.push(route.request().url());
    return route.abort();
  });
  return { blocked, errors };
}
