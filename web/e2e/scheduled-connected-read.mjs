import assert from "node:assert/strict";
import { join } from "node:path";
import { openChatJourney, revealChat, runDaemonTurn } from "./chat-ready.mjs";

const { axe, browser, context, page, token, url } = await openChatJourney();
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  if (!(await page.getByLabel("Your message").isVisible())) await revealChat(page);
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
  const schedule = page
    .locator("details")
    .filter({ has: page.getByText("Recurring agent turns", { exact: true }) })
    .last();
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
  await runDaemonTurn(page, process.argv[4], session);
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
  await revealChat(page);
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
  const layout = await page
    .evaluate(() => {
      const summary = [...document.querySelectorAll("summary")].find(
        (node) => node.textContent === "Recurring agent turns",
      );
      const ancestors = [];
      for (let node = summary; node; node = node.parentElement) {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        ancestors.push({
          tag: node.tagName,
          className: node.className,
          hidden: node.hidden,
          inert: node.inert,
          display: style.display,
          visibility: style.visibility,
          width: box.width,
          height: box.height,
          ariaHidden: node.getAttribute("aria-hidden"),
        });
      }
      return {
        ancestors,
        tabs: [...document.querySelectorAll('[role="tab"]')].map((node) => ({
          text: node.textContent,
          selected: node.getAttribute("aria-selected"),
        })),
      };
    })
    .catch(() => null);
  await page.screenshot({ path: join(process.argv[4], `scheduled-${process.argv[3]}-failure.png`) }).catch(() => {});
  const diagnostic = `${message}\n${visible}\nLayout: ${JSON.stringify(layout)}`;
  process.stderr.write(`${diagnostic.replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
