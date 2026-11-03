// Real browser: Install on a row low in Discover opens its confirmation under that row, inside the viewport and focused.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
const candidate = {
  spec: "heinzel-p29",
  requirement: "heinzel-p29 @ git+https://github.com/acme-example/heinzel-p29",
  distribution: "heinzel-p29",
  version: "1.0.0",
  summary: "Fictional plugin.",
  source: "git",
  url: "https://github.com/acme-example/heinzel-p29",
  publisher: "acme-example",
  first_party: false,
  private: false,
  revision: "b".repeat(40),
  installed_version: null,
  warnings: [],
};
const plugins = Array.from({ length: 30 }, (_, index) => ({
  name: `heinzel-p${index}`,
  short_name: `p${index}`,
  description: `Fictional plugin number ${index}.`,
  source: "github",
  community: true,
  install_spec: `heinzel-p${index}`,
  url: `https://example.org/p${index}`,
  repository_url: `https://github.com/acme-example/heinzel-p${index}`,
  private_repository: false,
  stars: null,
  installed: false,
}));
const browser = await chromium.launch();
const results = [];
try {
  for (const [name, width, height] of [
    ["desktop", 1280, 600],
    ["phone", 390, 667],
  ]) {
    const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: "block" });
    const page = await context.newPage();
    await page.route("**/api/system/plugin/search**", (route) => route.fulfill({ json: { plugins, warnings: [] } }));
    await page.route("**/api/system/plugin/add**", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.fulfill({
        json: {
          action: "add",
          done: false,
          distribution: "heinzel-p29",
          candidate,
          plugins: [],
          restart_required: false,
          next: [],
          previewed: true,
          operation_id: "fixture-operation",
        },
      });
    });
    await page.route("**/api/system/plugin/operation**", (route) =>
      route.fulfill({ json: { candidate, operation_id: "fixture-operation", status: "previewed" } }),
    );
    const address = new URL(url);
    await page.goto(`${address.origin}/?settings=plugins${address.hash}`);
    await page.locator("#splash").waitFor({ state: "hidden" });
    const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
    await dialog.getByRole("tab", { name: "Discover", exact: true }).click();
    const rows = dialog.getByRole("listitem");
    await rows.nth(29).waitFor();
    const row = rows.nth(29);
    await row.scrollIntoViewIfNeeded();
    const button = row.getByRole("button", { name: "Install", exact: true });
    await button.click();
    // Immediate feedback in the row itself, before the answer arrives.
    await row.getByRole("button", { name: "Checking…" }).waitFor();
    const card = row.getByRole("group", { name: /Install this plugin/ });
    await card.waitFor();
    await page.waitForTimeout(150);
    const box = await card.boundingBox();
    const view = page.viewportSize();
    assert.ok(box && view, "confirmation has a box");
    assert.ok(
      box.y >= 0 && box.y + Math.min(box.height, 40) <= view.height,
      `${name}: confirmation is in the viewport ${JSON.stringify(box)}`,
    );
    const rowBox = await row.boundingBox();
    assert.ok(box.y >= rowBox.y, `${name}: confirmation is under its own row`);
    assert.equal(await page.locator("[data-plugin-confirm]").count(), 1);
    assert.equal(
      await page.evaluate(() => document.activeElement?.hasAttribute("data-plugin-confirm")),
      true,
      `${name}: focus moved to the confirmation`,
    );
    assert.equal(
      await card.locator("details").evaluate((element) => element.open),
      false,
      "provenance starts collapsed",
    );
    await page.screenshot({ path: `${shots}/plugin-install-inline-${name}.png` });
    results.push({ name, y: Math.round(box.y), viewportHeight: view.height });
    await context.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ confirmations: results }));
