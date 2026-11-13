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

// Runs in the page, so it stands alone: is this summary's disclosure open once the page has painted?
const openAfterPaint = (node) =>
  new Promise((resolve) => {
    const settled = () => resolve(node.parentElement.open);
    requestAnimationFrame(() => requestAnimationFrame(settled));
  });

async function pressUntilOpen(summary, presses) {
  if (await summary.evaluate(openAfterPaint)) return;
  assert.ok(presses > 0, "Details did not stay open");
  await summary.click();
  await pressUntilOpen(summary, presses - 1);
}

/** The conversation comes first: reviews, terms and schedules sit behind the Details disclosure. Open it, once. */
// One click is not proof: while the conversation loads the summary moves, and a press that lands beside it opens
// nothing; every later step then waits 30 s on a section that is not shown. So wait for the loaded conversation, click,
// and require that Details is open once the page has painted; click again if it is not, three presses at most.
export async function openDetails(page) {
  const summary = page.locator("details > summary", { hasText: /^Details$/ }).first();
  await summary.waitFor({ timeout: 30000 });
  await page.getByLabel("Message your Heinzel").waitFor({ timeout: 30000 });
  await pressUntilOpen(summary, 3);
  const selects = await summary.locator("xpath=..").locator("select").all();
  const restyled = (await Promise.all(selects.map((select) => select.evaluate(quirkRestyled)))).filter(Boolean).length;
  if (restyled) process.stderr.write(`openDetails: restyled ${restyled} select(s) after the WebKit visibility quirk\n`);
}

// Runs in the page, so it stands alone. WebKit sometimes leaves a <select> in an open disclosure with
// checkVisibility() false although it and every ancestor are displayed and painted; Playwright's visibility wait then
// never ends (0.9.40 candidate, bead rbl3). Users see the field. One restyle of the field clears it: do that only in
// exactly this state, and say whether it was done.
const quirkRestyled = (node) => {
  if (node.checkVisibility()) return false;
  for (let element = node; element; element = element.parentElement) {
    const style = getComputedStyle(element);
    if (element.nodeName === "DETAILS" && !element.open) return false;
    if (element.hidden || style.display === "none" || style.visibility !== "visible") return false;
  }
  const rect = node.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return false;
  const color = node.style.color;
  node.style.color = "rgb(1, 2, 3)";
  getComputedStyle(node).color;
  node.style.color = color;
  getComputedStyle(node).color;
  return true;
};

// Runs in the page: why is this element not visible? The first ancestor that hides it, or none.
const hiddenBy = (node) => {
  for (let element = node; element; element = element.parentElement) {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const name = `${element.nodeName.toLowerCase()}${element.getAttribute("aria-label") ? `[${element.getAttribute("aria-label")}]` : ""}`;
    if (element.nodeName === "DETAILS" && !element.open && element !== node) return `${name} is closed`;
    if (element.hidden) return `${name} is hidden`;
    if (style.display === "none") return `${name} has display none`;
    if (style.visibility !== "visible") return `${name} has visibility ${style.visibility}`;
    if (element === node && (rect.width === 0 || rect.height === 0)) return `${name} has no size`;
  }
  return node.checkVisibility() ? null : "every ancestor is shown, yet checkVisibility() is false";
};

/** A section behind Details must be visible within 10 s; if not, fail with what hides it, not 30 s later in an action. */
export async function requireShown(locator, label) {
  try {
    await locator.waitFor({ state: "visible", timeout: 10000 });
  } catch (error) {
    if (await locator.evaluate(quirkRestyled).catch(() => false)) {
      process.stderr.write(`requireShown: restyled ${label} after the WebKit visibility quirk\n`);
      if (
        await locator.waitFor({ state: "visible", timeout: 5000 }).then(
          () => true,
          () => false,
        )
      )
        return;
    }
    const cause = await locator.evaluate(hiddenBy).catch(() => "not on the page");
    throw new Error(`${label} is not visible: ${cause ?? "shown now, after the wait"}`, { cause: error });
  }
}

// One place for the locator the scheduled, template and generic chat journeys each need.
/** The recurring-turns disclosure of the Heinzel pane; the Details disclosure around it matches too, so take the last. */
export function recurringTurns(page) {
  return page
    .locator("details")
    .filter({ has: page.getByText("Recurring Heinzel turns", { exact: true }) })
    .last();
}

/**
 * The composer's "+" leads to the new Heinzel's draft, like Create: name it and save. Its chat then holds the
 * message in its composer. The name defaults to the one the journeys used before the draft.
 */
export async function saveHeinzelDraft(page, name = "New Heinzel") {
  const draft = page.getByRole("form", { name: "Your new Heinzel" });
  await draft.waitFor({ timeout: 15000 });
  await draft.getByLabel("Name", { exact: true }).fill(name);
  await draft.getByRole("button", { name: "Save and chat" }).click();
  await page.getByLabel("Message your Heinzel").waitFor({ timeout: 15000 });
}

// One place for the opening steps the workshop and template journeys share.
/** Create a Heinzel from the general chat with this sentence, open Details and send it; returns the message field. */
export async function createHeinzelWithMessage(page, sentence) {
  await page.getByLabel("Your message").fill(sentence);
  await page.getByRole("button", { name: "Create a Heinzel and take this message" }).click();
  await saveHeinzelDraft(page);
  const message = page.getByLabel("Message your Heinzel");
  await message.waitFor();
  await openDetails(page);
  assert.equal(await message.inputValue(), sentence);
  await page.getByRole("button", { name: "Send" }).click();
  return message;
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

/** Type the first message into the new-chat composer, press "+" and save the draft: Save sends the creation. */
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
  const draft = page.getByRole("form", { name: "Your new Heinzel" });
  await draft.waitFor({ timeout: 15000 });
  await draft.getByLabel("Name", { exact: true }).fill("New Heinzel");
  await draft.getByRole("button", { name: "Save and chat" }).click();
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
  await openDetails(page);
}

/** A grant survives a reload as approved, then is revoked, and the review panel still fits the phone. */
export async function revokeAfterReload(page, panel) {
  await page.reload();
  await revealChat(page);
  await openDetails(page);
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
  await openDetails(page);
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
