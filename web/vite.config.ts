/// <reference types="vitest/config" />
// The web head's build (ADR 0039). `make web-build` writes the app into the Python package, which `hz ui` serves;
// `make web-dev` runs Vite in front of the web head's API, proxying /api so the browser stays on one origin.
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.HZ_API_PORT;
const vite = "http://localhost:5173";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  build: {
    outDir: "../src/heinzel/heads/web/static",
    emptyOutDir: true,
    // No inline modulepreload polyfill: the CSP allows scripts from this origin only.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1024,
  },
  server: api
    ? {
        proxy: {
          // Fonts a theme ships (ADR 0044) are served by the web head outside /api, without a token.
          "/theme-fonts": {
            target: `http://127.0.0.1:${api}`,
            changeOrigin: true,
            headers: { origin: `http://127.0.0.1:${api}` },
          },
          "/api": {
            target: `http://127.0.0.1:${api}`,
            changeOrigin: true,
            // The terminal is a WebSocket under /api; Vite rewrites its Origin only with this, like the requests'.
            ws: true,
            rewriteWsOrigin: true,
            // The web head refuses another Origin; to the browser, Vite's page and the API are one origin. The proxy
            // rewrites Origin for Vite's own page only, and refuses what another site sends, as the web head would.
            headers: { origin: `http://127.0.0.1:${api}` },
            // `bypass` doesn't see WebSocket upgrades: refuse another site's before its Origin is rewritten.
            configure: (proxy) => {
              proxy.on("proxyReqWs", (_request, incoming, socket) => {
                const origin = incoming.headers.origin;
                if (origin !== vite) socket.destroy();
              });
            },
            bypass: (request) => {
              const origin = request.headers.origin;
              const site = request.headers["sec-fetch-site"];
              if ((origin && origin !== vite) || site === "cross-site" || site === "same-site") return false;
              return undefined;
            },
          },
        },
      }
    : undefined,
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
