// Settings → Connectors: switch two documents connectors on at once (ADR 0161), apply, and see both still on after a
// reload; the first switched on is named the default. Then the phone layout keeps both switches in reach.
// The server refuses restarts (as `make web-dev` does), so "Restart and apply" saves and says to restart.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const url = new URL(process.argv[2]);
const [, , , engine, shots] = process.argv;
const browser = await (engine === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
// The switches themselves, inside the row that is labelled Documents too.
const documents = () => page.locator('fieldset[aria-label="Documents"]');
const toggle = (adapter) => documents().getByRole("checkbox", { name: new RegExp(`^${adapter} ·`) });

async function openConnectors() {
  const connectors = new URL(url); // keeps the session token in the fragment
  connectors.search = "?settings=connectors";
  await page.goto(connectors.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  await documents().waitFor({ timeout: 15000 });
  await page.getByText("Loading choices…").first().waitFor({ state: "detached", timeout: 15000 });
}

try {
  await openConnectors();
  assert.equal(await toggle("drive").isChecked(), false);
  assert.equal(await toggle("wiki").isChecked(), false);
  await toggle("drive").check();
  await toggle("wiki").check();
  await page.getByText("drive, the first, is the default").waitFor();
  const row = page.locator("li").filter({ has: documents() });
  await row.getByRole("button", { name: "Restart and apply", exact: true }).click();
  await page.getByRole("button", { name: "Restart and apply hz ui" }).click();
  await row.getByRole("status").filter({ hasText: "Saved Documents." }).waitFor({ timeout: 15000 });

  await openConnectors();
  assert.equal(await toggle("drive").isChecked(), true);
  assert.equal(await toggle("wiki").isChecked(), true);
  await page.getByText("drive, the first, is the default").waitFor();
  if (shots) {
    await documents().scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shots}/multi-documents-${engine}.png` });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await documents().scrollIntoViewIfNeeded();
  for (const adapter of ["drive", "wiki"]) {
    const box = await toggle(adapter).locator("xpath=..").boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= 390, `${adapter} stays on screen on a phone`);
  }
  if (shots) await page.screenshot({ path: `${shots}/multi-documents-${engine}-phone.png` });
  console.log(JSON.stringify({ documents: ["drive", "wiki"] }));
} catch (error) {
  if (shots) await page.screenshot({ path: `${shots}/multi-documents-${engine}-failed.png`, fullPage: true });
  throw error;
} finally {
  await browser.close();
}
