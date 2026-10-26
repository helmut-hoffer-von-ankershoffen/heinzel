// A real backend supplies all control/call/readiness data; only the OS vendor is faked by the Python fixture.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

async function openSharing(page) {
  const trigger = page.locator('[data-footer-content="tailscale:sharing"] button').first();
  const container = page.locator('[data-footer-part="tailscale:sharing"]');
  if ((await container.getAttribute("data-footer-hidden")) === "overflow") {
    const more = page.getByRole("region", { name: "More status items", exact: true });
    if (!(await more.isVisible())) await page.getByRole("button", { name: "More status items", exact: true }).click();
  }
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
  const mutations = { enable: 0, disable: 0, restart: 0 };
  const instances = new Set();
  const verify = [];
  let verifying = false;
  let observedVerification = () => {};
  const verificationObserved = new Promise((resolve) => {
    observedVerification = resolve;
  });
  let releaseVerification = () => {};
  const verificationGate = new Promise((resolve) => {
    releaseVerification = resolve;
  });
  await page.route("**/api/lifecycle/verify", async (route) => {
    const response = await route.fetch();
    if (response.ok() && (await response.json()).matches && !verifying) {
      verifying = true;
      observedVerification();
      await verificationGate;
    }
    await route.fulfill({ response });
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== "POST") return;
    if (path === "/api/tailscale/enable") mutations.enable++;
    if (path === "/api/tailscale/disable") mutations.disable++;
    if (path === "/api/restart") mutations.restart++;
  });
  page.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    if (response.ok() && path === "/api/lifecycle") instances.add((await response.json()).instance);
    if (response.ok() && path === "/api/lifecycle/verify") verify.push(await response.json());
  });
  await page.goto(url);
  await loaded(page);
  const anonymous = await context.request.get(new URL("/api/tailscale/status", url).href);
  assert.equal(anonymous.status(), 401);
  let panel = await openSharing(page);
  let control = panel.getByRole("switch", { name: "Share over Tailscale", exact: true });
  assert.equal(await control.getAttribute("aria-checked"), "false");
  assert.equal(await panel.getByRole("button", { name: "Copy URL", exact: true }).count(), 0);
  await control.click();
  // The retiring process's successful POST and even a held replacement proof cannot announce completion yet.
  let verificationTimer;
  try {
    await Promise.race([
      verificationObserved,
      new Promise((_resolve, reject) => {
        verificationTimer = setTimeout(
          () => reject(new Error("The mounted control must request replacement verification.")),
          15_000,
        );
      }),
    ]);
  } finally {
    clearTimeout(verificationTimer);
  }
  assert.equal(await control.getAttribute("aria-busy"), "true");
  assert.equal(await panel.getByRole("button", { name: "Copy URL", exact: true }).count(), 0);
  releaseVerification();
  await page.waitForFunction(
    () => document.querySelector('[role="switch"][aria-checked="true"][aria-busy="false"]'),
    null,
    { timeout: 60_000 },
  );
  await control.waitFor({ state: "visible" });
  assert.equal(await control.getAttribute("aria-busy"), "false");
  assert.equal(mutations.enable, 1);
  assert.equal(mutations.restart, 1);
  assert.ok(
    verify.some((answer) => answer.matches),
    "ON must include actual replacement policy proof.",
  );
  const ready = await sharingStatus(page);
  assert.equal(ready.state, "ready");
  assert.equal(ready.target, `http://127.0.0.1:${new URL(url).port}`);
  await panel.getByRole("button", { name: "Copy URL", exact: true }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  assert.equal(copied, "https://workstation.tail-example.ts.net/");
  assert.ok(!copied.includes("token"));
  const signIn = panel.getByRole("button", { name: "Copy sign-in link", exact: true });
  await signIn.click();
  const signed = await page.evaluate(() => navigator.clipboard.readText());
  assert.equal(new URL(signed).origin, "https://workstation.tail-example.ts.net");
  assert.equal(new URL(signed).hash, new URL(url).hash);
  assert.ok(
    !(await page.locator("body").textContent()).includes(new URL(url).hash),
    "A credential link must stay out of the DOM.",
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
  assert.equal(await panel.getByRole("button", { name: "Copy sign-in link", exact: true }).count(), 0);
  const status = await sharingStatus(page);
  assert.equal(status.state, "off");
  assert.equal(status.desired_enabled, false);
  assert.deepEqual(status.apply_settings, {});
  assert.deepEqual(mutations, { enable: 1, disable: 1, restart: 2 });
  assert.equal(instances.size, 3);
  assert.equal(verify.filter((answer) => answer.matches).length, 2);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      replacements: instances.size - 1,
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
