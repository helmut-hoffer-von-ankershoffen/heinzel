// The Heinzel's page (`/heinzel/:id`, bead agqz slice 7) against a real isolated `hz ui`: More → About opens it with
// focus on its heading; a rename applies at once and Undo puts the name back; Pause stops the check-in at once and
// Undo opens the resume as one whole item that runs only on the person's confirmation; Retire and Restore each
// confirm first, and a restore brings the check-in back paused. argv: the app's URL, and an
// optional folder for cropped desktop and phone screenshots.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium } from "playwright-core";

const require = createRequire(import.meta.url);
const url = new URL(process.argv[2] ?? "");
const shots = process.argv[3];
const token = /(?:^#|&)token=([^&]+)/.exec(url.hash)?.[1] ?? "";
url.pathname = "/doctor";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));

async function accessible(selector) {
  await page.evaluate(axe);
  const violations = await page.evaluate(async (selector) => {
    const found = await window.axe.run(document.querySelector(selector), {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    });
    return found.violations.map(
      (violation) => `${violation.id}: ${violation.help} ${violation.nodes.map((n) => n.target.join(" ")).join("; ")}`,
    );
  }, selector);
  assert.deepEqual(violations, [], `axe in ${selector}`);
}

/** One private call with the page's own token, as the app makes it; setup only, never the step under test. */
async function call(path, init = {}) {
  return await page.evaluate(
    async ({ path, init }) => {
      const response = await fetch(`/api${path}`, {
        ...init,
        headers: { Authorization: `Bearer ${sessionStorage.getItem("hz.token")}`, "Content-Type": "application/json" },
      });
      if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
      return await response.json();
    },
    { path, init },
  );
}

const schedule = async (session) => (await call(`/heinzel/session?session_id=${session}`)).schedule;

try {
  await page.goto(url.href);
  const setup = page.getByRole("dialog", { name: /Set up hz/ });
  await setup.waitFor({ timeout: 15_000 }).catch(() => {});
  if (await setup.isVisible()) {
    await setup.getByRole("button", { name: "Close setup" }).click();
    await setup.waitFor({ state: "hidden" });
  }
  const chat = page.getByRole("complementary", { name: "Chat" });
  await chat.getByLabel("What do you call it?").fill("Helmi", { timeout: 60_000 });
  await chat.getByRole("button", { name: "Start" }).click();
  const face = chat.getByRole("button", { name: /^Helmi, .+\. Switch Heinzel$/ });
  await face.waitFor({ timeout: 60_000 });
  const session =
    new URL(page.url()).searchParams.get("chat") ?? (await call("/heinzel/sessions")).sessions[0].session_id;

  // Setup: a check-in that runs, approved the way the chat approves it, so the page has something to pause.
  const zone = "UTC";
  await call("/heinzel/propose-check-in", {
    method: "POST",
    body: JSON.stringify({ session_id: session, request_id: "journey-check-in", timezone: zone }),
  });
  const proposed = await schedule(session);
  const query = new URLSearchParams({
    durable_session: session,
    request_id: "journey-activate",
    expected_revision: String(proposed.revision),
    action: "activate",
    expected_contract_version: String(proposed.contract_version),
    expected_preview_hash: proposed.preview_hash,
  });
  const approval = await call(`/system/runner/chat/propose-schedule-control?${query}`, { method: "POST" });
  await call(`/system/runner/chat/confirm?session=${approval.id}&action=${approval.actions[0].id}`, { method: "POST" });
  const active = await schedule(session);
  assert.equal(active.status, "active", "setup activated the check-in");

  // More → About: the page opens beside the chat, focus on its heading.
  await chat.getByRole("button", { name: "More: search, about" }).click();
  await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: "About Helmi" }).click();
  const heading = page.getByRole("heading", { level: 1, name: "Helmi, your Heinzel" });
  await heading.waitFor({ timeout: 30_000 });
  assert.equal(new URL(page.url()).pathname, `/heinzel/${session}`);
  await page.waitForFunction(() => document.activeElement?.tagName === "H1");
  const main = page.locator("main");
  assert.equal(await main.getByText(/\*\/5|[0-9a-f]{64}/).count(), 0, "no cron text and no hash on the page");
  await accessible("main");

  // Rename at once, then Undo.
  const look = page.getByRole("region", { name: "Look and name" });
  await look.getByLabel("Name", { exact: true }).fill("Fritz");
  await look.getByRole("button", { name: "Save" }).click();
  await look.getByText("Saved · Fritz").waitFor();
  await chat.getByRole("button", { name: /^Fritz, .+\. Switch Heinzel$/ }).waitFor();
  await look.getByRole("button", { name: "Undo" }).click();
  await look.getByText("Back as it was").waitFor();
  await face.waitFor();
  assert.equal(await look.getByLabel("Name", { exact: true }).inputValue(), "Helmi");

  // Pause at once, then Undo: the resume is one whole item, and nothing runs until the person confirms it.
  const lifecycle = page.getByRole("region", { name: "Pause or retire" });
  await lifecycle.getByText("Running · on its schedule").waitFor();
  if (shots) {
    await page.screenshot({ path: join(shots, "desktop-page.png"), clip: { x: 0, y: 0, width: 1440, height: 900 } });
  }
  await lifecycle.getByRole("button", { name: "Pause" }).click();
  await lifecycle.getByText("Paused · nothing runs until you resume").waitFor();
  assert.equal((await schedule(session)).status, "paused", "Pause took effect at once");
  await lifecycle.getByRole("button", { name: "Undo" }).click();
  const resume = page.getByRole("alertdialog", { name: "Resume Helmi?" });
  await resume.getByText(/^What resumes: /).waitFor();
  const confirm = resume.getByRole("button", { name: "Resume Helmi" });
  await page.waitForFunction(
    () => !document.querySelector('[role="alertdialog"] button:last-of-type')?.hasAttribute("disabled"),
  );
  assert.equal((await schedule(session)).status, "paused", "opening the item resumes nothing");
  await accessible('[role="alertdialog"]');
  if (shots) {
    const box = await resume.boundingBox();
    if (box)
      await page.screenshot({
        path: join(shots, "desktop-resume.png"),
        clip: { x: box.x - 16, y: box.y - 16, width: box.width + 32, height: box.height + 32 },
      });
  }
  await confirm.click();
  await resume.waitFor({ state: "hidden" });
  await lifecycle.getByText("Running · on its schedule").waitFor();
  const resumed = await schedule(session);
  assert.equal(resumed.status, "active", "the confirmed resume took effect");
  assert.equal(resumed.revision, active.revision + 2, "one pause and one resume");

  // Retire after its full confirmation, then Restore: it comes back with its check-in paused, never running on its
  // own until the person resumes it.
  await lifecycle.getByRole("button", { name: "Retire…" }).click();
  const retire = page.getByRole("alertdialog", { name: "Retire Helmi?" });
  await retire.getByText(/never wakes/).waitFor();
  await accessible('[role="alertdialog"]');
  await retire.getByRole("button", { name: "Retire Helmi" }).click();
  await lifecycle.getByText("Retired · it never wakes").waitFor();
  await chat.getByText("Retired · Restore it on its page").waitFor();
  assert.equal(await chat.getByLabel("Message Helmi").count(), 0, "a retired Heinzel takes no message");
  await page.getByRole("region", { name: "Records" }).waitFor();
  await lifecycle.getByRole("button", { name: "Restore…" }).click();
  const restore = page.getByRole("alertdialog", { name: "Restore Helmi?" });
  await restore.getByText("Its schedules stay paused until you resume them.").waitFor();
  await accessible('[role="alertdialog"]');
  await restore.getByRole("button", { name: "Restore Helmi" }).click();
  await lifecycle.getByText("Paused · nothing runs").waitFor();
  const restored = await schedule(session);
  assert.equal(restored.status, "paused", "a restore resumes nothing without its approval");
  await chat.getByLabel("Message Helmi").waitFor();

  // A phone: the same page passes axe and fits the width.
  await page.setViewportSize({ width: 390, height: 844 });
  await heading.waitFor();
  await accessible("main");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, "no sideways pan");
  if (shots) {
    await heading.scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(shots, "phone-page.png") });
  }
  assert.deepEqual(errors, []);
  process.stdout.write(`${JSON.stringify({ chatPage: "verified" })}\n`);
} catch (error) {
  const visible = await page.evaluate(() => (document.body.textContent ?? "").slice(0, 2000)).catch(() => "");
  process.stderr.write(`${String(error)}\n${visible}`.replaceAll(decodeURIComponent(token), "<redacted-session>"));
  process.exitCode = 1;
} finally {
  await browser.close();
}
