import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const url = new URL(process.argv[2]);
const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
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
  const chatTab = page.getByRole("tab", { name: "Chat", exact: true });
  await message.or(chatTab).first().waitFor({ timeout: 30000 });
  if (!(await message.isVisible())) await chatTab.click();
  await message.fill("Make the hat blue, keep my other preferences");
  await page.getByRole("button", { name: "Queue message" }).click();
  const editor = page
    .locator("details")
    .filter({ has: page.locator("summary").filter({ hasText: /^Customize your Heinzel(?: · suggested look)?$/ }) });
  await editor.locator("summary").click();
  const suggestion = editor.getByRole("region", { name: "Suggested identity" });
  await suggestion.waitFor({ timeout: 30000 });
  assert.equal(await editor.getByLabel("Hat colour").inputValue(), "green");
  await suggestion.getByRole("button", { name: "Use suggested identity" }).click();
  assert.equal(await editor.getByLabel("Hat colour").inputValue(), "blue");
  const saved = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/heinzel/profile" && response.request().method() === "POST",
  );
  await editor.getByRole("button", { name: "Save name and look" }).click();
  assert.equal((await saved).status(), 200);
  await page.reload();
  await message.or(chatTab).first().waitFor({ timeout: 30000 });
  if (!(await message.isVisible())) await chatTab.click();
  await editor.locator("summary").click();
  assert.equal(await editor.getByLabel("Hat colour").inputValue(), "blue");
  const avatar = editor.getByRole("img", { name: "Appearance preview" });
  assert.ok((await avatar.locator('[fill="#1E88E5"]').count()) > 0);
  console.log(JSON.stringify({ completed: true }));
} catch (error) {
  process.stderr.write(`${String(error).replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
