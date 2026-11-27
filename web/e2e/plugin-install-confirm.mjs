// Real browser: Install on a row low in Discover asks once, in the operation dialog: in the viewport, focused on Cancel,
// with no inline card under the row and no second restart question (owner 2026-11-08).
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
    await page.route("**/api/system/plugin/showcase**", (route) =>
      route.fulfill({ json: { screenshots: [], soundscapes: [] } }),
    );
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
    let releaseInstall;
    const heldInstall = new Promise((resolve) => {
      releaseInstall = resolve;
    });
    let installCalls = 0;
    await page.route("**/api/stream/system/plugin/add**", async (route) => {
      installCalls += 1;
      await heldInstall;
      await route.fulfill({
        contentType: "text/event-stream",
        body: `event: result\ndata: ${JSON.stringify({ data: { action: "add", done: true, distribution: "heinzel-p29", candidate, plugins: [], restart_required: false, next: [] } })}\n\n`,
      });
    });
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
    const confirm = page.getByRole("dialog", { name: "Install heinzel-p29", exact: true });
    await confirm.waitFor();
    await page.waitForTimeout(150);
    const box = await confirm.boundingBox();
    const view = page.viewportSize();
    assert.ok(box && view, "confirmation has a box");
    assert.ok(
      box.x >= 0 && box.y >= 0 && box.x + box.width <= view.width && box.y + box.height <= view.height,
      `${name}: the whole confirmation is in the viewport ${JSON.stringify(box)}`,
    );
    assert.equal(await row.getByRole("group", { name: /Install this plugin/ }).count(), 0, "no inline card");
    assert.equal(await page.locator("[data-plugin-confirm]").count(), 0, "no inline card");
    assert.equal(await page.locator("[data-operation]").count(), 1, `${name}: one confirmation`);
    assert.equal(await page.getByText(/Restart and apply|Restart the server to apply/).count(), 0);
    assert.equal(
      await confirm
        .getByRole("button", { name: "Cancel", exact: true })
        .evaluate((el) => el === document.activeElement),
      true,
      `${name}: focus is on Cancel`,
    );
    assert.deepEqual(
      await confirm.locator("[data-pane-actions] button").allTextContents(),
      ["Cancel", "Install"],
      `${name}: one row, Cancel then Install`,
    );
    assert.equal(
      await confirm.locator("details").evaluate((element) => element.open),
      false,
      "provenance starts collapsed",
    );
    assert.equal(await confirm.evaluate((element) => element.matches(":modal")), true, "native modal blocks the page");
    for (let i = 0; i < 8; i += 1) {
      await page.keyboard.press("Tab");
      assert.equal(
        await confirm.evaluate((element) => element.contains(document.activeElement)),
        true,
        "focus stays in the modal",
      );
    }
    await page.evaluate(() => {
      const probe = document.createElement("button");
      probe.textContent = "Background fixture";
      probe.id = "background-fixture";
      probe.style.cssText = "position:fixed;left:0;top:0;width:40px;height:40px;z-index:9999";
      probe.addEventListener("click", () => {
        probe.dataset.clicked = "yes";
      });
      document.body.append(probe);
    });
    await page.mouse.click(3, 3);
    assert.equal(await confirm.isVisible(), true, "outside pointer cannot dismiss or operate the page");
    assert.equal(await page.locator("#background-fixture").getAttribute("data-clicked"), null);
    await page.screenshot({ path: `${shots}/plugin-install-confirm-${name}.png` });
    const installStarted = page.waitForRequest((request) => request.url().includes("/api/stream/system/plugin/add"));
    await confirm.getByRole("button", { name: "Install", exact: true }).click();
    const progress = page.getByRole("dialog", { name: /Installing heinzel-p29/ });
    await installStarted;
    await progress.getByRole("button", { name: "Hide progress" }).click();
    await page.mouse.click(3, 3);
    assert.equal(await page.locator("#background-fixture").getAttribute("data-clicked"), "yes");
    await page.locator("#background-fixture").evaluate((element) => element.remove());
    await dialog.getByRole("button", { name: "Close settings" }).click();
    await dialog.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Show installation progress" }).click();
    await progress.waitFor();
    assert.equal(await progress.evaluate((element) => element.matches(":modal")), true);
    assert.equal(installCalls, 1, "hide and reopen do not send another install");
    const response = page.waitForResponse((result) => result.url().includes("/api/stream/system/plugin/add"));
    releaseInstall();
    await response;
    results.push({ name, y: Math.round(box.y), viewportHeight: view.height });
    await context.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ confirmations: results }));
