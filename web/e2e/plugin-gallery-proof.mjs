// Focused built-app proof: galleries in desktop and every phone viewport, in both browser engines.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium, webkit } from "playwright-core";
import { contributeGallery, exercisePluginGallery } from "./plugin-gallery.mjs";

const [url, shots] = process.argv.slice(2);
const origin = new URL(url).origin;
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
mkdirSync(shots, { recursive: true });
const engine = process.env.PLAYWRIGHT_BROWSER === "webkit" ? "webkit" : "chromium";
const browser = await (engine === "webkit" ? webkit : chromium).launch();
const viewports = [
  [1440, 900],
  [360, 780],
  [430, 932],
  [390, 667],
  [844, 390],
  [932, 430],
  [844, 320],
];
try {
  for (const [width, height] of viewports) {
    const context = await browser.newContext({
      viewport: { width, height },
      serviceWorkers: "block",
      colorScheme: width > 390 ? "dark" : "light",
    });
    const page = await context.newPage();
    const errors = [];
    const unexpected = [];
    page.on("pageerror", (error) => errors.push(error.message));
    // Playwright does not re-route redirects after continue(), so fetch each
    // bounded local response with redirects disabled and refuse redirect status.
    await context.route("**/*", async (route) => {
      const address = new URL(route.request().url());
      if (address.origin !== origin) {
        unexpected.push(address.href);
        return route.abort();
      }
      // This unrelated long-lived stream must not be buffered by route.fetch.
      if (address.pathname === "/api/system/notify/stream" && route.request().method() === "GET") {
        return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": fixture\n\n" });
      }
      const response = await route.fetch({ maxRedirects: 0 });
      if (response.status() >= 300 && response.status() < 400) {
        unexpected.push(`redirect ${address.pathname}`);
        return route.abort();
      }
      return route.fulfill({ response });
    });
    await context.routeWebSocket("**/*", (socket) => {
      unexpected.push(socket.url());
      socket.close();
    });
    const requests = await contributeGallery(page, origin);
    const address = new URL(url);
    address.searchParams.set("settings", "plugins");
    await page.goto(address.href);
    await page.locator("#splash").waitFor({ state: "hidden" });
    await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
    await exercisePluginGallery(page, requests, shots, `${engine}-${width}x${height}`);
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, [], "Unexpected requests must not escape the fixture");
    await context.close();
  }
  console.log(JSON.stringify({ engine, viewports: viewports.length, gallery: true, axe: true }));
} finally {
  await browser.close();
}
