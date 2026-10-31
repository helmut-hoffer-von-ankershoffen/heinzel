import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";
import { publishJson } from "./atomic-json.mjs";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const url = new URL(process.argv[2]);
const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
url.pathname = "/doctor";
const browser = await (process.argv[3] === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 } });
await context.addInitScript(() => localStorage.setItem("hz.chat.more", "1"));
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
  await page.getByText("Read complete.").waitFor({ timeout: 30000 });
  const terms = page.getByRole("region", { name: "Proposed agent terms" });
  await terms.getByRole("button", { name: "Review these terms" }).click();
  await terms.getByRole("button", { name: "Confirm" }).click();
  await terms.getByText("These exact terms were accepted.").waitFor();
  const panel = page.getByRole("region", { name: "Connected read review" });
  await panel.getByLabel("Read operation").selectOption("calendar.events");
  await panel
    .getByLabel("Fixed arguments (JSON)")
    .fill(JSON.stringify({ window: { since: "2026-01-01T00:00:00Z", until: "2026-01-02T00:00:00Z" } }));
  await panel.getByRole("button", { name: "Prepare review" }).click();
  await panel.getByText("Account: fixture-account").waitFor();
  await panel.getByRole("button", { name: "Review access" }).click();
  await panel.getByRole("heading", { name: "Review connected read" }).waitFor();
  await page.evaluate(axe);
  const violations = await panel.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/Recorded state: approved/).waitFor();
  const schedule = page.locator("details").filter({ has: page.getByText("Recurring agent turns", { exact: true }) });
  await schedule.getByText("Recurring agent turns", { exact: true }).click();
  const consent = await schedule.getByRole("checkbox", { name: /Allow the separately approved calendar.events/ });
  assert.equal(await consent.isChecked(), false);
  await consent.check();
  const notification = schedule.getByRole("checkbox", { name: /Allow this agent to queue local notifications/ });
  assert.equal(await notification.isChecked(), false);
  await notification.check();
  await schedule.getByLabel("Every (minutes)").fill("60");
  await schedule.getByLabel("Requests per turn").fill("4");
  await schedule.getByRole("button", { name: "Propose recurring turns" }).click();
  await schedule.getByRole("button", { name: "Review this schedule" }).click();
  const card = schedule.getByRole("heading", { name: "Review agent schedule" }).locator("../..");
  assert.match((await card.textContent()) ?? "", /fixture-account/);
  assert.match((await card.textContent()) ?? "", /fixture-read/);
  assert.match((await card.textContent()) ?? "", /max_per_24h/);
  assert.match((await card.textContent()) ?? "", /topic_cooldown_seconds/);
  assert.doesNotMatch((await card.textContent()) ?? "", /approval_proof/);
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
  publishJson(join(directory, "daemon-start.json"), { session });
  for (let i = 0; i < 600 && !existsSync(join(directory, "daemon-finished.json")); i++)
    await new Promise((resolve) => setTimeout(resolve, 50));
  const finished = JSON.parse(readFileSync(join(directory, "daemon-finished.json"), "utf8"));
  assert.equal(finished.ok, true, `daemon failed: ${finished.failure}`);
  assert.equal(finished.deduplicated, true);
  await page.reload();
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const activity = page.getByRole("region", { name: "Scheduled notification activity" });
  await activity.getByText("Fictional title").waitFor({ timeout: 30000 });
  assert.match((await activity.textContent()) ?? "", /model-chosen/);
  if (process.argv[5] === "uncertain") {
    const inspect = activity.getByRole("button", { name: "Inspect or set aside outcome" });
    assert.equal(await inspect.isEnabled(), false);
    await activity.getByRole("checkbox", { name: /I understand this notice may already have been queued/ }).check();
    await inspect.click();
    await activity.getByText(/Topic: example · abandoned/).waitFor();
  } else {
    await activity.getByText(/queued; not confirmed shown/).waitFor();
  }
  await page.evaluate(axe);
  const activityViolations = await activity.evaluate(async (node) =>
    (await window.axe.run(node)).violations.map(({ id }) => id),
  );
  assert.deepEqual(activityViolations, []);
  assert.equal(await activity.evaluate((node) => node.scrollWidth > node.clientWidth), false);
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible =
    (await page
      .locator("body")
      .textContent()
      .catch(() => "")) ?? "";
  const safe = `${message}\n${visible}`.replaceAll(decodeURIComponent(token), "<redacted-session>");
  process.stderr.write(`${safe}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
