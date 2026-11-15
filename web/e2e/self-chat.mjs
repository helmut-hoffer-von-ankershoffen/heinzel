// Chat as an extra place for notices (ADR 0160): with a made-up chat connector that can only message its owner, the
// Notifications settings offer "Also in chat" with that connector's name. Switching notices on saves it, and a test
// notice then also reaches the owner's messages, which the fixture keeps in the file HZ_SELF_CHAT_DEMO_INBOX names.
// Real Chromium against a real isolated `hz ui serve` with the fixture plugin installed; nothing is mocked.
// Usage: node e2e/self-chat.mjs URL SCREENSHOT_DIR (prints one JSON object)
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { ONE_CONNECTION } from "./fetch-once.mjs";

const [url, shots] = process.argv.slice(2);
if (!url || !shots) throw new Error("Usage: node e2e/self-chat.mjs URL SCREENSHOT_DIR");
const inbox = process.env.HZ_SELF_CHAT_DEMO_INBOX;
if (!inbox) throw new Error("HZ_SELF_CHAT_DEMO_INBOX names the fixture's inbox");
mkdirSync(shots, { recursive: true });
const target = new URL(url);
target.searchParams.set("settings", "notifications");
const token = new URL(url).hash.slice("#token=".length);

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "chrome" });
}
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await page.goto(target.href);
  await page.locator("#splash").waitFor({ state: "hidden" });
  const section = page.getByRole("region", { name: "Also in chat" });
  await section.getByText("Through Demo chat, in your own direct messages.", { exact: false }).waitFor();
  const notices = section.getByRole("switch", { name: "Notices also in chat" });
  const questions = section.getByRole("switch", { name: "Questions also in chat" });
  assert.equal(await notices.getAttribute("aria-checked"), "false");
  assert.equal(await questions.getAttribute("aria-checked"), "false");
  await section.scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(shots, "self-chat-1-offered.png") });

  await notices.click();
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll('[role="switch"][aria-checked="true"]')].some(
        (element) =>
          document.getElementById(element.getAttribute("aria-labelledby") ?? "")?.textContent ===
          "Notices also in chat",
      ),
    undefined,
    { timeout: 15_000 },
  );
  await section.screenshot({ path: join(shots, "self-chat-2-notices-on.png") });

  // A test notice without a native pop-up: it is recorded, and the switch sends it to the owner's messages too. With
  // no pop-up anywhere in this isolated profile the operation answers 503 (its exit code 1), the notice recorded.
  const tested = await context.request.post(new URL("/api/system/notify/test", url).href, {
    headers: { ...ONE_CONNECTION, Authorization: `Bearer ${token}` },
  });
  assert.ok([200, 503].includes(tested.status()), `the test notice is recorded: ${tested.status()}`);
  const deadline = Date.now() + 20_000;
  while (!(existsSync(inbox) && readFileSync(inbox, "utf-8").trim()) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const sent = existsSync(inbox) ? readFileSync(inbox, "utf-8").trim().split("\n") : [];
  assert.equal(sent.length, 1, "exactly one message reached the owner's chat");

  console.log(
    JSON.stringify({
      offered: true,
      notices: (await notices.getAttribute("aria-checked")) === "true",
      questions: (await questions.getAttribute("aria-checked")) === "true",
    }),
  );
} finally {
  await browser.close();
}
