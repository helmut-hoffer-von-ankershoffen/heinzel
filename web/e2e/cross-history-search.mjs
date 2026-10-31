import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";

const url = new URL(process.argv[2]);
const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
const session = process.argv[3];
const target = process.argv[4];
const engine = process.argv[5];
const directory = process.argv[6];
assert.ok(["chromium", "webkit"].includes(engine));
const axe = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
url.pathname = "/heinzel";
url.searchParams.set("chat", session);
const browser = await { chromium, webkit }[engine].launch({ headless: true });
const context = await browser.newContext({
  serviceWorkers: "block",
  viewport: { width: 390, height: 844 },
  isMobile: true,
});
const page = await context.newPage();
let searchCalls = 0;
page.on("request", (request) => {
  if (
    ["/api/heinzel/indexed-conversation-search", "/api/heinzel/search-agent-conversations"].includes(
      new URL(request.url()).pathname,
    )
  )
    searchCalls++;
});
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  const message = page.getByLabel("Message your Heinzel");
  const tab = page.getByRole("tab", { name: "Chat", exact: true });
  await message.or(tab).first().waitFor({ timeout: 30000 });
  if (!(await message.isVisible())) await tab.click();
  const oneSummary = page.locator("summary:visible").filter({ hasText: /^Search conversation$/ });
  await oneSummary.first().waitFor({ state: "visible", timeout: 30000 });
  assert.equal(await oneSummary.count(), 1);
  await oneSummary.click();
  const one = oneSummary.locator("..").getByRole("region", { name: "Search conversation" });
  await one.getByLabel("Text").fill("distinctive phrase");
  await one.getByLabel("From").fill("2000-01-01T00:00");
  await one.getByLabel("To").fill("2100-01-01T00:00");
  await one.getByRole("button", { name: "Search", exact: true }).click();
  const match = one.locator("li").filter({ hasText: "Find this distinctive phrase" });
  await match.getByRole("button", { name: "Open message" }).click();
  const marker = page.getByText("Search result", { exact: true });
  await marker.waitFor({ timeout: 30000 });
  assert.match(await marker.locator("..").innerText(), /Find this distinctive phrase/);
  await one.getByLabel("Text").fill("Expiring distinct result");
  await one.getByRole("button", { name: "Search", exact: true }).click();
  const expiring = one.locator("li").filter({ hasText: "Expiring distinct result" });
  await expiring.getByRole("button", { name: "Open message" }).waitFor({ timeout: 30000 });
  writeFileSync(join(directory, "expire.request"), "expire");
  for (let index = 0; index < 300 && !existsSync(join(directory, "expire.done")); index++)
    await new Promise((resolve) => setTimeout(resolve, 50));
  assert.ok(existsSync(join(directory, "expire.done")), "fixture did not expire its owned message");
  await expiring.getByRole("button", { name: "Open message" }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: "This message page could not be loaded" })
    .waitFor({ timeout: 30000 });
  await one.getByRole("button", { name: "Search", exact: true }).click();
  await one.getByText("No matches in the scanned portion.").waitFor({ timeout: 30000 });
  assert.equal(await one.getByText("Expiring distinct result").count(), 0);

  const allSummary = page.locator("summary:visible").filter({ hasText: /^Search all Heinzel conversations$/ });
  await allSummary.first().waitFor({ state: "visible", timeout: 30000 });
  assert.equal(await allSummary.count(), 1);
  await allSummary.click();
  const all = allSummary.locator("..").getByRole("region", { name: "Search all conversations" });
  await all.getByLabel("Text").fill("second rare phrase");
  let release;
  let observed;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const intercepted = new Promise((resolve) => {
    observed = resolve;
  });
  await page.route(
    "**/api/heinzel/search-agent-conversations",
    async (route) => {
      observed();
      await gate;
      await route.continue().catch(() => {});
    },
    { times: 1 },
  );
  await all.getByRole("button", { name: "Search", exact: true }).click();
  let timer;
  try {
    await Promise.race([
      intercepted,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("Search request was not intercepted")), 30000);
      }),
    ]);
    await all.getByRole("button", { name: "Stop" }).click();
  } finally {
    clearTimeout(timer);
    release();
  }
  await all.getByLabel("From").fill("2000-01-01T00:00");
  await all.getByLabel("To").fill("2100-01-01T00:00");
  await all.getByRole("button", { name: "Search", exact: true }).click();
  const crossHit = all.locator("li").filter({ hasText: "Find the second rare phrase" });
  await crossHit.getByRole("button", { name: "Open message" }).waitFor({ timeout: 30000 });
  await all.getByText(/Results may be incomplete/).waitFor();
  await page.evaluate(axe);
  const violations = await all.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await crossHit.getByRole("button", { name: "Open message" }).click();
  await page.waitForURL((next) => next.searchParams.get("chat") === target);
  const destination = page.getByText("Search result", { exact: true });
  await destination.waitFor({ timeout: 30000 });
  assert.match(await destination.locator("..").innerText(), /Find the second rare phrase/);
  assert.ok(searchCalls <= 30, "the controlled search must remain bounded");
} catch (error) {
  process.stderr.write(`${String(error).replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
