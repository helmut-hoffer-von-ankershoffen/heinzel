// Exercise the visible restart control against an isolated installed service, preserving the authenticated profile.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import axe from "axe-core";
import { chromium, webkit } from "playwright-core";

const [engineName, shots] = process.argv.slice(2);
const url = readFileSync(0, "utf8").trim();
mkdirSync(shots, { recursive: true });
const browser = await (engineName === "webkit" ? webkit : chromium).launch();
let stage = "open-settings";
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 667 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.name));
  let restarts = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/restart" && request.method() === "POST") restarts++;
  });
  await page.goto(`${url.split("#")[0]}?settings=web-server#${new URL(url).hash.slice(1)}`);
  const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
  const action = dialog.getByRole("button", { name: "Restart web server", exact: true });
  await action.waitFor();
  const read = () =>
    page.evaluate(async () => {
      const token = sessionStorage.getItem("hz.token") ?? localStorage.getItem("hz.device");
      const headers = { Authorization: `Bearer ${token}` };
      const lifecycle = await (await fetch("/api/lifecycle", { headers, cache: "no-store" })).json();
      const configured = await (
        await fetch("/api/system/config/get?key=hz.state_dir", { headers, cache: "no-store" })
      ).json();
      return { lifecycle, state: configured.value };
    });
  const before = await read();
  stage = "confirmation";
  await page.evaluate(() => {
    window.restartDocument = document;
    window.restartDialog = document.querySelector('[role="dialog"]');
  });
  await action.click();
  const confirmation = dialog.getByRole("alertdialog", { name: "Restart web server", exact: true });
  assert.match(await confirmation.textContent(), /This page reconnects for you/);
  assert.equal(restarts, 0);
  await page.evaluate(axe.source);
  stage = "confirmation-accessibility";
  const violations = await page.evaluate(async () => {
    const result = await window.axe.run(document.querySelector('[role="dialog"]'), {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    });
    return result.violations.map((violation) => violation.id);
  });
  assert.deepEqual(violations, []);
  await page.screenshot({ path: join(shots, `${engineName}-web-server-confirmation.png`) });
  await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(restarts, 0);
  await action.click();
  stage = "confirmed-restart";
  await confirmation.getByRole("button", { name: "Restart web server", exact: true }).click();
  await dialog
    .getByRole("status")
    .filter({ hasText: "Web server restarted and reconnected." })
    .waitFor({ timeout: 60_000 });
  const after = await read();
  stage = "replacement-and-profile";
  assert.notEqual(after.lifecycle.instance, before.lifecycle.instance);
  assert.equal(after.lifecycle.version, before.lifecycle.version);
  assert.equal(after.state, before.state);
  assert.equal(restarts, 1);
  assert.equal(await page.evaluate(() => window.restartDocument === document), true);
  assert.equal(await page.evaluate(() => window.restartDialog === document.querySelector('[role="dialog"]')), true);
  const sizing = await dialog.evaluate((element) => ({ width: element.clientWidth, content: element.scrollWidth }));
  assert.ok(sizing.content <= sizing.width + 1);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: join(shots, `${engineName}-web-server-reconnected.png`) });
  console.log(JSON.stringify({ replaced: true, profilePreserved: true, pagePreserved: true, restarts }));
} catch (error) {
  // Browser errors can quote the sign-in fragment or private profile paths; only fixed stages leave this process.
  console.error(`WEB_RESTART_STAGE:${stage}`);
  console.error(`WEB_RESTART_ERROR:${error instanceof Error ? error.name : "Unknown"}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
