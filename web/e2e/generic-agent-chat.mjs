import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const url = new URL(process.argv[2]);
const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
url.pathname = "/doctor";
const browser = await (process.argv[3] === "webkit" ? webkit : chromium).launch({ headless: true });
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
  const text = "Prepare a private draft and suggest an identity.";
  await page.getByLabel("Your message").fill(text);
  await page.getByRole("button", { name: "Create Heinzel and take this message" }).click();
  const message = page.getByLabel("Message your Heinzel");
  await message.waitFor();
  assert.equal(await message.inputValue(), text);
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("I prepared proposals for your review.").waitFor({ timeout: 30000 });
  const replyActivity = page.getByText("Tool activity for this reply · 4 events");
  await replyActivity.click();
  const replyEvents = replyActivity.locator("..");
  await replyEvents.getByText("agent:propose · requested").waitFor();
  await replyEvents.getByText("agent:propose · completed").waitFor();
  await replyEvents.getByText("profile:propose · requested").waitFor();
  await replyEvents.getByText("profile:propose · completed").waitFor();
  const overflow = await replyEvents.evaluate((node) => node.scrollWidth > node.clientWidth);
  assert.equal(overflow, false, "recorded activity must fit the phone viewport");
  const terms = page.getByRole("region", { name: "Proposed agent terms" });
  await terms.getByText("Review proposed terms").click();
  await terms.getByText("Prepare a private draft from my context.").waitFor();
  await terms.getByRole("button", { name: "Review these terms" }).click();
  await terms.getByRole("button", { name: "Confirm" }).click();
  await terms.getByText("These exact terms were accepted.").waitFor();
  const schedules = page.locator("details").filter({ has: page.getByText("Recurring agent turns", { exact: true }) });
  await schedules.locator("summary").first().click();
  await schedules.getByRole("button", { name: "Propose recurring turns" }).click();
  await schedules.getByRole("button", { name: "Review this schedule" }).click();
  await schedules.getByRole("button", { name: "Confirm", exact: true }).click();
  await schedules.getByText(/Approved; the daemon must be running/).waitFor();
  await schedules.getByRole("button", { name: "Pause future turns" }).click();
  await schedules.getByText(/Every 5 minutes · paused/).waitFor();
  await page.getByText(/suggested look/).click();
  await page.getByRole("button", { name: "Use suggested identity" }).click();
  await page.getByRole("button", { name: "Save name and look" }).click();
  await page.getByRole("heading", { name: "Mira" }).waitFor();
  const state = await page.evaluate(async () => {
    const session = new URLSearchParams(location.search).get("chat");
    const authorization = `Bearer ${sessionStorage.getItem("hz.token")}`;
    async function get(path) {
      const r = await fetch(path, { headers: { Authorization: authorization } });
      if (!r.ok) throw Error(`read ${r.status}`);
      return r.json();
    }
    return {
      session: await get(`/api/heinzel/session?session_id=${session}`),
      activity: await get(`/api/heinzel/tool-activity?session_id=${session}`),
    };
  });
  assert.equal(state.session.schedule, null);
  assert.equal(state.session.name, "Mira");
  assert.deepEqual(
    state.activity.items.map(({ tool, status }) => [tool, status]),
    [
      ["agent:propose", "requested"],
      ["agent:propose", "completed"],
      ["profile:propose", "requested"],
      ["profile:propose", "completed"],
    ],
  );
  await page.getByLabel("Message your Heinzel").fill("Continue after the interruption.");
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText(/reported its subscription usage limit/).waitFor();
  assert.equal(await page.getByRole("button", { name: "Retry the same message" }).isDisabled(), true);
  await page.reload();
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await page.getByText(/may still be running/).waitFor();
  await page.getByRole("checkbox", { name: /will not retry it or undo/ }).check();
  await page.getByRole("button", { name: "Set aside this turn" }).click();
  await page.getByText(/This turn was set aside/).waitFor();
  await page.reload();
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await page.getByText(/This turn was set aside/).waitFor();
  await page.getByRole("button", { name: "Continue conversation" }).click();
  await page.getByLabel("Message your Heinzel").fill("Please continue with the draft.");
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("I prepared proposals for your review.").nth(1).waitFor({ timeout: 30000 });
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .innerText()
    .catch(() => "");
  process.stderr.write(`${message}\n${visible}`.replaceAll(decodeURIComponent(token), "<redacted-session>") + "\n");
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
