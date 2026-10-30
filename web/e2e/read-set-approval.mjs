import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const url = new URL(process.argv[2]);
const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
url.pathname = "/heinzel";
url.searchParams.set("chat", process.argv[4]);
const browser = await (process.argv[3] === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 } });
const page = await context.newPage();
let approvals = 0;
const outcomes = [];
page.on("request", (request) => {
  if (new URL(request.url()).pathname === "/api/system/runner/chat/confirm") approvals++;
});
page.on("response", (response) => {
  const path = new URL(response.url()).pathname;
  if (path.includes("connected-read-set")) outcomes.push(`${response.request().method()} ${path} ${response.status()}`);
});
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  if (!(await page.getByLabel("Message your Heinzel").isVisible()))
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const panel = page.getByRole("region", { name: "Sources for this chat" });
  await panel.getByRole("button", { name: "Ask about sources" }).click();
  await page.getByLabel("Message your Heinzel").fill("Suggest two bounded reads.");
  await page.getByRole("button", { name: "Queue message" }).click();
  await panel.getByText(/Suggested sources: no provider was contacted/).waitFor({ timeout: 30000 });
  assert.equal(approvals, 0);
  await panel.getByRole("button", { name: "Use suggested sources" }).click();
  await panel.getByRole("button", { name: "Prepare set" }).click();
  await panel.getByText(/Sources ready for review/).waitFor();
  assert.equal(await panel.getByText(/Account: account-one/).count(), 2);
  await panel.getByRole("button", { name: "Check current sources" }).click();
  await panel.getByRole("button", { name: "Review set access" }).click();
  await panel.getByRole("heading", { name: "Review these sources" }).waitFor();
  assert.equal(approvals, 0);
  await page.evaluate(axe);
  const violations = await panel.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/whole set has a recorded approval/).waitFor();
  await panel.getByRole("button", { name: "Revoke this set" }).click();
  await panel.getByText(/No current set approval could be verified/).waitFor();
  assert.equal(await panel.evaluate((node) => node.scrollWidth > node.clientWidth), false);
  assert.equal(approvals, 1);
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .textContent()
    .catch(() => "");
  const diagnostic = `${message}\n${outcomes.join("\n")}\n${visible}`.replaceAll(
    decodeURIComponent(token),
    "<redacted-session>",
  );
  process.stderr.write(`${diagnostic}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
