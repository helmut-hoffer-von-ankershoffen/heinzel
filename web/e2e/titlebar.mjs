// The desktop app's integrated title bar in real Chromium, with the Electron bridge stubbed as preload.cjs exposes it
// on macOS, and the same page in a plain browser, which must keep its layout.
// Usage: node e2e/titlebar.mjs URL SCREENSHOT_DIR
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
if (!url || !shots) throw new Error("Usage: node e2e/titlebar.mjs URL SCREENSHOT_DIR");
mkdirSync(shots, { recursive: true });

const ROW = '[data-titlebar], .workspace-topbar, [data-placement="beside"] > .workspace-tabs';
const ROW_HEADER = `${ROW}, [data-placement="beside"]:not(:has(> .workspace-tabs)) .chat-panel-header`;

async function open(browser, desktop) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  if (desktop)
    await page.addInitScript(() => {
      window.heinzelDesktop = Object.freeze({
        workspaceAudio: true,
        titlebar: Object.freeze({ height: 44, left: 80, right: 0 }),
        lifecycle: () => Promise.resolve(null),
        onFullscreen: (callback) => {
          window.__titlebarFullscreen = callback;
          return () => {
            window.__titlebarFullscreen = null;
          };
        },
      });
    });
  await page.goto(url);
  await page.getByRole("heading", { name: "Today" }).waitFor();
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  return page;
}

/** Every row segment's box and every interactive element inside the row, with its computed app region. */
const measure = (page) =>
  page.evaluate(
    ([row, header]) => {
      const box = (element) => {
        const rect = element.getBoundingClientRect();
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, right: rect.right, bottom: rect.bottom };
      };
      const segments = [...document.querySelectorAll(header)].map((element) => ({
        name: element.className.toString().split(" ")[0] || "row",
        region: getComputedStyle(element).getPropertyValue("-webkit-app-region"),
        ...box(element),
      }));
      const controls = [...document.querySelectorAll(header)]
        .flatMap((element) => [...element.querySelectorAll("a, button, [role=tab], input")])
        .filter((element) => element.getClientRects().length > 0)
        .map((element) => ({
          label: element.getAttribute("aria-label") ?? element.textContent?.trim().slice(0, 30),
          region: getComputedStyle(element).getPropertyValue("-webkit-app-region"),
          ...box(element),
        }));
      const badge = document.querySelector(row)?.querySelector("button .rounded-full");
      return {
        desktop: document.documentElement.hasAttribute("data-desktop-titlebar"),
        inset: getComputedStyle(document.documentElement).getPropertyValue("--titlebar-inset-left").trim(),
        topbar: box(document.querySelector(".workspace-topbar")),
        segments,
        controls,
        badge: badge ? box(badge) : null,
      };
    },
    [ROW, ROW_HEADER],
  );

async function tabOrder(page, count) {
  await page.locator("body").focus();
  await page.evaluate(() => document.activeElement?.blur());
  const labels = [];
  for (let index = 0; index < count; index += 1) {
    await page.keyboard.press("Tab");
    labels.push(await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? ""));
  }
  return labels;
}

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "chrome" });
}
const result = {};
try {
  // A browser: no title bar mode, the 48 px top line as before.
  const web = await open(browser, false);
  const plain = await measure(web);
  assert.equal(plain.desktop, false);
  assert.equal(plain.topbar.height, 48, "The browser keeps its 48 px top line");
  assert.ok(
    plain.segments.every((segment) => segment.region !== "drag"),
    "Nothing drags a browser window",
  );
  await web.screenshot({ path: join(shots, "titlebar-browser.png") });
  result.browser = { topbar: plain.topbar.height };
  await web.close();

  const page = await open(browser, true);
  const bar = await measure(page);
  assert.equal(bar.desktop, true);
  assert.equal(bar.inset, "80px");
  for (const segment of bar.segments) {
    assert.equal(segment.y, 0, `${segment.name} starts at the window's top`);
    assert.equal(segment.height, 44, `${segment.name} is the 44 px row`);
    assert.equal(segment.region, "drag", `${segment.name} drags the window`);
  }
  assert.ok(bar.controls.length >= 6, "The row holds the sidebar icons and the header's controls");
  for (const control of bar.controls) {
    assert.equal(control.region, "no-drag", `${control.label} is clickable, not a drag handle`);
    assert.ok(control.x >= 80, `${control.label} stays clear of the traffic lights`);
    assert.ok(control.bottom <= 44, `${control.label} fits the row`);
  }
  if (bar.badge) assert.ok(bar.badge.y >= 0 && bar.badge.bottom <= 44, "The bell's badge is fully visible");
  const order = await tabOrder(page, 5);
  assert.deepEqual(order.slice(1, 5), ["Collapse navigation", "Today", "Notifications", "Search"]);
  await page.screenshot({ path: join(shots, "titlebar-desktop.png") });
  await page.screenshot({
    path: join(shots, "titlebar-desktop-top.png"),
    clip: { x: 0, y: 0, width: 1440, height: 96 },
  });
  result.desktop = { segments: bar.segments.length, controls: bar.controls.length, order };

  // A dialog over the row takes its box back from the drag region, so its top edge stays clickable.
  await page.getByRole("button", { name: "Search" }).click();
  const dialog = page.getByRole("dialog").first();
  await dialog.waitFor();
  assert.equal(
    await dialog.evaluate((element) => getComputedStyle(element).getPropertyValue("-webkit-app-region")),
    "no-drag",
  );
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });

  // Full screen: the lights are gone and the toggle moves to the start; the row keeps its height.
  await page.evaluate(() => window.__titlebarFullscreen?.(true));
  await page.waitForFunction(
    () => getComputedStyle(document.documentElement).getPropertyValue("--titlebar-inset-left").trim() === "0px",
  );
  const full = await measure(page);
  assert.equal(full.inset, "0px");
  assert.ok(full.controls[0].x < 80, "The toggle moves to the start in full screen");
  assert.ok(full.segments.every((segment) => segment.height === 44));
  await page.evaluate(() => window.__titlebarFullscreen?.(false));
  await page.waitForFunction(
    () => getComputedStyle(document.documentElement).getPropertyValue("--titlebar-inset-left").trim() === "80px",
  );

  // Collapsed: the expand button sits after the lights and the main header starts after it.
  await page.getByRole("button", { name: "Collapse navigation" }).click();
  // Measure the settled rail, not its width transition.
  await page.waitForFunction(() => document.querySelector("aside[aria-label]")?.getBoundingClientRect().width === 56);
  const expand = await page.getByRole("button", { name: "Expand navigation" }).boundingBox();
  assert.ok(expand && expand.x >= 80 && expand.y + expand.height <= 44);
  const rail = await measure(page);
  for (const control of rail.controls) {
    assert.ok(control.x >= 80, `${control.label} stays clear of the traffic lights when collapsed`);
    if (control.label !== "Expand navigation")
      assert.ok(control.x >= expand.x + expand.width, `${control.label} starts after the expand button`);
  }
  await page.screenshot({ path: join(shots, "titlebar-desktop-collapsed.png") });
  result.collapsed = { expandX: expand.x };
} finally {
  await browser.close();
}
console.log(JSON.stringify(result));
