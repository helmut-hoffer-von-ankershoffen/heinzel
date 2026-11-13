import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { openDetails } from "./chat-ready.mjs";

const url = new URL(process.argv[2]);
const token = /(?:^#|&)token=([^&]+)/.exec(url.hash)?.[1] ?? "";
url.pathname = "/heinzel";
url.searchParams.set("chat", process.argv[3]);
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  const message = page.getByLabel("Message your Heinzel");
  const header = page.getByRole("img", { name: /^Original/ }).first();
  const hatFill = '[fill="#1E88E5"]';
  const checkHeader = async (hatBlue) => {
    await header.waitFor({ timeout: 30000 });
    const view = await header.getAttribute("data-view");
    assert.equal(view, "bust");
    // Chosen look: old, long beard, rose shirt, scarf; the hat is green before the change and blue after it.
    assert.match((await header.getAttribute("aria-label")) ?? "", /wearing a scarf/);
    assert.equal((await header.locator(hatFill).count()) > 0, hatBlue);
    assert.ok((await header.locator('[fill="#43A047"]').count()) > 0 !== hatBlue);
  };
  const chatTab = page.getByRole("tab", { name: "Chat", exact: true });
  await message.or(chatTab).first().waitFor({ timeout: 30000 });
  if (!(await message.isVisible())) await chatTab.click();
  await checkHeader(false);
  await openDetails(page);
  await message.fill("Make the hat blue, keep my other preferences");
  await page.getByRole("button", { name: "Send" }).click();
  const editor = page
    .locator("details")
    .filter({ has: page.locator("summary").filter({ hasText: /^Customize your Heinzel(?: · suggested look)?$/ }) })
    .last(); // the Details disclosure around it matches too
  await editor.locator("summary").click();
  const suggestion = editor.getByRole("region", { name: "Suggested identity" });
  await suggestion.waitFor({ timeout: 30000 });
  const hat = () =>
    editor
      .getByRole("radiogroup", { name: "Hat colour" })
      .getByRole("radio", { checked: true })
      .getAttribute("aria-label");
  assert.equal(await hat(), "Green");
  await suggestion.getByRole("button", { name: "Use suggested identity" }).click();
  assert.equal(await hat(), "Blue");
  const saved = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/heinzel/profile" && response.request().method() === "POST",
  );
  await editor.getByRole("button", { name: "Save name and look" }).click();
  assert.equal((await saved).status(), 200);
  await page.reload();
  await message.or(chatTab).first().waitFor({ timeout: 30000 });
  if (!(await message.isVisible())) await chatTab.click();
  await openDetails(page);
  await editor.locator("summary").click();
  assert.equal(await hat(), "Blue");
  await checkHeader(true);
  const avatar = editor.getByRole("img", { name: "Appearance preview" });
  assert.ok((await avatar.locator('[fill="#1E88E5"]').count()) > 0);
  // Phone width: the header avatar stays visible next to the name.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.reload();
  await message.or(chatTab).first().waitFor({ timeout: 30000 });
  if (!(await message.isVisible())) await chatTab.click();
  await checkHeader(true);
  const box = await header.boundingBox();
  assert.ok(box && box.width > 16 && box.x >= 0 && box.x + box.width <= 375);
  // Phone: the Heinzel's page shows it read-only; "Edit in chat" opens the one editor in its chat's Details.
  const pageTab = page.getByRole("tab", { name: "Page", exact: true });
  await pageTab.click();
  const personal = page.getByRole("region", { name: "Personal Heinzels" });
  await personal.getByRole("radio", { name: /^Original/ }).click();
  assert.equal(await personal.getByRole("button", { name: "Save name and look" }).count(), 0, "the page is read-only");
  await personal.getByRole("button", { name: "Edit in chat" }).click();
  await page.waitForURL((next) => next.searchParams.get("profile-chat") !== null, { timeout: 10000 });
  await editor.getByRole("button", { name: "Save name and look" }).waitFor({ timeout: 10000 });
  assert.equal(await editor.evaluate((node) => node.open), true, "Edit in chat opens the name and look");
  // Retire hides it and keeps it under Retired; Restore brings it back, on the phone as on the desktop.
  await pageTab.click();
  await personal.getByRole("radio", { name: /^Original/ }).click();
  await personal.getByRole("button", { name: "Retire", exact: true }).click();
  const retiredList = personal.locator("details").filter({ has: page.locator("summary", { hasText: "Retired (1)" }) });
  await retiredList.waitFor({ timeout: 10000 });
  assert.equal(
    await personal.getByRole("radio", { name: /^Original/ }).count(),
    0,
    "a retired Heinzel leaves the list",
  );
  const restore = retiredList.getByRole("button", { name: "Restore Original" });
  if (!(await restore.isVisible())) await retiredList.locator("summary").click();
  const restoreBox = await restore.boundingBox();
  assert.ok(restoreBox && restoreBox.x >= 0 && restoreBox.x + restoreBox.width <= 375, "Restore fits the phone");
  await restore.click();
  await personal.getByRole("radio", { name: /^Original/ }).waitFor({ timeout: 10000 });
  // Phone, Chat tab on /heinzel: creating a Heinzel from the chat switcher changes only the query, and still shows
  // the draft on the Page tab.
  const plain = new URL(url.href);
  plain.searchParams.delete("chat");
  await page.goto(plain.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  await chatTab.click();
  await page.getByRole("button", { name: /: switch chats$/ }).click();
  await page
    .getByRole("dialog", { name: "Your chats" })
    .getByRole("button", { name: "Create a personal Heinzel" })
    .click();
  await page.waitForURL((next) => next.searchParams.get("create") === "1", { timeout: 10000 });
  const draft = page.getByText("Your new Heinzel", { exact: true });
  await draft.waitFor({ timeout: 10000 });
  const draftInView = async () => {
    const at = await draft.boundingBox();
    return Boolean(at && at.y >= 0 && at.y + at.height <= 812);
  };
  const until = Date.now() + 10000;
  while (Date.now() < until && !((await pageTab.getAttribute("aria-selected")) === "true" && (await draftInView())))
    await page.waitForTimeout(200);
  assert.equal(await pageTab.getAttribute("aria-selected"), "true", "the draft opens the Page tab");
  assert.ok(await draftInView(), "the draft is in view");
  assert.equal(new URL(page.url()).pathname, "/heinzel");
  console.log(JSON.stringify({ completed: true }));
} catch (error) {
  process.stderr.write(`${String(error).replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
