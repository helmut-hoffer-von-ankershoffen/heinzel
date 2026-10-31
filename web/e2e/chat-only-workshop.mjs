import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";

const url = new URL(process.argv[2]);
const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
url.pathname = "/doctor";
const engine = process.argv[3];
assert.ok(["chromium", "webkit"].includes(engine));
const axe = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const browser = await { chromium, webkit }[engine].launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 } });
const page = await context.newPage();
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  if (!(await page.getByLabel("Your message").isVisible()))
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
  const sentence = "Help me keep a private summary of my notes current";
  await page.getByLabel("Your message").fill(sentence);
  await page.getByRole("button", { name: "Create Heinzel and take this message" }).click();
  const message = page.getByLabel("Message your Heinzel");
  await message.waitFor();
  assert.equal(await message.inputValue(), sentence);
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Which information and output should I use?").waitFor({ timeout: 30000 });
  await message.fill("Use the example information and prepare a private summary");
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Draft ready for review").waitFor({ timeout: 30000 });
  const terms = page.getByRole("region", { name: "Proposed agent terms" });
  await terms.getByText("Review proposed terms").click();
  await terms.getByText(/Use the example information and prepare a private summary/).waitFor();
  await page.evaluate(axe);
  const violations = await terms.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    true,
    "phone must not overflow",
  );
  const session = new URL(page.url()).searchParams.get("chat");
  assert.ok(session);
  await page.reload();
  const composerAfterReload = page.getByLabel("Message your Heinzel");
  const chatTabAfterReload = page.getByRole("tab", { name: "Chat", exact: true });
  await composerAfterReload.or(chatTabAfterReload).first().waitFor({ timeout: 30000 });
  if (!(await composerAfterReload.isVisible())) await chatTabAfterReload.click();
  await page.getByText("Draft ready for review").waitFor({ timeout: 30000 });
  assert.equal(new URL(page.url()).searchParams.get("chat"), session);
  console.log(JSON.stringify({ session, completed: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  process.stderr.write(`${message.replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
