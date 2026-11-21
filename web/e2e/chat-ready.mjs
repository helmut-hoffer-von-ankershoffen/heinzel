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

/** The composer of the Heinzel's chat: "Message <name>" (ADR 0157: every chat is with a Heinzel). */
export const heinzelComposer = (page) =>
  page.getByRole("complementary", { name: "Chat" }).getByRole("textbox", { name: /^Message / });

/** Reviews, terms and recurring turns live on the Heinzel's page (`/heinzel/:id`), beside its chat. Open it, once. */
// The chat's More → About opens it. Then wait for the page's heading, keep the chat in reach on a phone, and restyle a
// select the WebKit quirk below hides.
/**
 * Open the Heinzel's page beside its chat. On a phone, where Page and Chat are tabs, `show` picks the one to end on:
 * the chat (default), with the page above it so both are in reach, or the page alone when a check needs the page's
 * controls inside the viewport.
 */
export async function openDetails(page, { show = "chat" } = {}) {
  const chat = page.getByRole("complementary", { name: "Chat" });
  await heinzelComposer(page).waitFor({ timeout: 30000 });
  const heading = page.getByRole("heading", { level: 1, name: /, your Heinzel$/ });
  const opened = !(await heading.isVisible());
  const before = new URL(page.url()).pathname;
  if (opened) {
    await chat.getByRole("button", { name: "More: search, about" }).click();
    await page
      .getByRole("dialog", { name: "More" })
      .getByRole("button", { name: /^About / })
      .click();
  }
  await heading.waitFor({ timeout: 30000 });
  // On a phone a new address selects the Page tab; with Chat selected the page stays above the chat, both in reach.
  // That selection runs just after the page renders (before any tap of a person's, which React handles after it), so
  // when About opened the page, wait for it to land before choosing Chat; else a slow runner chooses first and loses.
  // Only a route change selects Page. After a reload the heading can still be rendering when `opened` is decided, so
  // About is tapped on the page's own address: nothing navigates and Page is never selected (bead v606j). Wait for
  // the selection only when the address changed.
  const chatTab = page.getByRole("tab", { name: "Chat", exact: true });
  const pageTab = page.getByRole("tab", { name: "Page", exact: true });
  const navigated = new URL(page.url()).pathname !== before;
  if (navigated && (await pageTab.count()))
    await page.getByRole("tab", { name: "Page", exact: true, selected: true }).waitFor({ timeout: 30000 });
  if (show === "chat" && (await chatTab.count()) && !(await heinzelComposer(page).isVisible())) {
    await chatTab.press("Enter");
    await page.getByRole("tab", { name: "Chat", exact: true, selected: true }).waitFor();
  }
  if (show === "page" && (await pageTab.count()) && (await pageTab.getAttribute("aria-selected")) !== "true") {
    await pageTab.press("Enter");
    await page.getByRole("tab", { name: "Page", exact: true, selected: true }).waitFor();
  }
  if (show === "chat") await heinzelComposer(page).waitFor();
  const selects = await page.locator("main").locator("select").all();
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

/** A section of the Heinzel's page must be visible within 10 s; if not, fail with what hides it, not 30 s later in an action. */
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
/** The recurring-turns disclosure on the Heinzel's page; a disclosure around it would match too, so take the last. */
export function recurringTurns(page) {
  return page
    .locator("details")
    .filter({ has: page.getByText("Recurring Heinzel turns", { exact: true }) })
    .last();
}

// One place for the opening steps the workshop and template journeys share.
/** Name the first Heinzel at the chat's first start, open its page and send it this sentence; returns the composer. */
export async function createHeinzelWithMessage(page, sentence) {
  await createHeinzelWith(page, sentence);
  return sendFromPage(page, sentence);
}

/**
 * The chat as a returning person finds it: with no Heinzel yet, the first start names one (`createHeinzelWith`); with
 * one already there (a fixture made it), the chat opens on it, and the sentence goes into its composer, unsent.
 */
export async function openOrCreateHeinzel(page, text) {
  const chat = page.getByRole("complementary", { name: "Chat" });
  const name = chat.getByLabel("What do you call it?");
  const composer = heinzelComposer(page);
  const chatTab = page.getByRole("tab", { name: "Chat", exact: true });
  await name.or(composer).or(chatTab).first().waitFor({ timeout: 30000 });
  if (!(await name.isVisible()) && !(await composer.isVisible())) await chatTab.click();
  await name.or(composer).first().waitFor({ timeout: 30000 });
  if (await name.isVisible()) return createHeinzelWith(page, text);
  await appTakesInput(page);
  await composer.fill(text);
  assert.equal(await composer.inputValue(), text, "The composer did not take the first message");
}

/** `createHeinzelWithMessage` for a profile that may already have a Heinzel: opens it, or names the first one. */
export async function openOrCreateHeinzelWithMessage(page, sentence) {
  await openOrCreateHeinzel(page, sentence);
  return sendFromPage(page, sentence);
}

/** Open the Heinzel's page beside the chat, check the composer still holds the sentence, and send it. */
async function sendFromPage(page, sentence) {
  const message = heinzelComposer(page);
  await openDetails(page);
  assert.equal(await message.inputValue(), sentence);
  await page.getByRole("complementary", { name: "Chat" }).getByRole("button", { name: "Send" }).click();
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

/** With no Heinzel yet the chat is the first start (ADR 0157): press Start with the suggested name and look, which must
 * send the creation request, then type the first message into the new Heinzel's composer without sending it. */
export async function createHeinzelWith(page, text) {
  const chat = page.getByRole("complementary", { name: "Chat" });
  const name = chat.getByLabel("What do you call it?");
  if (!(await name.isVisible())) await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await name.waitFor({ timeout: 30000 });
  await appTakesInput(page);
  const sent = page.waitForRequest(
    (request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/heinzel/new-chat",
    { timeout: 15000 },
  );
  await chat.getByRole("button", { name: "Start", exact: true }).click();
  await sent.catch(async () => {
    const state = await page.evaluate(() => ({
      rootInert: document.getElementById("root")?.hasAttribute("inert") ?? null,
      splashShown: document.getElementById("splash")?.hidden === false,
      dialogs: [...document.querySelectorAll('[role="dialog"]')].map((d) => d.getAttribute("aria-label") ?? d.id),
      alerts: [...document.querySelectorAll('[role="alert"]')].map((a) => a.textContent.slice(0, 120)),
    }));
    throw new Error(`Start sent no creation request: ${JSON.stringify(state)}`);
  });
  const composer = heinzelComposer(page);
  await composer.waitFor({ timeout: 30000 });
  await composer.fill(text);
  assert.equal(await composer.inputValue(), text, "The composer did not take the first message");
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
