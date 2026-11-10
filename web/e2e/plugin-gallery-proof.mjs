// Focused built-app proof: galleries in desktop and every phone viewport, in both browser engines.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium, webkit } from "playwright-core";
import { guardGalleryRequest } from "./gallery-route.mjs";
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
    let closing = false;
    page.on("pageerror", (error) => errors.push(error.message));
    // Playwright does not re-route redirects after continue(), so fetch each
    // bounded local response with redirects disabled and refuse redirect status.
    await context.route("**/*", (route) => guardGalleryRequest(route, origin, unexpected, () => closing));
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
    closing = true;
    await context.close();
    assert.deepEqual(unexpected, [], "Unexpected requests must not escape during teardown");
  }
  console.log(JSON.stringify({ engine, viewports: viewports.length, gallery: true, axe: true }));
} finally {
  await browser.close();
}
