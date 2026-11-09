// Focused built-app proof: galleries in desktop and every phone viewport, in both browser engines.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium, webkit } from "playwright-core";
import { contributeGallery, exercisePluginGallery } from "./plugin-gallery.mjs";

const [url, shots] = process.argv.slice(2);
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
    page.on("pageerror", (error) => errors.push(error.message));
    const requests = await contributeGallery(page);
    const address = new URL(url);
    address.searchParams.set("settings", "plugins");
    await page.goto(address.href);
    await page.locator("#splash").waitFor({ state: "hidden" });
    await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
    await exercisePluginGallery(page, requests, shots, `${engine}-${width}x${height}`);
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log(JSON.stringify({ engine, viewports: viewports.length, gallery: true, axe: true }));
} finally {
  await browser.close();
}
