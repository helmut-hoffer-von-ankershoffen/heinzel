// Actual Chromium, component and CSS, including cropped viewBoxes and all non-baby ages/builds.
// Usage: node e2e/avatar-tilt.mjs SCREENSHOT_DIR
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const [shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const server = await createServer({ server: { host: "127.0.0.1", port: 0, strictPort: false } });
await server.listen();
const address = server.httpServer.address();
assert(address && typeof address !== "string");
let browser;
try {
  try {
    browser = await chromium.launch();
  } catch {
    browser = await chromium.launch({ channel: "chrome" });
  }
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.route("**/theme-assets/tilt-fixture/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      // Deliberately mostly empty: its image viewport is much bigger than the core cap's drawing bounds.
      body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -240 512 840"><circle cx="256" cy="150" r="10" fill="#fff"/></svg>',
    }),
  );
  await page.goto(`http://127.0.0.1:${address.port}/e2e/avatar-tilt.html`);
  await page.locator(".hz-hat").nth(53).waitFor();
  await page.waitForFunction(
    () =>
      performance.getEntriesByType("resource").filter((entry) => entry.name.includes("/theme-assets/")).length === 3,
  );
  await page.waitForFunction(() => document.styleSheets.length > 0);
  await page.evaluate(() => {
    for (const svg of document.querySelectorAll("svg")) svg.setAttribute("data-idle", "tilt");
  });
  await page.waitForFunction(
    () => document.getAnimations().filter((animation) => animation.animationName === "hz-tilt").length === 54,
  );
  const comparison = await page.evaluate(() => {
    for (const animation of document.getAnimations()) {
      animation.pause();
      animation.currentTime = 500;
    }
    return Array.from(document.querySelectorAll("[data-look]"), (look) => ({
      look: look.getAttribute("data-look"),
      hats: Array.from(look.querySelectorAll(".hz-hat"), (hat) => {
        const local = hat.parentElement.getCTM().inverse().multiply(hat.getCTM());
        const hinge = new DOMPoint(256, 202).matrixTransform(local);
        return {
          matrix: [local.a, local.b, local.c, local.d, local.e, local.f],
          hinge: [hinge.x, hinge.y],
          bounds: [hat.getBBox().width, hat.getBBox().height],
          origin: getComputedStyle(hat).transformOrigin,
        };
      }),
    }));
  });
  await page.screenshot({ path: join(shots, "avatar-tilt.png"), fullPage: true });
  const failures = [];
  for (const {
    look,
    hats: [core, layer],
  } of comparison) {
    if (!core.matrix.every((value, index) => Math.abs(value - layer.matrix[index]) < 0.001)) {
      failures.push(`${look}: image bounds changed tilt: ${JSON.stringify({ core, layer })}`);
    }
    if (![core, layer].every((hat) => Math.abs(hat.hinge[0] - 256) < 0.001 && Math.abs(hat.hinge[1] - 202) < 0.001)) {
      failures.push(`${look}: cap brim moved: ${JSON.stringify({ core, layer })}`);
    }
    assert(layer.bounds[1] > core.bounds[1] + 300, `${look}: fixture must exercise different bounds`);
    assert(Math.abs(core.matrix[1]) > 0.05, `${look}: test must measure an actual tilt`);
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  const reduced = await page
    .locator(".hz-hat")
    .evaluateAll((hats) => hats.every((hat) => getComputedStyle(hat).transform === "none"));
  assert(reduced, "Reduced motion must keep every cap still");
  assert.deepEqual(errors, []);
  assert.equal(failures.length, 0, failures.slice(0, 3).join("\n"));
  console.log(JSON.stringify({ comparisons: comparison.length, reduced, errors }));
} finally {
  await browser?.close();
  await server.close();
}
