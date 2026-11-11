// Isolated browser fixtures share the same loopback server and request boundary.
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

// This is only a base for parsing origin-form request targets; it is never contacted.
export const requestUrl = (target) => new URL(target, "https://fixture.invalid");

export async function startFixture(handler) {
  const denied = [];
  const server = await createServer({
    // Do not load the development config: its HZ_API_PORT proxy can reach a real backend.
    configFile: false,
    envFile: false,
    root: fileURLToPath(new URL("..", import.meta.url)),
    resolve: { alias: { "@": fileURLToPath(new URL("../src", import.meta.url)) } },
    server: { host: "127.0.0.1", port: 0 },
    plugins: [
      react(),
      tailwindcss(),
      {
        name: "fixture-api",
        configureServer: (vite) => {
          vite.middlewares.use((req, res, next) => {
            const path = requestUrl(req.url).pathname;
            const proxyPaths = ["/plugin-assets", "/plugin-sdk", "/theme-fonts", "/theme-assets"];
            const refuse = () => {
              denied.push(`${req.method} ${req.url}`);
              res.writeHead(403).end("fixture request denied");
            };
            if (proxyPaths.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) return refuse();
            return handler(req, res, () => (path === "/api" || path.startsWith("/api/") ? refuse() : next()));
          });
        },
      },
    ],
  });
  await server.listen();
  const address = server.httpServer.address();
  assert(address && typeof address !== "string");
  return { server, origin: `http://127.0.0.1:${address.port}`, denied };
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
