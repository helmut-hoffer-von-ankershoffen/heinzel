// Real browser and lifecycle controller with fictional intercepted install and restart responses.
import assert from "node:assert/strict";
import { chromium, devices, webkit } from "playwright-core";

const [url] = process.argv.slice(2);
const phone = process.env.PLAYWRIGHT_BROWSER === "webkit";
const browser = await (phone ? webkit : chromium).launch();
try {
  for (const dismissal of [
    "duplicate",
    "settings-close",
    "escape",
    "backdrop",
    "failure",
    "lost-response",
    "same-dialog-loss",
    "restart-observation",
  ]) {
    console.log(`dismissal: ${dismissal}`);
    const context = await browser.newContext({ ...(phone ? devices["iPhone 15"] : {}), serviceWorkers: "block" });
    const page = await context.newPage();
    page.setDefaultTimeout(15_000);
    let releaseInstall;
    let installStarted;
    const started = new Promise((resolve) => {
      installStarted = resolve;
    });
    const pending = new Promise((resolve) => {
      releaseInstall = resolve;
    });
    let installs = 0;
    let restarts = 0;
    let verifications = 0;
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
      if (dismissal === "failure") return route.fulfill({ status: 500, json: { error: "Fictional install failed" } });
      if ((dismissal === "lost-response" || dismissal === "same-dialog-loss") && installs === 1)
        return route.abort("connectionreset");
      if (dismissal === "lost-response" || dismissal === "same-dialog-loss")
        return route.fulfill({ json: { extra: "openai", done: false, restart_required: false } });
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
      return route.fulfill({ json: { restarting: true, instance: "fixture-before" } });
    });
    await page.route("**/api/lifecycle/verify", (route) => {
      verifications += 1;
      if (dismissal === "restart-observation" && verifications === 1)
        return route.fulfill({ status: 400, json: { error: "Fictional verification refused" } });
      return route.fulfill({
        json: {
          instance: "fixture-after",
          version: "1.0.0",
          matches: true,
          mismatched: [],
        },
      });
    });
    const address = new URL(url);
    await page.goto(`${address.origin}/?settings=ai${address.hash}`);
    await page.locator("#splash").waitFor({ state: "hidden" });
    const setup = page.getByRole("button", { name: "Set up ChatGPT plan" });
    const dialog = page.locator('[role="dialog"][data-operation="/system/plugin/add-extra"]');
    await setup.click();
    await dialog.getByText("hz installs it, then restarts to load it.", { exact: false }).waitFor();
    const box = await dialog.boundingBox();
    const viewport = page.viewportSize();
    assert.ok(box && viewport && box.x >= 0 && box.x + box.width <= viewport.width);
    const add = dialog.getByRole("button", { name: "Add and restart" });
    assert.equal(installs, 0);
    assert.equal(restarts, 0);
    const installRequest = page.waitForRequest("**/api/system/plugin/add-extra**");
    if (dismissal === "duplicate") {
      // Both callbacks run in the same turn, before React swaps the confirmation for progress.
      await add.evaluate((button) => {
        button.click();
        button.click();
      });
    } else await add.click();
    await installRequest;
    await started;
    if (dismissal === "settings-close") {
      // Simulate the owning Settings tree disappearing while the operation is in flight.
      await page
        .getByRole("dialog", { name: "Settings", exact: true })
        .getByRole("button", { name: "Close settings", exact: true })
        .evaluate((button) => button.click());
      await setup.waitFor({ state: "detached" });
    }
    if (dismissal === "escape") await page.keyboard.press("Escape");
    if (dismissal === "backdrop") {
      if (phone) await page.touchscreen.tap(2, 2);
      else await page.mouse.click(2, 2);
    }
    await dialog.getByRole("progressbar").waitFor();
    assert.equal(await dialog.getAttribute("data-stage"), "running", dismissal);
    assert.equal(await dialog.getByRole("button", { name: "Close" }).count(), 0, dismissal);
    assert.equal(await dialog.getByText("Applied").count(), 0, dismissal);
    assert.equal(installs, 1, dismissal);
    releaseInstall();
    if (dismissal === "failure" || dismissal === "lost-response" || dismissal === "same-dialog-loss") {
      const unknown = dialog.getByText("The server cannot confirm how this installation ended. Do not retry it yet.");
      await unknown.waitFor();
      if (dismissal === "failure") {
        await dialog.getByRole("alert").getByText("Fictional install failed").waitFor();
        await dialog.getByRole("button", { name: "Close", exact: true }).last().click();
        await dialog.waitFor({ state: "detached" });
        await setup.click();
        await dialog.getByRole("alert").getByText("Fictional install failed").waitFor();
      }
      assert.equal(await add.count(), 0);
      assert.equal(await dialog.getByRole("button", { name: "Try again" }).count(), 0);
      assert.equal(restarts, 0);
      if (dismissal === "lost-response" || dismissal === "same-dialog-loss") {
        if (dismissal === "lost-response") {
          await page.reload();
          await page.locator("#splash").waitFor({ state: "hidden" });
          await setup.click();
          await unknown.waitFor();
          assert.equal(await add.count(), 0);
          assert.equal(installs, 1);
        }
        await dialog.getByRole("button", { name: "Resolve after checking" }).click();
        await dialog.getByRole("button", { name: "I checked; clear this tab's receipt" }).click();
        await add.waitFor();
        assert.equal(await add.isEnabled(), true);
        assert.equal(installs, 1);
        assert.equal(restarts, 0);
        await add.click();
        await dialog.getByText("Applied", { exact: true }).waitFor();
        assert.equal(installs, 2);
        assert.equal(restarts, 1);
      }
      await context.close();
      continue;
    }
    if (dismissal === "restart-observation") {
      await dialog.getByRole("alert").getByText("Fictional verification refused").waitFor();
      await dialog.getByRole("button", { name: "Try again" }).click();
    }
    await dialog.getByText("Applied", { exact: true }).waitFor();
    const receiptsCleared = await page.evaluate(
      () =>
        sessionStorage.getItem("hz.plugin-activation.v1") === null &&
        sessionStorage.getItem("hz.ai-client-install-pending.v1") === null,
    );
    assert.equal(receiptsCleared, true, dismissal);
    assert.equal(restarts, 1, dismissal);
    assert.equal(installs, 1, dismissal);
    await context.close();
  }
} finally {
  await browser.close();
}
console.log("shared client install confirmation, progress, and recovery passed");
