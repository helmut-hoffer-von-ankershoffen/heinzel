import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";
import { publishJson } from "./atomic-json.mjs";
import { until } from "./until.mjs";

const require = createRequire(import.meta.url);

/** The phone-sized browser a chat journey runs in, at /doctor: argv is the app's URL and the browser engine. */
export async function openChatJourney() {
  const url = new URL(process.argv[2]);
  const token = /(?:^#|&)token=([^&]+)/.exec(url.hash)?.[1] ?? "";
  url.pathname = "/doctor";
  const browser = await (process.argv[3] === "webkit" ? webkit : chromium).launch({ headless: true });
  const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
  return { axe, browser, context, page, token, url };
}

// After a reload, the Chat tab sits below page content that is still loading, so a click can land on what moved under
// the pointer and do nothing. Wait for startup to end, then press the tab: that needs no position. Require that Chat
// really is selected and shown.
export async function revealChat(page) {
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => {
    const root = document.getElementById("root");
    return root !== null && !root.hasAttribute("inert");
  });
  await page.getByRole("tab", { name: "Chat", exact: true }).press("Enter");
  await page.getByRole("tab", { name: "Chat", exact: true, selected: true }).waitFor();
  await page.getByRole("tabpanel", { name: "Chat", exact: true }).waitFor();
}

// Opening a disclosure has the same race as the Chat tab: proposals and activity load in above it after a reply, so a
// click at the summary's position can land on what moved there and leave it closed. Press Enter on the summary, which
// needs no position, only while it is closed, and require that its details element is open.
export async function openDisclosure(summary) {
  const details = summary.locator("xpath=ancestor-or-self::details[1]");
  if (!(await details.evaluate((node) => node.open))) await summary.press("Enter");
  await until(() => details.evaluate((node) => node.open), {
    message: `The disclosure "${await summary.innerText()}" did not open`,
  });
}

/** From the app's first load to the first message of a new Heinzel, with setup closed and Chat shown. */
export async function startChatJourney(page, url) {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  if (!(await page.getByLabel("Your message").isVisible()))
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await page.getByLabel("Your message").fill("Read my selected source.");
  await page.getByRole("button", { name: "Create a helper and take this message" }).click();
}

/** A grant survives a reload as approved, then is revoked, and the review panel still fits the phone. */
export async function revokeAfterReload(page, panel) {
  await page.reload();
  await revealChat(page);
  await panel.getByText(/Recorded state: approved/).waitFor();
  await panel.getByRole("button", { name: "Revoke this grant" }).click();
  await panel.getByText(/Recorded state: revoked/).waitFor();
  assert.equal(await panel.evaluate((node) => node.scrollWidth > node.clientWidth), false);
}

/** Hand the session to the test's daemon probe, require its deduplicated turn, and reopen Chat. */
export async function runDaemonTurn(page, directory, session) {
  publishJson(join(directory, "daemon-start.json"), { session });
  for (let i = 0; i < 600 && !existsSync(join(directory, "daemon-finished.json")); i++)
    await new Promise((resolve) => setTimeout(resolve, 50));
  const finished = JSON.parse(readFileSync(join(directory, "daemon-finished.json"), "utf8"));
  assert.equal(finished.ok, true, `daemon failed: ${finished.failure}`);
  assert.equal(finished.deduplicated, true);
  await page.reload();
  await revealChat(page);
}

/** Say what failed and what the page showed, without the session token. */
export async function reportJourneyFailure(page, token, error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .innerText()
    .catch(() => "");
  process.stderr.write(`${`${message}\n${visible}`.replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
}
