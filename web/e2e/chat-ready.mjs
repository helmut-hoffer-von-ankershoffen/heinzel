import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";
import { publishJson } from "./atomic-json.mjs";

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

// The first-run wizard opens when the profile answers, which a loaded runner can delay past any fixed pause; and while
// the splash or the wizard covers the app, #root is inert: a press on the composer's buttons then lands and does
// nothing (bead f67s). So wait for the profile's answer, close the wizard and see it gone, and wait until the app takes
// input again before the first press.
export async function openAppWithSetupClosed(page, url) {
  const profileAnswered = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/org/show" && response.ok(),
    { timeout: 30000 },
  );
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  await profileAnswered;
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const setup = page.getByRole("dialog", { name: "Set up hz" });
  if (await setup.isVisible()) {
    await setup.getByRole("button", { name: "Close setup" }).click();
    await setup.waitFor({ state: "hidden", timeout: 15000 });
  }
  await appTakesInput(page);
}

/** Neither the splash nor a dialog holds the app inert. */
export async function appTakesInput(page) {
  await page.waitForFunction(
    () => {
      const root = document.getElementById("root");
      return root !== null && !root.hasAttribute("inert") && document.getElementById("splash")?.hidden !== false;
    },
    null,
    { timeout: 15000 },
  );
}

/** Type the first message into the new-chat composer and press Create: the press must send the creation request. */
export async function createHeinzelWith(page, text) {
  if (!(await page.getByLabel("Your message").isVisible()))
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const composer = page.getByLabel("Your message");
  await composer.fill(text);
  assert.equal(await composer.inputValue(), text, "The composer did not take the first message");
  await appTakesInput(page);
  const sent = page.waitForRequest(
    (request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/heinzel/new-chat",
    { timeout: 15000 },
  );
  await page.getByRole("button", { name: "Create a Heinzel and take this message" }).click();
  await sent.catch(async () => {
    const state = await page.evaluate(() => ({
      rootInert: document.getElementById("root")?.hasAttribute("inert") ?? null,
      splashShown: document.getElementById("splash")?.hidden === false,
      dialogs: [...document.querySelectorAll('[role="dialog"]')].map((d) => d.getAttribute("aria-label") ?? d.id),
      alerts: [...document.querySelectorAll('[role="alert"]')].map((a) => a.textContent.slice(0, 120)),
    }));
    throw new Error(`Create a Heinzel sent no creation request: ${JSON.stringify(state)}`);
  });
}

/** From the app's first load to the first message of a new Heinzel, with setup closed and Chat shown. */
export async function startChatJourney(page, url) {
  await openAppWithSetupClosed(page, url);
  await createHeinzelWith(page, "Read my selected source.");
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
