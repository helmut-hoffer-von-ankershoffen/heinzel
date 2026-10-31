import assert from "node:assert/strict";
import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const url = new URL(process.argv[2]);
const token = /(?:^#|&)token=([^&]+)/.exec(url.hash)?.[1] ?? "";
url.pathname = "/doctor";
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
await context.addInitScript(() => localStorage.setItem("hz.chat.more", "1"));
const page = await context.newPage();
const observed = [];
page.on("response", (response) => {
  const path = new URL(response.url()).pathname;
  if (["/api/heinzel/chat", "/api/heinzel/answer-chat", "/api/heinzel/session"].includes(path) && observed.length < 32)
    observed.push({ path, status: response.status() });
});
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  if (!(await page.getByLabel("Your message").isVisible()))
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const sentence = "Help me keep a private summary of my source current";
  await page.getByLabel("Your message").fill(sentence);
  await page.getByRole("button", { name: "Create Heinzel and take this message" }).click();
  const message = page.getByLabel("Message your Heinzel");
  await message.waitFor();
  assert.equal(await message.inputValue(), sentence);
  await page.getByRole("button", { name: "Queue message" }).click();
  const assistantReply = page.locator("p").filter({
    has: page.locator('strong[data-speaker="assistant"]'),
    hasText: "Done",
  });
  await assistantReply.waitFor({ timeout: 30000 });
  assert.equal(
    await assistantReply.evaluate((node) =>
      [...node.childNodes]
        .filter((child) => child.nodeType === Node.TEXT_NODE)
        .map((child) => child.textContent)
        .join("")
        .trim(),
    ),
    "Done",
  );
  const terms = page.getByRole("region", { name: "Proposed agent terms" });
  await terms.getByText("Review proposed terms").click();
  await terms.getByText(/Inspect the approved source/).waitFor();
  await terms.getByRole("button", { name: "Review these terms" }).click();
  await terms.getByRole("button", { name: "Confirm", exact: true }).click();
  await terms.getByText("These exact terms were accepted.").waitFor();
  const panel = page.getByRole("region", { name: "Connected read review" });
  await panel.getByLabel("Read operation").selectOption("calendar.events");
  await panel.getByRole("checkbox", { name: /Use a rolling window/ }).check();
  await panel.getByLabel("Lookback seconds").fill("60");
  await panel.getByLabel("Lookahead seconds").fill("600");
  await panel.getByLabel("Display timezone (IANA)").fill("Europe/Berlin");
  await panel.getByLabel("Fixed arguments (JSON)").fill("{}");
  await panel.getByRole("button", { name: "Prepare review" }).click();
  await panel.getByText("Account: account-one").waitFor();
  await panel.getByRole("button", { name: "Review access" }).click();
  await panel.getByRole("heading", { name: "Review connected read" }).waitFor();
  await panel.getByRole("button", { name: "Confirm", exact: true }).click();
  await panel.getByText(/Recorded state: approved/).waitFor();
  const schedule = page.locator("details").filter({ has: page.getByText("Recurring agent turns", { exact: true }) });
  await schedule.getByText("Recurring agent turns", { exact: true }).click();
  const consent = schedule.getByRole("checkbox", { name: /Allow the separately approved calendar.events/ });
  assert.equal(await consent.isChecked(), false);
  await consent.check();
  const notices = schedule.getByRole("checkbox", { name: /Allow this agent to queue local notifications/ });
  assert.equal(await notices.isChecked(), false);
  await notices.check();
  await schedule.getByLabel("Notification deduplication").selectOption("revision");
  await schedule.getByLabel("Requests per turn").fill("4");
  await schedule.getByRole("button", { name: "Propose recurring turns" }).click();
  await schedule.getByRole("button", { name: "Review this schedule" }).click();
  const review = schedule.getByRole("heading", { name: "Review agent schedule" }).locator("../..");
  await review.waitFor();
  await review.getByText(/300/).first().waitFor({ state: "visible" });
  await schedule.getByText(/at most one attempt per source item revision/i).waitFor({ state: "visible" });
  await schedule
    .getByText(/provider-reported.*does not establish complete source history/i)
    .waitFor({ state: "visible" });
  await schedule
    .getByText(/account-one/)
    .first()
    .waitFor({ state: "visible" });
  const directory = process.argv[3];
  writeFileSync(join(directory, "preconfirm.tmp"), "{}");
  renameSync(join(directory, "preconfirm.tmp"), join(directory, "preconfirm.json"));
  for (let i = 0; i < 600 && !existsSync(join(directory, "preconfirm-result.json")); i++)
    await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(JSON.parse(readFileSync(join(directory, "preconfirm-result.json"), "utf8")).zero_effects, true);
  await schedule.getByRole("button", { name: "Confirm", exact: true }).click();
  await schedule
    .getByText("Approved; the daemon must be running. A first turn may run in the current interval after approval.", {
      exact: true,
    })
    .waitFor();
  const session = new URL(page.url()).searchParams.get("chat");
  assert.ok(session);
  writeFileSync(join(directory, "wake.tmp"), JSON.stringify({ session }));
  renameSync(join(directory, "wake.tmp"), join(directory, "wake.json"));
  for (let i = 0; i < 600 && !existsSync(join(directory, "wake-result.json")); i++)
    await new Promise((resolve) => setTimeout(resolve, 50));
  const result = JSON.parse(readFileSync(join(directory, "wake-result.json"), "utf8"));
  assert.equal(result.ok, true, `wake failed: ${result.failure}`);
  await page.reload();
  const composerAfterReload = page.getByLabel("Message your Heinzel");
  const chatTabAfterReload = page.getByRole("tab", { name: "Chat", exact: true });
  await composerAfterReload.or(chatTabAfterReload).first().waitFor({ timeout: 30000 });
  if (!(await composerAfterReload.isVisible())) await chatTabAfterReload.click();
  assert.equal(new URL(page.url()).searchParams.get("chat"), session);
  assert.match(result.first_turn_id, /^interactive-/);
  const replies = page
    .locator("p")
    .filter({ has: page.locator("strong").filter({ hasText: /^(assistant|Daemon):$/ }) });
  const firstWakeReply = replies.nth(result.first_reply_index).locator("..");
  await firstWakeReply
    .locator("summary")
    .filter({ hasText: /^Tool activity for this reply · \d+ events$/ })
    .click();
  for (const tool of ["connected_read", "notification:send"]) {
    for (const status of ["requested", "completed"])
      await firstWakeReply.getByText(`${tool} · ${status}`, { exact: true }).waitFor({ timeout: 30000 });
  }
  console.log(JSON.stringify({ session, completed: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = {
    composer: await page
      .getByLabel("Message your Heinzel")
      .isVisible()
      .catch(() => false),
    reply: await page
      .locator("p")
      .filter({ has: page.locator('strong[data-speaker="assistant"]'), hasText: "Done" })
      .isVisible()
      .catch(() => false),
    assistantExcerpt: await page
      .locator("p")
      .evaluateAll((nodes) =>
        nodes
          .filter((node) => node.querySelector("strong")?.dataset.speaker === "assistant")
          .slice(0, 3)
          .map((node) => (node.textContent ?? "").slice(0, 200)),
      )
      .catch(() => []),
    proposal: await page
      .getByRole("region", { name: "Proposed agent terms" })
      .isVisible()
      .catch(() => false),
  };
  const diagnostics = `${message}\n${JSON.stringify({ visible, observed })}`;
  process.stderr.write(`${diagnostics.replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
