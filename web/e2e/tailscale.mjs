// A real backend supplies all control/call/readiness data; only the OS vendor is faked by the Python fixture.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const ORIGIN = "https://workstation.tail-example.ts.net";

async function openSharing(page) {
  const trigger = page.locator('[data-footer-content="tailscale:sharing"] button').first();
  const more = page.getByRole("button", { name: "More status items", exact: true });
  const popup = page.getByRole("region", { name: "More status items", exact: true });
  // Footer overflow is measured after fonts, plugin controls and the viewport settle. During that first layout,
  // an item's anchor can briefly report `overflow` even though the follow-up measurement puts it inline. Wait
  // for an actionable, visible state instead of trusting that transient data attribute.
  await page.waitForFunction(() => {
    const visible = (element) => {
      if (!element) return false;
      const box = element.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && getComputedStyle(element).visibility !== "hidden";
    };
    return (
      visible(document.querySelector('[data-footer-content="tailscale:sharing"] button')) ||
      visible(document.querySelector('button[aria-label="More status items"]')) ||
      visible(document.querySelector('[role="region"][aria-label="More status items"]'))
    );
  });
  if (!(await trigger.isVisible()) && !(await popup.isVisible())) await more.click();
  await trigger.click();
  const panel = page.getByRole("region", { name: "Tailscale", exact: true });
  await panel.waitFor();
  return panel;
}

async function loaded(page) {
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !document.querySelector("#root")?.hasAttribute("inert"));
  await page.locator('[data-footer-part="tailscale:sharing"]').waitFor();
}

async function sharingStatus(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/tailscale/status", {
      headers: { Authorization: `Bearer ${sessionStorage.getItem("hz.token")}` },
    });
    if (!response.ok) throw new Error("Authenticated sharing status must succeed.");
    return response.json();
  });
}

async function externalStatus(request, url) {
  const session = new URLSearchParams(new URL(url).hash.slice(1)).get("token");
  assert.ok(session);
  return request.get(new URL("/api/tailscale/status", url).href, {
    headers: {
      Authorization: `Bearer ${session}`,
      Host: new URL(ORIGIN).host,
      Origin: ORIGIN,
    },
  });
}

async function withinViewport(locator) {
  await locator.page().waitForFunction(
    (element) => {
      const box = element.getBoundingClientRect();
      return box.x >= 0 && box.y >= 0 && box.right <= innerWidth + 1 && box.bottom <= innerHeight + 1;
    },
    await locator.elementHandle(),
  );
}

let input = "";
for await (const chunk of process.stdin) input += chunk;
const { url, shots } = JSON.parse(input);
if (shots) mkdirSync(shots, { recursive: true });
let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "chrome" });
}
try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 3,
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const page = await context.newPage();
  const errors = [];
  const failedRequests = [];
  const mutations = { enable: 0, disable: 0, restart: 0 };
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) failedRequests.push({ path, error: request.failure()?.errorText });
  });
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== "POST") return;
    if (path === "/api/tailscale/enable") mutations.enable++;
    if (path === "/api/tailscale/disable") mutations.disable++;
    if (path === "/api/restart") mutations.restart++;
  });
  await page.goto(url);
  await loaded(page);
  const anonymous = await context.request.get(new URL("/api/tailscale/status", url).href);
  assert.equal(anonymous.status(), 401);
  assert.equal((await externalStatus(context.request, url)).status(), 403);
  let panel = await openSharing(page);
  let control = panel.getByRole("switch", { name: "Share over Tailscale", exact: true });
  assert.equal(await control.getAttribute("aria-checked"), "false");
  assert.equal(await panel.getByRole("button", { name: "Copy URL", exact: true }).count(), 0);
  await control.click();
  // The live server applies access policy in-process; no daemon restart or replacement process is needed.
  await page.waitForFunction(
    () => document.querySelector('[role="switch"][aria-checked="true"][aria-busy="false"]'),
    null,
    { timeout: 15_000 },
  );
  assert.equal(await control.getAttribute("aria-busy"), "false");
  assert.equal(mutations.enable, 1);
  assert.equal(mutations.restart, 0);
  const ready = await sharingStatus(page);
  assert.equal(ready.state, "ready");
  assert.equal(ready.target, `http://127.0.0.1:${new URL(url).port}`);
  assert.equal((await externalStatus(context.request, url)).status(), 200);
  await panel.getByRole("button", { name: "Copy URL", exact: true }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  assert.equal(new URL(copied).origin, "https://workstation.tail-example.ts.net");
  assert.equal(new URL(copied).hash, new URL(url).hash);
  assert.ok(
    (await panel.textContent()).includes(new URL(copied).hash),
    "The displayed authenticated URL includes the token a person needs to share it.",
  );
  const layouts = [];
  for (const [width, height] of [
    [360, 780],
    [390, 844],
    [430, 932],
    [780, 360],
    [844, 390],
    [932, 430],
  ]) {
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width, height });
    await page.waitForFunction(() => document.querySelector("aside").getBoundingClientRect().width <= 57);
    panel = await openSharing(page);
    control = panel.getByRole("switch", { name: "Share over Tailscale", exact: true });
    await withinViewport(panel);
    await control.focus();
    assert.equal(await control.evaluate((element) => document.activeElement === element), true);
    await page.keyboard.press("Tab");
    const copy = panel.getByRole("button", { name: "Copy URL", exact: true });
    assert.equal(await copy.evaluate((element) => document.activeElement === element), true);
    await copy.press("Enter");
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), copied);
    await withinViewport(copy);
    const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
    assert.equal(geometry.width, width, "The mobile viewport must not silently scale a wider layout.");
    assert.ok(geometry.scroll <= geometry.width, JSON.stringify(geometry));
    if (shots) await page.screenshot({ path: join(shots, `sharing-${width}x${height}.png`) });
    layouts.push({ width, height, keyboard: true, noOverflow: true });
  }
  await control.click();
  await page.waitForFunction(() => !document.querySelector('[role="switch"][aria-checked="true"]'));
  await page.waitForFunction(() => document.querySelector('[role="switch"][aria-busy="false"]'));
  assert.equal(await panel.getByRole("button", { name: "Copy URL", exact: true }).count(), 0);
  const status = await sharingStatus(page);
  assert.equal(status.state, "off");
  assert.equal(status.desired_enabled, false);
  assert.equal((await externalStatus(context.request, url)).status(), 403);
  assert.deepEqual(status.apply_settings, {});
  assert.deepEqual(mutations, { enable: 1, disable: 1, restart: 0 });
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      mutations,
      copied: new URL(copied).origin,
      layouts,
      errors,
      browser: browser.version(),
    }),
  );
} finally {
  await browser.close();
}
