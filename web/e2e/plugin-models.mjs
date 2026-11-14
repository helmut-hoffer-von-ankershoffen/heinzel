// A plugin's own AI models in the real Settings (ADR 0159): the made-up plugin's option sits next to Claude Code,
// picking it makes it the AI access in use, and its model picker lists what the plugin offers with an unavailable one
// marked. Picking a model saves it and the live check answers through the plugin's model. Real Chromium against a real
// isolated `hz ui serve` with the fixture plugin installed; nothing is mocked.
// Usage: node e2e/plugin-models.mjs URL SCREENSHOT_DIR (prints one JSON object)
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
if (!url || !shots) throw new Error("Usage: node e2e/plugin-models.mjs URL SCREENSHOT_DIR");
mkdirSync(shots, { recursive: true });
const target = new URL(url);
target.searchParams.set("settings", "ai");

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "chrome" });
}
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(target.href);
  await page.locator("#splash").waitFor({ state: "hidden" });
  const card = page.locator('[data-option="demo-models"]');
  const option = card.getByRole("radio", { name: /Demo models/ });
  await option.waitFor();
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(shots, "plugin-models-1-access-options.png") });

  await option.click();
  await page.waitForFunction(
    () => document.querySelector('[data-option="demo-models"] [role="radio"]')?.getAttribute("aria-checked") === "true",
  );
  const pick = card.getByRole("button", { name: "Pick a model of Demo models" });
  await pick.click();
  const dialog = page.getByRole("dialog", { name: "Pick a model of Demo models" });
  await dialog.waitFor();
  const retired = dialog.getByRole("radio", { name: /^demo-retired/ });
  assert.match((await retired.textContent()) ?? "", /unavailable/);
  assert.equal(await retired.isEnabled(), true);
  assert.equal(await dialog.getByRole("radio", { name: "demo-fast" }).getAttribute("aria-checked"), "true");
  await page.screenshot({ path: join(shots, "plugin-models-2-model-picker.png") });

  await dialog.getByRole("radio", { name: "demo-deep" }).click();
  await dialog.getByText("Saved. Test it to see it answer.").waitFor();
  await dialog.getByRole("button", { name: "Test" }).click();
  await dialog.getByText(/^Answered: /).waitFor({ timeout: 30_000 });
  await page.screenshot({ path: join(shots, "plugin-models-3-model-picked.png") });

  const answer = (await dialog.getByText(/^Answered: /).textContent()) ?? "";
  console.log(JSON.stringify({ picked: true, unavailableMarked: true, answered: answer.startsWith("Answered: ") }));
} finally {
  await browser.close();
}
