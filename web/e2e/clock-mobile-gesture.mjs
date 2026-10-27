// Mounted Chromium phone emulation; this does not claim physical iPhone coverage.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const [url] = process.argv.slice(2);
assert.ok(url);
const browser = await chromium.launch().catch(() => chromium.launch({ channel: "chrome" }));
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
await page.addInitScript(() =>
  localStorage.setItem("hz.onboarding", JSON.stringify({ step: "you", visited: [], closed: true })),
);
await page.clock.install({ time: new Date("2026-10-26T17:00:00Z") });
await page.route("**/api/system/info", (route) =>
  route.fulfill({ json: { version: "e2e", modules: [{ qualname: "clock" }] } }),
);
await page.route("**/api/clock", (route) =>
  route.fulfill({
    json: ["Europe/Berlin", "America/New_York", "America/Los_Angeles"].map((zone) => ({
      zone,
      hours: [9, 18],
      working: false,
      time: "",
    })),
  }),
);
await page.route("**/api/system/ui/actions**", (route) =>
  route.fulfill({
    json: [
      {
        id: "clock:team-clock",
        module: "clock",
        plugin: "heinzel-clock",
        order: 500,
        refresh: 60,
        problem: null,
        slots: { header: true },
        label: "World clock",
        icon: "globe",
        badge: 0,
        effect: { kind: "navigate", path: "/?panel=world-clock" },
      },
    ],
  }),
);
try {
  await page.goto(url);
  await page.getByRole("button", { name: "World clock", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "World clock panel" });
  await panel.waitFor();
  const pane = page.locator('[data-placement="below"]');
  const scroller = pane.locator("..");
  const geometry = await pane.evaluate((element) => ({
    panelTop: element.getBoundingClientRect().top,
    mainBottom: element.parentElement.querySelector("main").getBoundingClientRect().bottom,
    overflow: getComputedStyle(element.parentElement).overflowY,
    mainInert: element.parentElement.querySelector("main").inert,
    mainHidden: element.parentElement.querySelector("main").getAttribute("aria-hidden"),
  }));
  assert.equal(geometry.overflow, "auto");
  assert.equal(geometry.mainInert, false, "Today remains interactive");
  assert.notEqual(geometry.mainHidden, "true", "Today remains accessible");
  assert.ok(geometry.panelTop >= geometry.mainBottom - 1, "clock should follow Today content");
  // Starting a vertical finger drag on the globe must scroll the shared area, rather than being captured by rotation.
  await scroller.evaluate((element) => {
    element.scrollTop = element.scrollHeight - element.clientHeight - 200;
  });
  const dragLayer = panel.locator(".touch-pan-y");
  await dragLayer.waitFor();
  const globe = await dragLayer.boundingBox();
  assert.ok(globe, "interactive globe is present");
  const touch = await page.context().newCDPSession(page);
  const beforeTouch = await scroller.evaluate((element) => element.scrollTop);
  const x = globe.x + globe.width * 0.55;
  const y = globe.y + globe.height * 0.7;
  await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  await touch.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y - 120 }] });
  await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await page.waitForTimeout(200);
  const afterTouch = await scroller.evaluate((element) => element.scrollTop);
  assert.ok(
    afterTouch > beforeTouch,
    `touch scroll: before=${beforeTouch} after=${afterTouch} globe=${JSON.stringify(globe)} metrics=${JSON.stringify(await scroller.evaluate((e) => [e.scrollHeight, e.clientHeight]))}`,
  );
  await touch.detach();
  const last = panel.getByText("Los Angeles", { exact: true });
  const box = await last.boundingBox();
  assert.ok(box && box.y >= 0 && box.y + box.height <= 844, "last location is visible after scrolling");
} finally {
  await browser.close();
}
