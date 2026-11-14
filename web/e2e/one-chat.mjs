// One kind of chat (flag `one_chat`, bead agqz) against a real isolated `hz ui`: the first start names the first
// Heinzel, its chat opens with the face, the name and its switcher, More and Close, and there is no "Back to chat".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium } from "playwright-core";

const require = createRequire(import.meta.url);
const url = new URL(process.argv[2] ?? "");
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

try {
  await page.goto(url.href);
  // A fresh profile opens the setup first; it can be closed and comes back from the menu.
  const setup = page.getByRole("dialog", { name: /Set up hz/ });
  await setup.waitFor({ timeout: 15_000 }).catch(() => {});
  if (await setup.isVisible()) {
    await setup.getByRole("button", { name: "Close setup" }).click();
    await setup.waitFor({ state: "hidden" });
  }
  const chat = page.getByRole("complementary", { name: "Chat" });
  await chat.getByRole("heading", { name: "Meet your first Heinzelmännchen" }).waitFor({ timeout: 60_000 });
  await accessible('[aria-label="Chat"] form');
  // The look editor in a real browser: the hat overlay lies inside the figure, arrow keys pick a hat, Surprise me
  // changes the figure, Undo brings it back, and the form passes axe at the phone and the desktop size.
  const figure = chat.locator("svg[role=img]").first();
  const hatPart = chat.getByRole("button", { name: "Change the hat" });
  const box = await figure.boundingBox();
  const spot = await hatPart.boundingBox();
  assert.ok(box && spot && spot.height > 8 && spot.width > 8, "the hat overlay has a size");
  assert.ok(
    spot.x >= box.x - 1 &&
      spot.y >= box.y - 1 &&
      spot.x + spot.width <= box.x + box.width + 1 &&
      spot.y + spot.height <= box.y + box.height + 1,
    `the hat overlay lies inside the figure: ${JSON.stringify({ box, spot })}`,
  );
  await hatPart.focus();
  await page.keyboard.press("Enter");
  assert.equal(await hatPart.getAttribute("aria-pressed"), "true", "the hat part is chosen");
  const before = await figure.evaluate((node) => node.outerHTML);
  const hats = chat.getByRole("radiogroup", { name: "Hat" });
  // The swatches are buttons: Tab reaches each, Space chooses it.
  await hats.getByRole("radio").nth(2).focus();
  await page.keyboard.press("Space");
  await hats.getByRole("radio", { checked: true }).waitFor();
  assert.notEqual(await figure.evaluate((node) => node.outerHTML), before, "a hat colour changes the figure");
  const undo = chat.getByRole("button", { name: "Undo" });
  const beforeSurprise = await figure.evaluate((node) => node.outerHTML);
  await chat.getByRole("button", { name: "Surprise me" }).click();
  assert.notEqual(await figure.evaluate((node) => node.outerHTML), beforeSurprise, "Surprise me changes the figure");
  assert.equal(await undo.isDisabled(), false, "Undo is enabled after Surprise me");
  await undo.click();
  assert.equal(await figure.evaluate((node) => node.outerHTML), beforeSurprise, "Undo brings the look back");
  await page.setViewportSize({ width: 375, height: 812 });
  await accessible('[aria-label="Chat"] form');
  await page.setViewportSize({ width: 1440, height: 900 });
  await accessible('[aria-label="Chat"] form');
  await chat.getByLabel("What do you call it?").fill("Helmi");
  await chat.getByRole("button", { name: "Start" }).click();

  const face = chat.getByRole("button", { name: "Helmi. Switch Heinzel" });
  await face.waitFor({ timeout: 60_000 });
  await chat.getByLabel("Message Helmi").waitFor();
  assert.equal(await page.getByText("Back to chat", { exact: true }).count(), 0, "no Back to chat");
  await accessible('[aria-label="Chat"] .chat-panel-header');

  await face.click();
  const list = page.getByRole("dialog", { name: "Your Heinzels" });
  await list.getByRole("button", { name: /Helmi/ }).waitFor();
  await list.getByRole("button", { name: "New Heinzel" }).waitFor();
  await page.keyboard.press("Escape");
  assert.ok(await face.evaluate((node) => node === document.activeElement), "focus returns to the name");

  const more = chat.getByRole("button", { name: "More: search, details, earlier chats" });
  await more.click();
  await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: "Search" }).click();
  await chat.getByRole("region", { name: "Search conversation" }).waitFor();

  // The default Heinzel survives a reload without a link, and on a phone the same header and menus pass axe.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(url.href);
  await page
    .getByRole("tab", { name: /Chat/ })
    .or(page.getByRole("button", { name: /^(Open the chat|Chat)$/ }))
    .first()
    .click();
  const phoneFace = chat.getByRole("button", { name: "Helmi. Switch Heinzel" });
  await phoneFace.waitFor({ timeout: 60_000 });
  await accessible('[aria-label="Chat"] .chat-panel-header');
  await phoneFace.click();
  await page.getByRole("dialog", { name: "Your Heinzels" }).waitFor();
  await accessible('[role="dialog"]');
  await page.keyboard.press("Escape");
  await chat.getByRole("button", { name: "More: search, details, earlier chats" }).click();
  await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: "Earlier chats" }).click();
  await chat.getByRole("button", { name: "Chat with Helmi" }).click();
  await phoneFace.waitFor();
  assert.deepEqual(errors, []);
  process.stdout.write(`${JSON.stringify({ oneChat: "verified" })}\n`);
} catch (error) {
  const visible = await page.evaluate(() => (document.body.textContent ?? "").slice(0, 2000)).catch(() => "");
  process.stderr.write(`${String(error)}\n${visible}`.replaceAll(decodeURIComponent(token), "<redacted-session>"));
  process.exitCode = 1;
} finally {
  await browser.close();
}
