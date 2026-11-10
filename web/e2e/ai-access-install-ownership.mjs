// Real Chromium and real lifecycle controller; every install/restart response is a fictional intercepted fixture.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const [url] = process.argv.slice(2);
const browser = await chromium.launch();
try {
  for (const dismissal of ["close", "escape", "backdrop"]) {
    console.log(`dismissal: ${dismissal}`);
    const context = await browser.newContext({ serviceWorkers: "block" });
    const page = await context.newPage();
    let releaseInstall;
    let installStarted;
    let restartObserved;
    const started = new Promise((resolve) => {
      installStarted = resolve;
    });
    const restartDone = new Promise((resolve) => {
      restartObserved = resolve;
    });
    const pending = new Promise((resolve) => {
      releaseInstall = resolve;
    });
    let installs = 0;
    let restarts = 0;
    let restarted = false;
    await page.route("**/api/ai/access/list", (route) =>
      route.fulfill({
        json: {
          selected: "",
          uses: [],
          note: null,
          next: [],
          options: [
            {
              name: "chatgpt",
              title: "ChatGPT plan",
              summary: "Fictional client",
              module: "ai.access",
              plugin: null,
              in_use: false,
              status: "needs setup",
              detail: "",
              fix: "",
              models: [],
              runner: null,
              hosts: [],
              last_test: null,
              needs: "Add the OpenAI client",
              setup: "client",
              client: "openai",
            },
          ],
        },
      }),
    );
    await page.route("**/api/system/plugin/add-extra**", async (route) => {
      installs += 1;
      installStarted();
      await pending;
      await route.fulfill({ json: { extra: "openai", done: true, restart_required: true } });
    });
    await page.route("**/api/lifecycle", (route) =>
      route.fulfill({
        json: {
          instance: restarted ? "fixture-after" : "fixture-before",
          version: "1.0.0",
          restartable: true,
        },
      }),
    );
    await page.route("**/api/restart", (route) => {
      restarts += 1;
      restarted = true;
      restartObserved();
      return route.fulfill({ json: { restarting: true, instance: "fixture-before" } });
    });
    await page.route("**/api/lifecycle/verify", (route) =>
      route.fulfill({
        json: {
          instance: "fixture-after",
          version: "1.0.0",
          matches: true,
          mismatched: [],
        },
      }),
    );
    const address = new URL(url);
    await page.goto(`${address.origin}/?settings=ai${address.hash}`);
    await page.locator("#splash").waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Set up ChatGPT plan" }).click();
    let dialog = page.getByRole("dialog", { name: "Set up ChatGPT plan" });
    await dialog.getByRole("button", { name: "Add and restart" }).click();
    await started;
    if (dismissal === "close") await dialog.getByRole("button", { name: "Close" }).click();
    if (dismissal === "escape") {
      await dialog.getByRole("button", { name: "Close" }).focus();
      await page.keyboard.press("Escape");
    }
    if (dismissal === "backdrop")
      await page
        .locator(".viewport-overlay")
        .last()
        .click({ position: { x: 2, y: 2 } });
    await dialog.waitFor({ state: "detached", timeout: 3_000 });
    await page.getByRole("button", { name: "Set up ChatGPT plan" }).click();
    dialog = page.getByRole("dialog", { name: "Set up ChatGPT plan" });
    assert.equal(await dialog.getByRole("button", { name: "Add and restart" }).isDisabled(), true, dismissal);
    assert.equal(await dialog.getByText("Applied").count(), 0, dismissal);
    assert.equal(installs, 1, dismissal);
    releaseInstall();
    await Promise.race([
      restartDone,
      new Promise((_, reject) => setTimeout(() => reject(new Error("no restart")), 15_000)),
    ]);
    await page.waitForFunction(() => sessionStorage.getItem("hz.plugin-activation.v1") === null, null, {
      timeout: 15_000,
    });
    await page.waitForTimeout(250);
    assert.equal(restarts, 1, dismissal);
    assert.equal(installs, 1, dismissal);
    await context.close();
  }
} finally {
  await browser.close();
}
console.log("three dismissal paths passed");
