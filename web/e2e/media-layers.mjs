// Focused, mounted Chromium evidence for the media layer ordering. The shared verifier opens a real stream-shaped
// player, overlays each app popover and dialog at its iframe, and uses elementFromPoint to check browser hit testing.
// Usage: node e2e/media-layers.mjs URL SCREENSHOT_DIR (prints one JSON object)
import { mkdirSync } from "node:fs";
import { chromium } from "playwright-core";
import { verifyLayers } from "./layers.mjs";

const [url, shots] = process.argv.slice(2);
if (!url || !shots) throw new Error("Usage: node e2e/media-layers.mjs URL SCREENSHOT_DIR");
mkdirSync(shots, { recursive: true });

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "chrome" });
}
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(url);
  await page.getByRole("heading", { name: "Today" }).waitFor();
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  const result = await verifyLayers(page, shots, true);
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
