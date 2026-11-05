import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { openChatJourney, revealChat } from "./chat-ready.mjs";
import { disclosureDiagnostics, watchDisclosure } from "./disclosure-diagnostics.mjs";

const { browser, context, page, token, url } = await openChatJourney();
const latePage = process.argv[4] === "late-page";
let replyDisclosureTrace = [];
if (latePage) {
  // Commit page DOM growth between press/release, while its resize
  // notification arrives after release. Keep actual browser layout, response data and physical pointer events.
  await page.addInitScript(() => {
    const NativeObserver = window.ResizeObserver;
    const pending = new Map();
    window.__hzHoldPageResize = false;
    window.__hzReleasePageResize = () => {
      window.__hzHoldPageResize = false;
      for (const [observer, deliver] of pending) {
        pending.delete(observer);
        deliver();
      }
    };
    window.ResizeObserver = class extends NativeObserver {
      constructor(callback) {
        super((entries, observer) => {
          if (window.__hzHoldPageResize && entries.some(({ target }) => target.querySelector("#content")))
            pending.set(observer, () => callback(entries, observer));
          else callback(entries, observer);
        });
      }
      disconnect() {
        pending.delete(this);
        super.disconnect();
      }
    };
  });
}
const trace = [];
const watched = new Set([
  "/api/heinzel/chat",
  "/api/heinzel/answer-chat",
  "/api/heinzel/chat-turn-state",
  "/api/heinzel/abandon-chat-turn",
  "/api/heinzel/session",
]);
page.on("response", (response) => {
  const request = response.request();
  const path = new URL(response.url()).pathname;
  if (watched.has(path)) trace.push(`${request.method()} ${path} ${response.status()}`);
});
page.on("requestfailed", (request) => {
  const path = new URL(request.url()).pathname;
  if (watched.has(path)) trace.push(`${request.method()} ${path} failed`);
});
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
  await page.getByRole("button", { name: "Create a Heinzel and take this message" }).click();
  const message = page.getByLabel("Message your Heinzel");
  await message.waitFor();
  assert.equal(await message.inputValue(), text);
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("I prepared proposals for your review.").waitFor({ timeout: 30000 });
  const replyActivity = page.getByText("Tool activity for this reply · 4 events");
  await watchDisclosure(replyActivity, "reply-activity");
  if (process.argv[6] === "smooth") {
    await replyActivity.hover();
    const box = await replyActivity.boundingBox();
    assert.ok(box);
    const scrollOrigin = await replyActivity.evaluate((node) => {
      const scroller = node.closest(".workspace-scroll");
      const before = scroller.scrollTop;
      scroller.scrollTo({ top: before - 120, behavior: "smooth" });
      return before;
    });
    assert.ok(scrollOrigin > 120, "the controlled press must have real workspace scroll distance");
    await page.mouse.down();
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.mouse.up();
  } else await replyActivity.click();
  const replyEvents = replyActivity.locator("..");
  await replyEvents.getByText("agent:propose · requested").waitFor();
  await replyEvents.getByText("agent:propose · completed").waitFor();
  await replyEvents.getByText("profile:propose · requested").waitFor();
  await replyEvents.getByText("profile:propose · completed").waitFor();
  replyDisclosureTrace = await disclosureDiagnostics(page);
  const overflow = await replyEvents.evaluate((node) => node.scrollWidth > node.clientWidth);
  assert.equal(overflow, false, "recorded activity must fit the phone viewport");
  const terms = page.getByRole("region", { name: "Proposed agent terms" });
  await watchDisclosure(terms.getByText("Review proposed terms"), "proposed-terms");
  await terms.getByText("Review proposed terms").click();
  await terms.getByText("Prepare a private draft from my context.").waitFor();
  await terms.getByRole("button", { name: "Review these terms" }).click();
  await terms.getByRole("button", { name: "Confirm" }).click();
  await terms.getByText("These exact terms were accepted.").waitFor();
  const schedules = page.locator("details").filter({ has: page.getByText("Recurring agent turns", { exact: true }) });
  await watchDisclosure(schedules.locator("summary").first(), "recurring-turns");
  await schedules.locator("summary").first().click();
  await schedules.getByRole("button", { name: "Propose recurring turns" }).click();
  await schedules.getByRole("button", { name: "Review this schedule" }).click();
  await schedules.getByRole("button", { name: "Confirm", exact: true }).click();
  await schedules.getByText(/Approved; the daemon must be running/).waitFor();
  await schedules.getByRole("button", { name: "Pause future turns" }).click();
  await schedules.getByText(/Every 5 minutes · paused/).waitFor();
  await watchDisclosure(page.getByText(/suggested look/), "suggested-look");
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
  await revealChat(page);
  await page.getByText(/may still be running/).waitFor();
  await page.getByRole("checkbox", { name: /will not retry it or undo/ }).check();
  await page.getByRole("button", { name: "Set aside this turn" }).click();
  await page.getByText(/This turn was set aside/).waitFor();
  const ready = Promise.withResolvers();
  const release = Promise.withResolvers();
  const inserted = Promise.withResolvers();
  if (latePage) {
    await page.route("**/api/system/doctor", async (route) => {
      const response = await route.fetch();
      ready.resolve();
      await release.promise;
      await route.fulfill({ response });
      inserted.resolve();
    });
  }
  await page.reload();
  await revealChat(page);
  await page.getByText(/This turn was set aside/).waitFor();
  const continueConversation = page.getByRole("button", { name: "Continue conversation" });
  await watchDisclosure(continueConversation, "continue-conversation-after-reload");
  if (latePage) {
    await ready.promise;
    await continueConversation.hover();
    const box = await continueConversation.boundingBox();
    assert.ok(box);
    const pointer = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    const priorHeight = await page.locator("#content").evaluate((node) => node.getBoundingClientRect().height);
    await page.evaluate(() => {
      window.__hzHoldPageResize = true;
    });
    try {
      await page.mouse.down();
      release.resolve();
      await inserted.promise;
      await page.waitForFunction(
        (height) => document.getElementById("content").getBoundingClientRect().height > height,
        priorHeight,
      );
      const stillUnderPointer = await continueConversation.evaluate(
        (node, { x, y }) => node.contains(document.elementFromPoint(x, y)),
        pointer,
      );
      await page.mouse.up();
      assert.equal(stillUnderPointer, true, "late page loading keeps the pressed Continue control under the pointer");
    } finally {
      await page.evaluate(() => window.__hzReleasePageResize());
    }
  } else await continueConversation.click();
  const composer = page.getByLabel("Message your Heinzel");
  await composer.fill("Please continue with the draft.");
  const submit = page.getByRole("button", { name: "Queue message" });
  trace.push(`before-submit enabled=${await submit.isEnabled()} input=${(await composer.inputValue()).length}`);
  assert.equal(await composer.inputValue(), "Please continue with the draft.");
  const accepted = page.waitForResponse(
    (response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/heinzel/chat",
  );
  const answered = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && new URL(response.url()).pathname === "/api/heinzel/answer-chat",
  );
  const rendered = page.getByText("I prepared proposals for your review.").nth(1).waitFor({ timeout: 30000 });
  await submit.click();
  const [acceptResponse, answerResponse] = await Promise.all([accepted, answered, rendered]);
  assert.equal(acceptResponse.status(), 200, "the new message must be accepted");
  assert.equal(answerResponse.status(), 200, "the new message must receive an answer");
  const disclosures = await disclosureDiagnostics(page);
  if (process.argv[5])
    writeFileSync(
      join(process.argv[5], "generic-disclosures.json"),
      JSON.stringify([...replyDisclosureTrace, ...disclosures], null, 2),
    );
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .innerText()
    .catch(() => "");
  const disclosures = await disclosureDiagnostics(page);
  if (process.argv[5])
    writeFileSync(
      join(process.argv[5], "generic-disclosures.json"),
      JSON.stringify([...replyDisclosureTrace, ...disclosures], null, 2),
    );
  const diagnostic = `${message}\n${visible}\nHTTP trace: ${trace.join(" | ")}\nDisclosure trace: ${JSON.stringify(disclosures)}`;
  process.stderr.write(`${diagnostic.replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
