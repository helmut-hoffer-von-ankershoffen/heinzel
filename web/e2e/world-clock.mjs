// Cold-open the World Clock in the real app. The API is fixed here so the browser check does not depend on a user's
// installed plugins or configured zones; COBE still initializes its real WebGL canvas and embedded map texture.
// Usage: node e2e/world-clock.mjs SESSION_URL
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const [sessionUrl] = process.argv.slice(2);
assert.ok(sessionUrl, "usage: node e2e/world-clock.mjs SESSION_URL");
const browser = await chromium.launch().catch(() => chromium.launch({ channel: "chrome" }));
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
await page.route("**/api/system/info", (route) =>
  route.fulfill({ json: { version: "e2e", modules: [{ qualname: "clock" }] } }),
);
await page.route("**/api/clock", (route) =>
  route.fulfill({
    json: [
      { zone: "Europe/Berlin", hours: [9, 18], working: true, time: "" },
      { zone: "America/New_York", hours: [9, 18], working: false, time: "" },
    ],
  }),
);
await page.route("**/api/system/ui/status", (route) =>
  route.fulfill({
    json: [
      {
        id: "clock:primary-zone",
        module: "clock",
        plugin: "heinzel-clock",
        order: 900,
        refresh: 60,
        problem: null,
        slots: { sidebar: true },
        item: { text: "Berlin", tone: "neutral", icon: "clock" },
      },
    ],
  }),
);
await page.addInitScript(() =>
  localStorage.setItem("hz.onboarding", JSON.stringify({ step: "you", visited: [], closed: true })),
);

try {
  const target = new URL(sessionUrl);
  target.searchParams.set("panel", "world-clock");
  await page.goto(target.href, { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "World Clock" });
  await panel.waitFor();
  const setup = page.getByRole("dialog");
  if (await setup.isVisible()) {
    await setup.getByRole("button", { name: "Close setup" }).click();
    await setup.waitFor({ state: "hidden" });
  }
  const canvas = panel.locator("canvas");
  await canvas.waitFor({ timeout: 15_000 });
  // Cobe draws asynchronously. Wait for a painted frame instead of treating canvas creation as a loaded map.
  await page.waitForFunction(
    () => {
      const canvas = document.querySelector('[aria-label="World Clock"] canvas');
      if (!(canvas instanceof HTMLCanvasElement) || !canvas.width || !canvas.height) return false;
      const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
      if (!gl) return false;
      const pixel = new Uint8Array(4);
      gl.readPixels(
        Math.floor(canvas.width / 2),
        Math.floor(canvas.height / 2),
        1,
        1,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        pixel,
      );
      return pixel[3] !== 0;
    },
    { timeout: 20_000 },
  );

  // Markers are exposed as projected, keyboard accessible hit targets over the canvas.
  const marker = panel.getByRole("button", { name: "Berlin · Europe/Berlin" });
  const markerName = panel.getByText("Berlin · Europe/Berlin");
  await marker.hover();
  await markerName.waitFor({ timeout: 1_000 });

  const left = panel.getByRole("button", { name: "Rotate globe left" });
  const right = panel.getByRole("button", { name: "Rotate globe right" });
  await left.waitFor();
  await right.waitFor();
  const before = await canvas.screenshot();
  await right.click();
  await page.waitForTimeout(100);
  const after = await canvas.screenshot();
  assert.notDeepEqual(after, before, "rotation control changes the rendered globe");

  // The sidebar ticker keeps its clock live while its animated strip pauses for reading.
  const ticker = page.getByTestId("zone-ticker");
  await ticker.waitFor();
  const track = ticker.locator(".hz-zone-track");
  const tickerTime = async () => {
    const label = await ticker.getAttribute("aria-label");
    assert.match(label ?? "", /Berlin \d{2}:\d{2}:\d{2}/);
    return label;
  };
  assert.equal(await track.evaluate((element) => getComputedStyle(element).animationPlayState), "running");
  await ticker.hover();
  assert.equal(await track.evaluate((element) => getComputedStyle(element).animationPlayState), "paused");
  const duringHoverBeforeTick = await tickerTime();
  await page.waitForFunction(
    (previous) => document.querySelector('[data-testid="zone-ticker"]')?.getAttribute("aria-label") !== previous,
    duringHoverBeforeTick,
    { timeout: 4_000 },
  );
  const afterHover = await tickerTime();
  assert.notEqual(afterHover, duringHoverBeforeTick, "the clock continues ticking while hover pauses the strip");
  await page.mouse.move(0, 0);
  assert.equal(await track.evaluate((element) => getComputedStyle(element).animationPlayState), "running");

  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(await track.evaluate((element) => getComputedStyle(element).animationName), "none");
  const beforeReducedMotion = await tickerTime();
  await page.waitForFunction(
    (previous) => document.querySelector('[data-testid="zone-ticker"]')?.getAttribute("aria-label") !== previous,
    beforeReducedMotion,
    { timeout: 4_000 },
  );
  assert.notEqual(await tickerTime(), beforeReducedMotion, "reduced motion stops scrolling, not the live clock");
  assert.deepEqual(errors, [], `browser errors: ${errors.join("; ")}`);
  console.log(
    JSON.stringify({
      panel: true,
      paintedGlobe: true,
      markerHover: true,
      rotation: true,
      tickerTicks: true,
      hoverPausesScroll: true,
      reducedMotionTicks: true,
      errors,
    }),
  );
} finally {
  await browser.close();
}
