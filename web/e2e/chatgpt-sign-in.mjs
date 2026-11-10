// The real AI access Settings UI with a fictional browser sign-in outcome on both phone engines.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const target = new URL(process.argv[2]);
target.searchParams.set("settings", "ai");
const results = [];
for (const [engine, launcher] of [
  ["chromium", chromium],
  ["webkit", webkit],
]) {
  const browser = await launcher.launch();
  try {
    const context = await browser.newContext({
      viewport: { width: 320, height: 640 },
      isMobile: true,
      hasTouch: true,
      serviceWorkers: "block",
    });
    const page = await context.newPage();
    let connected = false;
    let starts = 0;
    await page.route("**/api/ai/access/list", (route) =>
      route.fulfill({
        json: {
          selected: "claude-code",
          uses: [],
          options: [
            {
              name: "chatgpt",
              title: "ChatGPT plan",
              summary: "A fictional ChatGPT plan",
              in_use: false,
              status: connected ? "signed in" : "not signed in",
              setup: connected ? null : "sign-in",
              fix: connected ? "" : "hz ai access login",
            },
          ],
        },
      }),
    );
    await page.route("**/api/ai/access/chatgpt-sign-in", async (route) => {
      const request = route.request();
      const headers = await request.allHeaders();
      assert.equal(request.method(), "POST");
      assert.equal(request.postData(), null);
      assert.equal(headers.origin, target.origin);
      if (headers["sec-fetch-site"] !== undefined) assert.equal(headers["sec-fetch-site"], "same-origin");
      // The fixture's environment credential backend cannot connect a plan or launch the OAuth browser.
      const refused = await route.fetch();
      assert.deepEqual(await refused.json(), { outcome: "failed" });
      starts += 1;
      connected = true;
      await route.fulfill({ json: { outcome: "connected" } });
    });
    await page.goto(target.href);
    await page.locator("#splash").waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Set up ChatGPT plan" }).tap();
    const dialog = page.getByRole("dialog", { name: "Set up ChatGPT plan" });
    const proceed = dialog.getByRole("button", { name: "Continue with ChatGPT" });
    await proceed.waitFor();
    assert.equal(starts, 0);
    for (const action of [proceed, dialog.getByRole("button", { name: "Check again" })]) {
      const box = await action.boundingBox();
      assert.ok(box && box.height >= 44 && box.x >= 0 && box.x + box.width <= 320, `${engine} touch target fits`);
    }
    await proceed.tap();
    await dialog.getByText("Your ChatGPT plan is connected.").waitFor();
    await dialog.getByText("Signed in", { exact: true }).waitFor();
    assert.equal(starts, 1);
    results.push(engine);
  } finally {
    await browser.close();
  }
}
console.log(JSON.stringify({ engines: results }));
