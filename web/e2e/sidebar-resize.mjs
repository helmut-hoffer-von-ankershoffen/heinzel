// The right sidebar is resizable: drag, keyboard, persistence across reload, the terminal refits, no handle on a phone.
// Usage: node e2e/sidebar-resize.mjs URL SCREENSHOT_DIR
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch({ headless: true });
const result = {};
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, serviceWorkers: "block" });
  const page = await context.newPage();
  await page.goto(url);
  const handle = page.getByRole("separator", { name: "Resize sidebar" });
  await handle.waitFor();
  const panel = page.locator(".workspace-panel");
  const widthOf = async () => Math.round((await panel.boundingBox()).width);
  assert.equal(await widthOf(), 384);

  await page.getByRole("button", { name: "Terminal" }).first().click();
  const screen = page.locator(".xterm-screen").first();
  await screen.waitFor();
  await page.waitForTimeout(800); // the first fit settles before the width is measured
  const before = await screen.boundingBox();

  const box = await handle.boundingBox();
  const y = box.y + box.height / 2;
  const x = box.x + box.width / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 150, y, { steps: 6 });
  assert.equal(await page.evaluate(() => document.documentElement.hasAttribute("data-sidebar-resizing")), true);
  await page.mouse.up();
  const dragged = await widthOf();
  assert.ok(Math.abs(dragged - 534) <= 2, `dragged width ${dragged}`);
  await page.waitForFunction(
    (previous) => document.querySelector(".xterm-screen").getBoundingClientRect().width > previous + 80,
    before.width,
  );
  result.refit = true;

  await handle.focus();
  await page.keyboard.press("Shift+ArrowRight");
  assert.equal(await widthOf(), dragged - 64);
  await page.keyboard.press("Home");
  assert.equal(await widthOf(), 320);
  await page.keyboard.press("End");
  const max = await widthOf();
  assert.ok(max <= 0.7 * 1440 + 1, `max ${max}`);
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowLeft");
  await page.reload();
  await page.getByRole("separator", { name: "Resize sidebar" }).waitFor();
  assert.equal(await widthOf(), 336, "the width survives a reload");
  await page.getByRole("separator", { name: "Resize sidebar" }).dblclick();
  assert.equal(await widthOf(), 384);
  await page.screenshot({ path: join(shots, "sidebar-resize.png") });
  await context.close();

  const phone = await browser.newContext({
    viewport: { width: 375, height: 812 },
    hasTouch: true,
    serviceWorkers: "block",
  });
  const small = await phone.newPage();
  await small.goto(url);
  await small.getByRole("button", { name: "Terminal" }).first().waitFor();
  assert.equal(await small.getByRole("separator", { name: "Resize sidebar" }).count(), 0);
  result.phone = "no handle";
  await phone.close();
} finally {
  await browser.close();
}
console.log(JSON.stringify(result));
