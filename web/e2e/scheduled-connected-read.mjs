import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const url = new URL(process.argv[2]);
const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
url.pathname = "/doctor";
const browser = await (process.argv[3] === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 } });
const page = await context.newPage();
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  if (!(await page.getByLabel("Your message").isVisible()))
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await page.getByLabel("Your message").fill("Read my selected source.");
  await page.getByRole("button", { name: "Create Heinzel and take this message" }).click();
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Read complete.").waitFor({ timeout: 30000 });
  const terms = page.getByRole("region", { name: "Proposed agent terms" });
  await terms.getByRole("button", { name: "Review these terms" }).click();
  await terms.getByRole("button", { name: "Confirm" }).click();
  await terms.getByText("These exact terms were accepted.").waitFor();
  await page.getByLabel("Message your Heinzel").fill("Suggest an interval for this agent.");
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Suggest an interval for this agent.").waitFor();
  await page.getByText("Read complete.").nth(1).waitFor({ timeout: 30000 });
  const panel = page.getByRole("region", { name: "Connected read review" });
  await panel.getByLabel("Read operation").selectOption("calendar.events");
  if (process.argv[5] === "rolling") {
    await panel.getByRole("checkbox", { name: /Use a rolling window/ }).check();
    await panel.getByLabel("Lookback seconds").fill("60");
    await panel.getByLabel("Lookahead seconds").fill("600");
    await panel.getByLabel("Display timezone (IANA)").fill("Europe/Berlin");
    await panel.getByLabel("Fixed arguments (JSON)").fill("{}");
  } else {
    await panel
      .getByLabel("Fixed arguments (JSON)")
      .fill(JSON.stringify({ window: { since: "2026-01-01T00:00:00Z", until: "2026-01-02T00:00:00Z" } }));
  }
  await panel.getByRole("button", { name: "Prepare review" }).click();
  await panel.getByText("Account: fixture-account").waitFor();
  await panel.getByText("Provider-reported candidate read: Candidate metadata may be filtered locally.").waitFor();
  await panel.getByRole("button", { name: "Review access" }).click();
  await panel.getByRole("heading", { name: "Review connected read" }).waitFor();
  await page.evaluate(axe);
  const violations = await panel.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/Recorded state: approved/).waitFor();
  const schedule = page.locator("details").filter({ has: page.getByText("Recurring agent turns", { exact: true }) });
  await schedule.getByText("Recurring agent turns", { exact: true }).click();
  await schedule.getByText(/Suggested interval: every 7 minutes/).waitFor();
  assert.equal(await schedule.getByLabel("Every (minutes)").inputValue(), "5");
  await schedule.getByRole("button", { name: "Use interval in preview" }).click();
  assert.equal(await schedule.getByLabel("Every (minutes)").inputValue(), "7");
  const consent = await schedule.getByRole("checkbox", { name: /Allow the separately approved calendar.events/ });
  assert.equal(await consent.isChecked(), false);
  await consent.check();
  await schedule.getByLabel("Requests per turn").fill("4");
  await schedule.getByRole("button", { name: "Propose recurring turns" }).click();
  await schedule.getByRole("button", { name: "Review this schedule" }).click();
  await schedule.getByText("Provider-reported candidate read: Candidate metadata may be filtered locally.").waitFor();
  const card = schedule.getByRole("heading", { name: "Review agent schedule" }).locator("../..");
  assert.match(await card.innerText(), /420/);
  assert.match(await card.innerText(), /fixture-account/);
  assert.match(await card.innerText(), /fixture-read/);
  if (process.argv[5] === "rolling") assert.match(await card.innerText(), /rolling-window/);
  assert.doesNotMatch(await card.innerText(), /approval_proof/);
  await page.evaluate(axe);
  const scheduleViolations = await schedule.evaluate(async (node) =>
    (await window.axe.run(node)).violations.map(({ id }) => id),
  );
  assert.deepEqual(scheduleViolations, []);
  await schedule.getByRole("button", { name: "Confirm" }).click();
  await schedule.getByText(/approved; the daemon must be running/i).waitFor();
  const session = new URLSearchParams(page.url().split("?")[1]).get("chat");
  assert.ok(session);
  const directory = process.argv[4];
  writeFileSync(join(directory, "daemon-start.json"), JSON.stringify({ session }));
  for (let i = 0; i < 600 && !existsSync(join(directory, "daemon-finished.json")); i++)
    await new Promise((resolve) => setTimeout(resolve, 50));
  const finished = JSON.parse(readFileSync(join(directory, "daemon-finished.json"), "utf8"));
  assert.equal(finished.ok, true, `daemon failed: ${finished.failure}`);
  assert.equal(finished.deduplicated, true);
  await page.reload();
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await page
    .getByText(/Tool activity for this reply/)
    .nth(2)
    .click();
  await page
    .getByText(/connected_read · completed/)
    .first()
    .waitFor({ timeout: 30000 });
  await page.getByText("Approved read: first recorded result.").waitFor();
  assert.equal(await page.getByText("Approved read: first recorded result.").count(), 1);
  await page.getByText(/this is not event deduplication or complete history/i).waitFor();
  await panel.getByRole("button", { name: "Revoke this grant" }).click();
  await panel.getByText(/Recorded state: revoked/).waitFor();
  await page.reload();
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await schedule.getByText("Recurring agent turns", { exact: true }).click();
  await schedule.getByText(/stale · contract revision/).waitFor();
  assert.equal(await panel.evaluate((node) => node.scrollWidth > node.clientWidth), false);
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .innerText()
    .catch(() => "");
  process.stderr.write(`${message}\n${visible}`.replaceAll(decodeURIComponent(token), "<redacted-session>") + "\n");
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
